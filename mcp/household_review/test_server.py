"""明細の読み込み・分類・二重計上の防止・固定費・シミュレーション・報告書のテスト。

実行: python -m pytest mcp/household_review/test_server.py
"""

import csv
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.pop("HOUSEHOLD_DATA_PATH", None)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402
from ledger import Household, parse_statement  # noqa: E402


@pytest.fixture(autouse=True)
def sample_book(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "book", Household(None, server.SAMPLE_FILES))
    monkeypatch.setenv("HOUSEHOLD_EXPORT_DIR", str(tmp_path))


@pytest.fixture
def empty_book(monkeypatch, tmp_path):
    book = Household(tmp_path / "household.json")
    monkeypatch.setattr(server, "book", book)
    return book


# --- 明細の読み込み -------------------------------------------------------------------

def test_bank_and_card_formats():
    bank = "日付,摘要,お引出し,お預入れ\n2026/09/25,給与 テスト,,\"300,000\"\n2026/09/27,家賃,78000,\n"
    card = "ご利用日,ご利用店名,ご利用金額\n2026年09月03日,テストスーパー,2980円\n2026/09/05,返品 テストスーパー,-980\n"
    b, _ = parse_statement(bank, "bank")
    c, _ = parse_statement(card, "card")
    assert [(t.description, t.amount, t.direction) for t in b] == [("給与 テスト", 300000, "in"), ("家賃", 78000, "out")]
    assert [(t.date, t.amount, t.direction) for t in c] == [("2026-09-03", 2980, "out"), ("2026-09-05", 980, "in")]


def test_bad_rows_and_missing_columns():
    txns, problems = parse_statement("日付,摘要,金額\n九月,A,100\n2026/09/01,B,\n2026/09/02,C,300\n", "x")
    assert [t.description for t in txns] == ["C"] and len(problems) == 2
    with pytest.raises(ValueError, match="列が必要"):
        parse_statement("日付,メモ\n2026/09/01,A\n", "x")


def test_import_shift_jis_and_skip_duplicates(empty_book, tmp_path):
    path = tmp_path / "明細.csv"
    with path.open("w", encoding="cp932", newline="") as f:
        csv.writer(f).writerows([["日付", "摘要", "出金", "入金"], ["2026/09/01", "テストスーパー", "1200", ""]])
    assert server.import_statement(str(path))["added"] == 1
    again = server.import_statement(str(path))
    assert again["added"] == 0 and again["duplicates"] == 1


# --- 分類と二重計上 ---------------------------------------------------------------------

def test_card_payment_is_not_double_counted():
    book = server.book
    card_total = sum(t.amount for t in book.txns if t.source.startswith("カード"))
    transfers = sum(t.amount for t in book.txns if t.category == "振替（集計外）")
    assert transfers == card_total                     # 銀行のカード引き落とし＝カード明細の合計
    ov = server.overview()
    spending = sum(t.amount for t in book.txns if t.direction == "out" and t.category != "振替（集計外）")
    assert ov["monthly_spending"] == round(spending / 3) and ov["uncategorized"] == 0


def test_set_category_rule_and_uncategorized(empty_book):
    server.add_transactions([
        {"date": "2026-09-01", "description": "なぞの店", "amount": 1500},
        {"date": "2026-09-08", "description": "なぞの店", "amount": 1500},
    ])
    assert server.uncategorized() == [{"description": "なぞの店", "direction": "out", "count": 2, "total": 3000}]
    res = server.set_category("なぞ", "趣味・娯楽")
    assert res["matched_transactions"] == 2 and res["still_uncategorized"] == 0
    with pytest.raises(ValueError, match="category"):
        server.set_category("なぞ", "よく分からない")


def test_add_transactions_validation(empty_book):
    res = server.add_transactions([
        {"date": "2026/09/01", "description": "A", "amount": 100},
        {"date": "2026-09-01", "description": "B", "amount": 0},
        {"date": "2026-09-01", "description": "C", "amount": 100, "direction": "sideways"},
        {"description": "D", "amount": 100},
        {"date": "2026-09-02", "description": "給与", "amount": 250000, "direction": "in"},
    ])
    assert res["added"] == 1 and len(res["errors"]) == 4
    assert empty_book.txns[0].category == "収入"


# --- 固定費と見直し候補 ------------------------------------------------------------------

def test_fixed_costs_exclude_cash_and_irregular():
    items = {r["description"]: r for r in server.fixed_costs()["items"]}
    assert items["家賃 サンプル不動産"]["monthly"] == 78000
    assert "動画配信サービスA" in items and "サンプルモバイル 通信料" in items
    assert "ATM 引出" not in items                     # 現金引き出しは固定費に入れない
    assert "サンプル旅館" not in items                 # 1回だけの支出
    assert "水道料金" not in items                     # 2か月ごとの請求で金額がそろわない


def test_review_points_have_facts_and_no_product_names():
    points = {p["topic"]: p for p in server.review_points()}
    assert set(points) >= {"サブスク・会費", "手数料", "通信費", "保険料", "現金引き出し", "外食"}
    assert points["手数料"]["facts"] == ["年間で約6,600円"]
    assert "FP が行う" in points["保険料"]["question"]


# --- シミュレーション ---------------------------------------------------------------------

def test_simulate_without_and_with_return():
    plain = server.simulate([{"label": "A", "monthly_saving": 5000}, {"label": "B", "monthly_saving": 1000}], years=3)
    assert plain["annual_total"] == 72000 and plain["by_year"][-1] == {"year": 3, "saved": 216000}
    grown = server.simulate([{"label": "A", "monthly_saving": 10000}], years=10, annual_return=0.03)
    r = 0.03 / 12
    expected = 10000 * ((1 + r) ** 120 - 1) / r      # 毎月末に積み立てた場合の将来価値
    assert abs(grown["by_year"][-1]["with_return"] - expected) < 1
    assert "約束するものではありません" in grown["assumption"]
    with pytest.raises(ValueError, match="annual_return"):
        server.simulate([{"label": "A", "monthly_saving": 1}], annual_return=0.2)
    with pytest.raises(ValueError, match="正の数"):
        server.simulate([{"label": "A", "monthly_saving": -100}])


def test_render_report():
    res = server.render_report([{"label": "<b>通信</b>", "monthly_saving": 5000}], years=10, annual_return=0.03, client="テスト様")
    page = Path(res["path"]).read_text(encoding="utf-8")
    assert "家計見直しのご提案　テスト様" in page and "&lt;b&gt;通信&lt;/b&gt;" in page
    assert "二重計上を避けるため" in page and "約束するものではありません" in page
    assert res["annual_total"] == 60000


def test_rules_and_transactions_persist(empty_book, tmp_path):
    server.add_transactions([{"date": "2026-09-01", "description": "なぞの店", "amount": 1500}])
    server.set_category("なぞ", "その他")
    saved = json.loads((tmp_path / "household.json").read_text(encoding="utf-8"))
    assert saved["rules"] == [["なぞ", "その他"]] and saved["transactions"][0]["category"] == "その他"
    assert Household(tmp_path / "household.json").txns[0].category == "その他"
