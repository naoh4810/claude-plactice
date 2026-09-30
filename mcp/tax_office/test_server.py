"""期限の計算ルールと、サンプル事務所でのツールの動きのテスト。

実行: python -m pytest mcp/tax_office/test_server.py
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

os.environ.pop("OFFICE_DATA_PATH", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
from rules import deadlines_for, next_business_day  # noqa: E402
from store import Office  # noqa: E402


@pytest.fixture(autouse=True)
def sample_office(monkeypatch):
    monkeypatch.setattr(server, "office", Office(None))


def dues(client, year):
    return {(d.title, d.period): d.due for d in deadlines_for(client, range(year, year + 1))}


# --- 期限の計算 -------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    (date(2026, 5, 31), date(2026, 6, 1)),     # 日曜 → 月曜
    (date(2026, 1, 10), date(2026, 1, 13)),    # 土曜 → 日曜 → 成人の日 → 火曜
    (date(2026, 10, 10), date(2026, 10, 13)),  # 土曜 → 日曜 → スポーツの日
    (date(2026, 9, 21), date(2026, 9, 24)),    # 敬老の日 → 国民の休日 → 秋分の日
    (date(2026, 12, 30), date(2027, 1, 4)),    # 年末年始（12/29〜1/3）
    (date(2027, 3, 15), date(2027, 3, 15)),    # 平日はそのまま
])
def test_next_business_day(raw, expected):
    assert next_business_day(raw) == expected


def test_corporation_with_extension_separates_filing_and_payment():
    c = {"id": "x", "type": "法人", "fiscal_year_end_month": 3, "consumption_tax": True, "filing_extension": True}
    d = dues(c, 2026)
    assert d[("法人税・地方法人税の確定申告", "2026-03期")] == date(2026, 6, 30)       # 延長で3か月
    assert d[("法人税（見込み額）の納付", "2026-03期")] == date(2026, 6, 1)           # 納付は2か月のまま（5/31は日曜）
    assert d[("消費税の確定申告", "2026-03期")] == date(2026, 6, 1)                  # 消費税の延長は別の届出
    c["consumption_tax_extension"] = True
    assert dues(c, 2026)[("消費税の確定申告", "2026-03期")] == date(2026, 6, 30)


def test_corporation_december_year_end_crosses_year():
    c = {"id": "x", "type": "法人", "fiscal_year_end_month": 12}
    assert dues(c, 2026)[("法人税・地方法人税の確定申告", "2026-12期")] == date(2027, 3, 1)   # 2/28 は日曜


def test_individual_and_withholding_and_year_end():
    c = {"id": "x", "type": "個人", "consumption_tax": True, "withholding": "納期の特例", "employees": True,
         "depreciable_assets": True}
    d = dues(c, 2026)
    assert d[("所得税の確定申告", "2026年分")] == date(2027, 3, 15)
    assert d[("消費税の確定申告（個人）", "2026年分")] == date(2027, 3, 31)
    assert d[("源泉所得税の納付（納期の特例・1〜6月分）", "2026年1〜6月分")] == date(2026, 7, 10)
    assert d[("源泉所得税の納付（納期の特例・7〜12月分）", "2026年7〜12月分")] == date(2027, 1, 20)
    assert d[("法定調書合計表・給与支払報告書の提出", "2026年分")] == date(2027, 2, 1)       # 1/31 は日曜
    assert d[("償却資産申告", "2027年度")] == date(2027, 2, 1)


def test_monthly_withholding_december_goes_to_next_january():
    c = {"id": "x", "type": "法人", "fiscal_year_end_month": 3, "withholding": "毎月"}
    assert dues(c, 2026)[("源泉所得税の納付", "2026-12分")] == date(2027, 1, 12)          # 1/10 日曜、1/11 成人の日


# --- ツール -----------------------------------------------------------------------

def test_overview_and_upcoming_in_sample():
    ov = server.office_overview()
    assert ov["today"] == "2026-09-30" and ov["overdue_not_done"] == 0
    rows = server.upcoming_deadlines(days=14)
    assert [(r["client"], r["title"]) for r in rows][:2] == [
        ("株式会社サンプル商事", "法人税・地方法人税の確定申告"), ("株式会社サンプル商事", "消費税の確定申告")]
    assert rows[0]["days_left"] == 0 and rows[0]["documents"] == "4/6"


def test_tracking_since_hides_older_overdue_but_shows_new_ones():
    # 8/10 の源泉（使い始める前）は出さず、使い始めた後に過ぎた未完了の期限は出す
    server.office.data["as_of"] = "2026-10-14"
    rows = server.upcoming_deadlines(days=1)
    overdue = [(r["due"], r["title"]) for r in rows if r["days_left"] < 0]
    assert ("2026-08-10", "源泉所得税の納付") not in overdue
    assert ("2026-09-30", "法人税・地方法人税の確定申告") in overdue
    assert ("2026-10-13", "源泉所得税の納付") in overdue


def test_documents_flow_and_reminders():
    status = server.document_status("サンプルデザイン")
    assert status[0]["package"] == "決算" and len(status[0]["missing"]) == 4
    res = server.mark_received("c03", "決算", "2026-08期", ["期末の在庫表"])
    assert "期末の在庫表" in res["received"] and len(res["missing"]) == 3
    with pytest.raises(ValueError, match="この束に無い書類"):
        server.mark_received("c03", "決算", "2026-08期", ["年賀状"])

    cands = {r["client"]: r for r in server.reminder_candidates(days=45)}
    assert cands["合同会社サンプルデザイン"]["last_reminded"] == "2026-09-18 LINE"
    assert not cands["合同会社サンプルデザイン"]["recently_reminded"]           # 12日前なので催促してよい
    server.record_reminder("c03", "決算", "2026-08期", "LINE")
    assert {r["client"]: r for r in server.reminder_candidates()}["合同会社サンプルデザイン"]["recently_reminded"]

    server.mark_received("c03", "決算", "2026-08期", ["すべて"])
    assert "合同会社サンプルデザイン" not in {r["client"] for r in server.reminder_candidates()}


def test_mark_done_removes_from_upcoming():
    server.mark_done("サンプル商事", "消費税の確定申告", "2026-07期")
    titles = [(r["client"], r["title"]) for r in server.upcoming_deadlines(days=14)]
    assert ("株式会社サンプル商事", "消費税の確定申告") not in titles
    assert ("株式会社サンプル商事", "法人税・地方法人税の確定申告") in titles


def test_add_task_from_event_rolls_over_holidays():
    task = server.add_task("サンプル不動産", "役員変更登記（変更日から2週間以内）", event_date="2026-10-19", days_after=14,
                           documents=["株主総会議事録"])
    assert task["due"] == "2026-11-02" and "11/02" not in task["note"]      # 11/2 は月曜なのでずれない
    task2 = server.add_task("サンプル不動産", "本店移転登記", event_date="2026-10-20", days_after=14)
    assert task2["due"] == "2026-11-04" and "本来は11/03" in task2["note"]   # 11/3 文化の日
    with pytest.raises(ValueError, match="due_date か"):
        server.add_task("c05", "何か")


def test_client_lookup_errors():
    with pytest.raises(ValueError, match="複数"):
        server.document_status("サンプル")
    with pytest.raises(ValueError, match="見つかりません"):
        server.document_status("存在しない会社")


def test_file_mode_new_office(tmp_path, monkeypatch):
    path = tmp_path / "office.json"
    monkeypatch.setattr(server, "office", Office(path))
    c = server.add_client("テスト株式会社", "法人", fiscal_year_end_month=6, consumption_tax=True, withholding="毎月")
    assert c["id"] == "c01"
    server.mark_received("c01", "源泉所得税", f"{date.today().year}-{date.today().month:02d}分", ["すべて"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tracking_since"] == date.today().isoformat() and data["clients"][0]["name"] == "テスト株式会社"
    with pytest.raises(ValueError, match="決算月"):
        server.add_client("決算月なし株式会社", "法人")
