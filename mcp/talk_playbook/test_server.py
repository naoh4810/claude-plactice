"""業種の判定・手引き・交流会の準備・現場メモのテスト。

実行: python -m pytest mcp/talk_playbook/test_server.py
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.pop("TALK_NOTES_PATH", None)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402

# 「実演できる」デモと、その実装があるディレクトリ
READY_DIRS = {"A1": "sales_sheet", "B1": "form_report", "B2": "shop_ops", "B3": "knowledge_search",
              "B5": "sns_planner", "B6": "shortlink", "B7": "line_recall",
              "B8": "tax_office", "B9": "photo_estimate",
              "B11": "household_review"}


@pytest.fixture(autouse=True)
def fresh_notes(monkeypatch):
    monkeypatch.setattr(server, "notes", server.Notes(None))


# --- 手引きのデータの整合性 --------------------------------------------------------

def test_ready_demos_really_exist():
    ready = {k for k, (_, status) in server.DEMOS.items() if status == "ready"}
    assert ready == set(READY_DIRS)
    for demo, directory in READY_DIRS.items():
        assert (HERE.parent / directory / "server.py").exists(), f"{demo} の実装がありません"


def test_every_playbook_is_complete_and_refers_to_known_demos():
    for pid, p in server.PLAYBOOKS.items():
        assert p["keywords"] and p["pains"] and p["ideas"] and p["opener"] and p["cautions"], pid
        assert len(p["questions"]) == 3, pid
        assert all(i.get("demo") in server.DEMOS for i in p["ideas"] if i.get("demo")), pid
    assert server.FALLBACK in server.PLAYBOOKS


# --- 業種の判定 -------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("まちのわ司法書士事務所", "judicial_scrivener"),
    ("税理士・AFP", "tax_accountant"),               # FP より税理士を優先
    ("サンプル歯科医院 院長", "dental_clinic"),
    ("整体院の代表", "bodywork"),
    ("外壁洗浄専門店", "construction"),
    ("障害者支援の事業所", "welfare"),
    ("ECサイト運営（Shopify）", "ecommerce"),
    ("ふるさと納税の事業会社 営業部", "tourism"),
    ("人材会社の支店長", "staffing"),
    ("WEBデザイナ", "creator"),
    ("Lステップ代理店", "marketing_partner"),
    ("交流会主催", "community"),
    ("ＥＣショップ", "ecommerce"),                   # 全角も判定できる
])
def test_match_industry(text, expected):
    assert server.match_industry(text)["best"] == expected


def test_unknown_falls_back_to_general():
    res = server.match_industry("株式会社サンプル")
    assert res["best"] == "general" and res["candidates"] == [] and "聞いて" in res["note"]


# --- 手引きと交流会の準備 -------------------------------------------------------------

def test_playbook_marks_ideas_that_cannot_be_shown_yet():
    pb = server.get_playbook("tutor")
    statuses = {i["demo"]["id"]: i["demo"]["status"] for i in pb["ideas"]}
    assert statuses["B4"].startswith("構想")
    assert statuses["B1"] == "実演できる"
    assert pb["demo_to_show"]["id"] == "B1"          # 構想の B4 ではなく、見せられる B1


def test_fp_now_shows_the_household_demo():
    assert server.get_playbook("FP")["demo_to_show"]["id"] == "B11"


def test_construction_now_shows_the_estimate_demo():
    assert server.get_playbook("外壁塗装")["demo_to_show"]["id"] == "B9"


def test_tax_accountant_now_shows_the_deadline_demo():
    assert server.get_playbook("tax_accountant")["demo_to_show"]["id"] == "B8"


def test_clinics_and_salons_now_show_the_recall_demo():
    for industry in ("dental_clinic", "bodywork", "beauty_salon"):
        assert server.get_playbook(industry)["demo_to_show"]["id"] in ("B7", "B5")
    assert server.get_playbook("dental_clinic")["demo_to_show"]["id"] == "B7"
    assert server.get_playbook("bodywork")["demo_to_show"]["id"] == "B7"


def test_get_playbook_accepts_free_text():
    assert server.get_playbook("訪問看護ステーション")["id"] == "bodywork"


def test_prep_for_event_groups_by_industry():
    res = server.prep_for_event(["税理士事務所", "会計事務所", "整体院", "株式会社サンプル"], event="サンプル交流会")
    assert [a["industry"] for a in res["attendees"]] == [
        "税理士・会計事務所", "税理士・会計事務所", "整体・鍼灸・治療院", "個人事業主・小さな会社（業種が分からないとき）"]
    assert len(res["cautions_by_industry"]) == 3
    assert "士業の期限・書類MCP（顧問先ごとの期限の自動計算・書類の回収状況・催促の下書き）" in res["demos_to_prepare"]


# --- 現場メモ ---------------------------------------------------------------------

def test_insight_shows_up_in_next_playbook_and_event_prep():
    server.log_insight("税理士事務所", "守秘義務の話を先にすると聞いてもらえた", "面談につながった")
    pb = server.get_playbook("tax_accountant")
    assert pb["field_notes"][0]["insight"] == "守秘義務の話を先にすると聞いてもらえた"
    prep = server.prep_for_event(["税理士事務所"])
    assert prep["field_notes_by_industry"]["税理士・会計事務所"][0]["outcome"] == "面談につながった"
    with pytest.raises(ValueError, match="空"):
        server.log_insight("tax_accountant", "  ")


def test_insights_persist_to_file(tmp_path, monkeypatch):
    path = tmp_path / "notes.json"
    monkeypatch.setattr(server, "notes", server.Notes(path))
    assert server.log_insight("community", "運営を楽にする話だと聞いてもらえる")["persisted"]
    assert json.loads(path.read_text(encoding="utf-8"))["community"][0]["insight"] == "運営を楽にする話だと聞いてもらえる"
    assert server.Notes(path).get("community")[0]["insight"] == "運営を楽にする話だと聞いてもらえる"
