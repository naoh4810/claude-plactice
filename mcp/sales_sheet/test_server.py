"""サンプルモードでツールの動きを確かめるテスト。

実行: python -m pytest mcp/sales_sheet/test_server.py
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.pop("SALES_SHEET_ID", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
from store import SampleStore  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(server, "store", SampleStore())


def test_skips_empty_numbered_rows():
    names = [c["お客さま"] for c in server.search_contacts(sheet="営業")]
    assert names == ["山田 花子", "佐藤 健", "鈴木 一郎", "高橋 美咲"]


def test_stalled_merges_same_person_and_drops_closed():
    stalled = {s["name"]: s for s in server.stalled_contacts()}
    assert "鈴木 一郎" not in stalled          # 否決
    assert "小林 恵" not in stalled            # NG
    assert stalled["佐藤 健"]["stage"] == "1回目商談"  # 交流会の「面談予定」より進んでいる方
    assert len(stalled["佐藤 健"]["rows"]) == 2
    assert stalled["佐藤 健"]["business"] == "家庭教師サトウ"


def test_pipeline_counts_meeting_rate_per_event():
    summary = server.pipeline_summary()
    assert summary["by_event"]["サンプル交流会B"] == {"met": 3, "meeting_or_later": 1}


def test_add_contact_fills_first_empty_row_and_reports_skipped():
    res = server.add_contact(name="渡辺 光", sheet="営業", event="サンプル交流会C", note="整体院", email="h@example.com")
    assert res["written"]
    assert res["contact"]["row"] == 6
    assert res["contact"]["メモ"] == "整体院"
    assert res["skipped"] == {"交流会": "サンプル交流会C"}


def test_add_contact_refuses_duplicate():
    res = server.add_contact(name="山田 花子", sheet="交流会")
    assert not res["written"]


def test_update_contact_appends_note():
    res = server.update_contact(sheet="交流会", row=5, progress="面談予定", note_append="LINEでお礼送付")
    assert res["contact"]["進捗"] == "面談予定"
    assert res["contact"]["内容・感想"] == "時間がなく話せず\nLINEでお礼送付"
