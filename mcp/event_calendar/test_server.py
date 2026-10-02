"""予定の登録と取り込み・行く会の案・成果の記録・費用対効果・.ics の書き出しのテスト。

実行: python -m pytest mcp/event_calendar/test_server.py
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.pop("EVENT_CALENDAR_PATH", None)
sys.path.insert(0, str(Path(__file__).parent))

import ics  # noqa: E402
import server  # noqa: E402

HERE = Path(__file__).parent


@pytest.fixture(autouse=True)
def sample_calendar(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "cal", server.Calendar(None))
    monkeypatch.setenv("EVENT_CALENDAR_EXPORT_DIR", str(tmp_path))


def by_id(rows):
    return {r["id"]: r for r in rows}


# --- シリーズ名 -----------------------------------------------------------------------

@pytest.mark.parametrize("title, series", [
    ("朝活ビジネス交流会 vol.12", "朝活ビジネス交流会"),
    ("異業種交流ランチ会 10月", "異業種交流ランチ会"),
    ("女性起業家ミートアップ #6", "女性起業家ミートアップ"),
    ("創業カフェ交流会 第3回", "創業カフェ交流会"),
    ("【満席】士業・経営者懇親会 秋", "士業・経営者懇親会"),
])
def test_series_of(title, series):
    assert server.series_key(server.series_of(title)) == server.series_key(series)


# --- 登録と取り込み ---------------------------------------------------------------------

def test_add_events_dedupes_by_date_and_series_and_fills_blanks():
    res = server.add_events([
        {"title": "AI活用勉強会&交流会 第2回", "date": "2026-10-28", "organizer": "上書きしない", "url": "https://example.com/ai"},
        {"title": "創業相談会", "date": "2026-11-02", "start": "9:30", "fee": 500},
        {"title": "日付なし"},
    ])
    assert [e["id"] for e in res["updated"]] == ["E013"]               # 全角＆と半角&の揺れも同じシリーズ
    e13 = server.cal.get("E013")
    assert e13["url"] == "https://example.com/ai" and e13["organizer"] == "サンプルAI研究会"
    assert res["added"][0]["id"] == "E016" and res["added"][0]["start"] == "09:30"
    assert res["added"][0]["status"] == "候補" and "1件目" not in res["errors"][0]


def test_import_ics_timezones_folding_and_duplicates():
    res = server.import_ics(str(HERE / "sample" / "connpass_export.ics"))
    added = {e["title"]: e for e in res["added"]}
    cafe = added["創業カフェ交流会 第3回"]
    assert (cafe["date"], cafe["start"], cafe["end"]) == ("2026-11-05", "19:00", "21:00")
    fair = added["サンプル市 産業フェア（出展者交流会あり）"]                 # 折り返した行をつなぐ・終日
    assert fair["date"] == "2026-11-14" and not fair["start"]
    assert [e["id"] for e in res["updated"]] == ["E011"]               # UTC の 09:30Z＝日本時間 18:30 の既存の会
    assert server.cal.get("E011")["start"] == "18:30"
    assert "創業予定の方, " not in server.cal.get(cafe["id"])["audience"]   # 「\,」をカンマに戻す
    assert "創業1年以内の方, 創業予定の方" in server.cal.get(cafe["id"])["audience"]
    again = server.import_ics(str(HERE / "sample" / "connpass_export.ics"))
    assert not again["added"] and len(again["duplicates"]) == 3


# --- 行く会の案 -------------------------------------------------------------------------

def test_recommend_ranks_by_history_and_skips_clashes_and_poor_series():
    r = server.recommend()
    c = by_id(r["candidates"])
    assert c["E015"]["expected_meetings"] == 1.5                       # 朝活：(面談5＋1)/(3回＋1)
    assert c["E013"]["basis"].startswith("未経験")
    assert c["E010"]["decision"] == "見送り案（見込みが低い）"           # ランチ会：2回で面談0
    assert c["E011"]["decision"] == "行く案"
    assert c["E012"]["decision"] == "見送り案（時間が重なる）"           # 同じ夜のミートアップと重なる
    assert r["plan"] == ["E015", "E011", "E014", "E013"]
    assert all(server.cal.get(i)["status"] == "候補" for i in r["plan"])   # 申し込みはしない


def test_recommend_respects_weekly_limit():
    r = server.recommend(max_per_week=1)
    c = by_id(r["candidates"])
    assert c["E014"]["decision"] == "行く案" and c["E013"]["decision"].startswith("見送り案（その週は1件")


def test_clash_needs_travel_time_between_different_places():
    a = {"date": "2026-10-01", "start": "18:00", "end": "20:00", "place": "A"}
    assert server._clash(a, {"date": "2026-10-01", "start": "20:15", "end": "21:00", "place": "B"})
    assert not server._clash(a, {"date": "2026-10-01", "start": "20:15", "end": "21:00", "place": "A"})
    assert not server._clash(a, {"date": "2026-10-01", "start": "", "place": "B"})


def test_upcoming_lists_clashes_and_booked_weeks():
    up = server.upcoming()
    rows = by_id(up["events"])
    assert rows["E011"]["clashes_with"] == ["E012"] and "E001" not in rows
    assert up["booked_per_week"] == {"2026-W41": 1}


# --- 成果の記録 -------------------------------------------------------------------------

def test_pending_results_and_record_result():
    assert [e["id"] for e in server.pending_results()] == ["E008"]
    with pytest.raises(ValueError, match="開催前"):
        server.record_result("E009", cards=3)
    with pytest.raises(ValueError, match="面談の数"):
        server.record_result("E008", cards=2, meetings=3)
    res = server.record_result("E008", cards=6, meetings=1, memo="商工会の方が多い")
    assert res["status"] == "参加済" and server.pending_results() == []
    server.record_result("E008", deals=1, revenue=80000, memo="酒店さんから受注")
    r = server.cal.get("E008")["result"]
    assert (r["cards"], r["meetings"], r["deals"]) == (6, 1, 1) and r["memo"] == "商工会の方が多い / 酒店さんから受注"


def test_import_sheet_results_counts_only_numbers():
    res = server.import_sheet_results(str(HERE / "sample" / "営業シート_交流会タブ.csv"))
    assert [(r["id"], r["cards"], r["meetings"], r["deals"]) for r in res["recorded"]] == [("E008", 6, 2, 0)]
    assert res["unmatched_rows"] == {"知人の紹介": 1}
    saved = json.dumps(server.cal.data, ensure_ascii=False)
    assert "サンプル 一郎" not in saved                                  # 名前は保存しない
    assert by_id(server.recommend()["candidates"])["E014"]["expected_meetings"] == 1.5   # 次の回の見込みに反映


# --- 費用対効果 -------------------------------------------------------------------------

def test_series_report_verdicts_and_costs():
    rep = {r["series"]: r for r in server.series_report()["series"]}
    morning = rep["朝活ビジネス交流会"]
    assert (morning["attended"], morning["cost"], morning["meetings"], morning["cost_per_meeting"]) == (3, 4500, 5, 900)
    assert morning["verdict"] == "続ける"
    assert rep["異業種交流ランチ会"]["verdict"].startswith("見直し")
    assert rep["士業・経営者懇親会"]["verdict"] == "様子見" and rep["士業・経営者懇親会"]["cost_per_meeting"] == 6000
    assert server.series_report()["not_recorded"] == 1
    sept = server.series_report(date_from="2026-09-01", date_to="2026-09-30")
    assert sept["total"]["attended"] == 3 and len(server.cal.data["events"]) == 15   # 絞り込んでも元に戻る


# --- 書き出しと保存 ---------------------------------------------------------------------

def test_export_ics_round_trip():
    server.set_status(["E011", "E013"], "申込済")
    res = server.export_ics()
    assert [e["id"] for e in res["events"]] == ["E009", "E011", "E013"]
    raw = Path(res["path"]).read_bytes()
    text = raw.decode("utf-8")
    assert raw.count(b"\r\n") == raw.count(b"\n") and "DTSTART:20261007T220000Z" in text          # 10/8 07:00 日本時間
    back = ics.parse(text)
    assert [(str(e["date"]), e["start"], e["end"]) for e in back] == [
        ("2026-10-08", "07:00", "08:30"), ("2026-10-21", "18:30", "20:30"), ("2026-10-28", "19:00", "21:00")]
    assert "参加費: 2500円" in back[2]["description"]
    assert max(len(line) for line in raw.split(b"\r\n")) <= 75          # 長い行は折り返す


def test_set_status_validation():
    with pytest.raises(ValueError, match="status"):
        server.set_status(["E010"], "行く")
    with pytest.raises(ValueError, match="E999"):
        server.set_status(["E999"], "見送り")


def test_calendar_persists(tmp_path, monkeypatch):
    path = tmp_path / "cal.json"
    monkeypatch.setattr(server, "cal", server.Calendar(path))
    server.add_events([{"title": "テスト交流会 第1回", "date": "2020-01-10", "fee": 1000}])
    server.record_result("E001", cards=4, meetings=1)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["events"][0]["result"]["cards"] == 4 and saved["next_id"] == 2
    assert server.Calendar(path).get("E001")["status"] == "参加済"
