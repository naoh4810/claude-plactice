"""サンプルモードでツールの動きを確かめるテスト。

実行: python -m pytest mcp/form_report/test_server.py
"""

import csv
import os
import sys
from pathlib import Path

import pytest

os.environ.pop("FORM_SHEET_ID", None)
os.environ.pop("FORM_PERSON_COLUMN", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
from store import SampleStore  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "store", SampleStore())
    monkeypatch.setenv("FORM_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("FORM_ORGANIZATION", "サンプル事業所")


def test_describe_form_classifies_questions():
    info = server.describe_form()
    assert info["person_column"] == "利用者名"
    assert info["period"] == ["2026-09-01", "2026-09-12"]
    assert info["questions"] == {
        "記入者": "choice",
        "体調": "choice",
        "睡眠時間": "number",
        "食事量": "choice",
        "参加した活動": "choice",
        "様子・気になったこと": "text",
    }


def test_list_people_counts_and_last_date():
    people = {p["name"]: p for p in server.list_people()}
    assert people["井上 そら"] == {"name": "井上 そら", "responses": 6, "last_date": "2026-09-11"}


def test_summarize_person_in_period_splits_checkbox_answers():
    result = server.summarize(person="井上 そら", date_from="2026-09-01", date_to="2026-09-08")
    by_q = {q["question"]: q for q in result["questions"]}
    assert result["responses"] == 4
    assert by_q["体調"]["counts"] == {"普通": 2, "悪い": 2}
    assert by_q["睡眠時間"]["average"] == 4.8
    assert by_q["参加した活動"]["counts"] == {"軽作業": 1, "創作活動": 1}
    assert by_q["参加した活動"]["unanswered"] == 2
    assert [e["date"] for e in by_q["様子・気になったこと"]["entries"]] == [
        "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-08",
    ]


def test_number_question_without_answers_in_period(monkeypatch):
    store = SampleStore()
    for row in store.table()[1:]:
        if row[1] == "青木 ゆう":
            row[4] = ""                         # 睡眠時間を未記入にする
    monkeypatch.setattr(server, "store", store)
    sleep = server.summarize(person="青木 ゆう", question="睡眠時間")["questions"][0]
    assert sleep["type"] == "number" and "average" not in sleep
    html = Path(server.create_report(person="青木 ゆう")["path"]).read_text(encoding="utf-8")
    assert "回答なし" in html


def test_unknown_person_lists_known_names():
    with pytest.raises(ValueError, match="青木 ゆう"):
        server.get_responses(person="存在しない人")


def test_create_report_writes_escaped_html_with_comment(tmp_path):
    res = server.create_report(person="井上 そら", date_from="2026-09-01", date_to="2026-09-30",
                               title="支援記録", comment="睡眠が短い日は<体調>が悪い傾向。")
    html = Path(res["path"]).read_text(encoding="utf-8")
    assert Path(res["path"]).parent == tmp_path
    assert "支援記録" in html and "サンプル事業所" in html
    assert "睡眠が短い日は&lt;体調&gt;が悪い傾向。" in html
    assert "平均 5.3" in html
    assert html.count("<tr><td>09/") == 6       # 記録一覧の行数
    assert res["has_comment"]


def test_create_report_without_person_includes_name_column():
    res = server.create_report()
    html = Path(res["path"]).read_text(encoding="utf-8")
    assert "<th>利用者名</th>" in html and "集計レポート" in html
    assert "2026-09-01 〜 2026-09-12" in html     # 期間未指定なら実際の回答期間
    assert "（所見を記入）" in html


def test_export_csv_opens_in_excel():
    res = server.export_csv(person="青木 ゆう")
    raw = Path(res["path"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")     # BOM 付き（Excel で文字化けしない）
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert rows[0][:3] == ["日付", "記入者", "体調"]
    assert len(rows) == 1 + res["rows"] == 6


def test_bad_date_is_reported():
    with pytest.raises(ValueError, match="日付として読めません"):
        server.summarize(date_from="9月1日")
