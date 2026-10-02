"""予約台帳の読み込み・分類・集計・配信の効果測定のテスト。

実行: python -m pytest mcp/line_recall/test_server.py
"""

import csv
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

os.environ.pop("RESERVATIONS_CSV", None)
os.environ.pop("LINE_STATE_PATH", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
from store import Ledger, map_columns, parse_csv  # noqa: E402

TODAY = date.today()


def write_csv(path: Path, header: list[str], rows: list[list[str]], encoding: str = "utf-8-sig") -> Path:
    with path.open("w", encoding=encoding, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


@pytest.fixture
def small_ledger(tmp_path, monkeypatch):
    """今日を基準にした小さな台帳。分類がはっきり分かれるようにしてある。"""
    rows = [
        # 2週間おきに通っていたが 40日空いた → 途切れ
        *[[ago(d), "A", "定期さん", "整体60分", "来院"] for d in (96, 82, 68, 54, 40)],
        # 2か月おきの人が 40日空いた → いつものペースなので通院中
        *[[ago(d), "B", "ゆっくりさん", "整体30分", "来院"] for d in (160, 100, 40)],
        # 初回から 20日 → 2回目未来院
        [ago(20), "C", "新規さん", "骨盤調整", "来院"],
        # 初回から 5日 → 新規（2週間以内）
        [ago(5), "D", "新しいさん", "整体60分", "来院"],
        # 200日来ていない → 休眠
        *[[ago(d), "E", "休眠さん", "整体60分", "来院"] for d in (230, 200)],
        # 途切れのペースだが次の予約がある → 予約あり
        *[[ago(d), "F", "予約さん", "整体60分", "来院"] for d in (70, 56, 42)],
        [(TODAY + timedelta(days=3)).isoformat(), "F", "予約さん", "整体60分", "予約"],
        # キャンセルと無断キャンセル
        [ago(10), "A", "定期さん", "整体60分", "キャンセル"],
        [ago(12), "B", "ゆっくりさん", "整体30分", "無断キャンセル"],
    ]
    path = write_csv(tmp_path / "台帳.csv", ["来院日", "会員番号", "お名前", "コース", "状態"], rows)
    led = Ledger(path, tmp_path / "state.json")
    monkeypatch.setattr(server, "ledger", led)
    monkeypatch.setenv("LINE_EXPORT_DIR", str(tmp_path / "out"))
    return led


# --- CSV の読み込み -----------------------------------------------------------------

def test_column_aliases_and_required_columns():
    assert map_columns(["予約日時", "患者番号", "患者名", "施術内容", "予約状況"]) == {
        "date": "予約日時", "customer_id": "患者番号", "name": "患者名", "menu": "施術内容", "status": "予約状況"}
    with pytest.raises(ValueError, match="日付"):
        map_columns(["名前", "メニュー"])


def test_parse_csv_statuses_and_bad_rows():
    text = "日付,名前,状態\n2026/09/01,青木,来店\n2026/09/02 10:00,井上,無断キャンセル\n2026-09-03,上田,予約キャンセル\n九月,江藤,来院\n2026/09/05,,来院\n2026/09/06,加藤,謎\n"
    rows, problems = parse_csv(text)
    assert [(r.customer_id, r.status) for r in rows] == [("青木", "visited"), ("井上", "no_show"), ("上田", "cancelled"), ("加藤", "visited")]
    assert len(problems) == 3 and problems[0].startswith("5行目") and "謎" in problems[2]   # 1行目はヘッダー


def test_shift_jis_csv(tmp_path):
    path = write_csv(tmp_path / "sjis.csv", ["日付", "顧客ID", "氏名"], [[ago(30), "X1", "斉藤"]], encoding="cp932")
    assert Ledger(path, None).reservations[0].name == "斉藤"


# --- 分類と案内候補 -------------------------------------------------------------------

def test_segments_are_based_on_each_persons_rhythm(small_ledger):
    seg = {c.id: server.classify(c, TODAY) for c in server._customers().values()}
    assert seg == {"A": "途切れ", "B": "通院中", "C": "2回目未来院", "D": "新規（2週間以内）", "E": "休眠", "F": "予約あり"}


def test_recall_list_and_recently_contacted(small_ledger):
    res = server.recall_list("途切れ")
    assert [c["customer_id"] for c in res["customers"]] == ["A"]
    assert res["customers"][0]["usual_interval_days"] == 14 and res["customers"][0]["cancels"] == 1
    assert any("効果" in rule for rule in res["message_rules"])
    server.record_message("途切れ", ["A"], note="テスト")
    assert server.recall_list("途切れ")["customers"][0]["recently_contacted"]
    with pytest.raises(ValueError, match="segment"):
        server.recall_list("通院中")


def test_customer_history_lookup(small_ledger):
    h = server.customer_history("ゆっくり")
    assert h["customer_id"] == "B" and h["no_shows"] == 1 and len(h["history"]) == 4
    with pytest.raises(ValueError, match="いません"):
        server.customer_history("だれでもない")


# --- 集計 -----------------------------------------------------------------------------

def test_visit_stats_second_visit_rate(small_ledger):
    stats = server.visit_stats(date_from=ago(240), date_to=TODAY.isoformat())
    # 期間内の新規: A(96日前) B(160日前) C(20日前) D(5日前) E(230日前) F(70日前)
    # 60日たって判定できるのは A・B・E・F。60日以内に2回目: A(14日後) E(30日後) F(14日後)。B は60日後で含む
    assert stats["new_customers"] == 6 and stats["second_visit_judgeable"] == 4
    assert stats["second_visit_rate"] == "100%"
    assert stats["cancel_rate"] == "5.9%" and stats["no_show_rate"] == "5.9%"   # 今日までの17件中それぞれ1件（未来の予約は含めない）


# --- 配信の記録と効果 ------------------------------------------------------------------

def test_message_effect_compares_with_people_not_messaged(tmp_path, monkeypatch):
    sent = TODAY - timedelta(days=40)
    rows = []
    for cid in ("P1", "P2", "P3", "P4"):                    # 送った日の時点で全員 2回目未来院
        rows.append([(sent - timedelta(days=30)).isoformat(), cid, cid, "", "来院"])
    rows.append([(sent + timedelta(days=10)).isoformat(), "P1", "P1", "", "来院"])   # 案内した P1 が戻った
    rows.append([(sent + timedelta(days=45)).isoformat(), "P4", "P4", "", "来院"])   # 案内していない P4 は窓の外
    path = write_csv(tmp_path / "t.csv", ["日付", "顧客ID", "名前", "メニュー", "状態"], rows)
    led = Ledger(path, None)
    led.messages = [{"date": sent.isoformat(), "segment": "2回目未来院", "channel": "LINE", "customer_ids": ["P1", "P2"]}]
    monkeypatch.setattr(server, "ledger", led)
    effect = server.message_effect(days=30)[0]
    assert (effect["sent"], effect["returned"], effect["return_rate"]) == (2, 1, "50%")
    assert effect["baseline"] == {"not_sent": 2, "returned": 0, "return_rate": "0%"}
    assert effect["judging"] is False
    assert server.message_effect(days=60)[0]["judging"] is True


def test_record_message_persists_and_validates(small_ledger, tmp_path):
    with pytest.raises(ValueError, match="台帳に無い"):
        server.record_message("途切れ", ["ZZZ"])
    res = server.record_message("2回目未来院", ["C", "C"], note="初回のお礼")
    assert res["count"] == 1 and res["persisted"]
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert saved["messages"][0]["customer_ids"] == ["C"]
    assert Ledger(tmp_path / "台帳.csv", tmp_path / "state.json").messages[0]["note"] == "初回のお礼"


def test_export_list_skips_recently_contacted(small_ledger):
    server.record_message("途切れ", ["A"])
    res = server.export_list("途切れ")
    assert res["rows"] == 0 and res["excluded_recently_contacted"] == 1
    res = server.export_list("2回目未来院")
    lines = Path(res["path"]).read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("顧客ID,名前,分類") and lines[1].startswith("C,新規さん,2回目未来院")


# --- サンプル -------------------------------------------------------------------------

def test_sample_story(monkeypatch):
    monkeypatch.setattr(server, "ledger", Ledger(None, None))
    ov = server.overview()
    assert ov["today"] == "2026-10-01" and ov["recall_targets"] == 30
    effect = server.message_effect()[0]
    assert effect["return_rate"] == "33%" and effect["baseline"]["return_rate"] == "0%"
