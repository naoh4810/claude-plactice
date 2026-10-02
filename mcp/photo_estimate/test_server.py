"""単価表・面積の目安・見積の計算とチェック・見積書の出力のテスト。

実行: python -m pytest mcp/photo_estimate/test_server.py
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.pop("PRICE_LIST_CSV", None)
os.environ.pop("ESTIMATE_DATA_PATH", None)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402
from pricing import load_price_list, wall_area  # noqa: E402

PHOTO = HERE / "examples" / "デモ用_外壁.png"


@pytest.fixture(autouse=True)
def fresh_book(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "book", server.Book(None))
    monkeypatch.setenv("ESTIMATE_EXPORT_DIR", str(tmp_path))


def new_estimate(**kw):
    return server.create_estimate("テスト 様", "テスト市", "外壁塗装工事", **kw)["id"]


# --- 単価表と面積 -------------------------------------------------------------------

def test_wall_area_rule_of_thumb():
    assert wall_area(40, 6, 25) == {"paint_area_m2": 215.0, "scaffold_area_m2": 336.0,
                                    "formula": "塗装面積＝外周×高さ−開口部、足場面積＝(外周+8m)×(高さ+1m)（目安）"}
    with pytest.raises(ValueError, match="開口部"):
        wall_area(10, 2, 30)
    with pytest.raises(ValueError, match="0より大きい"):
        wall_area(0, 6)


def test_price_list_csv_with_commas_and_shift_jis(tmp_path):
    path = tmp_path / "単価.csv"
    path.write_bytes("コード,区分,項目,単位,単価,備考\nX-1,塗装,テスト塗装,m2,\"2,600\",\n".encode("cp932"))
    assert int(load_price_list(path)["X-1"].unit_price) == 2600
    path.write_text("コード,区分,項目,単位,単価,備考\nX-1,塗装,テスト塗装,m2,時価,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="2行目"):
        load_price_list(path)


# --- 見積の計算 ---------------------------------------------------------------------

def test_totals_with_decimal_quantities_and_tax_rounding():
    est = new_estimate(overhead_rate=0.05)
    server.add_items(est, [
        {"code": "PNT-01", "quantity": 152.5, "basis": "実測"},      # 152.5 × 2600 = 396,500
        {"code": "SEA-01", "quantity": 33.3, "basis": "実測"},        # 33.3 × 1100 = 36,630
        {"name": "雨戸の交換", "unit": "枚", "unit_price": 28000, "quantity": 1, "basis": "実測"},
    ])
    r = server.review_estimate(est)
    assert r["subtotal"] == 396_500 + 36_630 + 28_000 == 461_130
    assert r["overhead"] == 23_056                    # 461,130 × 5% = 23,056.5 → 切り捨て
    assert r["tax"] == 48_418                         # (461,130 + 23,056) × 10% = 48,418.6 → 切り捨て
    assert r["total"] == 461_130 + 23_056 + 48_418


def test_line_amount_rounds_half_up():
    est = new_estimate()
    server.add_items(est, [{"name": "端数", "unit": "m", "unit_price": 333, "quantity": 0.5, "basis": "実測"}])
    assert server.review_estimate(est)["lines"][0]["amount"] == 167    # 166.5 → 167


def test_add_items_validation():
    est = new_estimate()
    res = server.add_items(est, [
        {"code": "NOPE", "quantity": 1, "basis": "実測"},
        {"code": "PNT-01", "quantity": 0, "basis": "実測"},
        {"code": "PNT-01", "quantity": "たくさん", "basis": "実測"},
        {"code": "PNT-01", "quantity": 10, "basis": "勘"},
        {"name": "単価なし", "unit": "式", "quantity": 1, "basis": "実測"},
        {"name": "時価の品", "unit": "式", "unit_price": "時価", "quantity": 1, "basis": "実測"},
        {"code": "PNT-01", "quantity": 10, "basis": "図面"},
    ])
    assert res["added"] == ["外壁塗装 シリコン（3回塗り）"] and len(res["errors"]) == 6


def test_remove_item():
    est = new_estimate()
    server.add_items(est, [{"code": "PRE-01", "quantity": 10, "basis": "実測"}, {"code": "PRE-02", "quantity": 10, "basis": "実測"}])
    assert server.remove_item(est, 1)["removed"] == "高圧洗浄"
    with pytest.raises(ValueError, match="line_no"):
        server.remove_item(est, 5)


# --- チェック -----------------------------------------------------------------------

def test_review_warns_about_missing_pairs_estimates_and_area_mismatch():
    est = new_estimate()
    server.add_items(est, [
        {"code": "PNT-01", "quantity": 215, "basis": "写真から推定"},
        {"code": "SCF-01", "quantity": 150, "basis": "写真から推定"},
        {"code": "EQP-01", "quantity": 1, "basis": "実測"},
    ])
    warnings = "\n".join(server.review_estimate(est)["warnings"])
    for expected in ("高圧洗浄", "養生", "飛散防止ネット", "既存品の撤去", "写真から推定した数量が 2 件", "足場（150m2）が外壁塗装（215m2）より小さく"):
        assert expected in warnings


def test_complete_estimate_has_only_the_estimate_warning():
    est = new_estimate()
    a = wall_area(40, 6, 25)
    server.add_items(est, [{"code": c, "quantity": q, "basis": "実測"} for c, q in [
        ("SCF-01", a["scaffold_area_m2"]), ("SCF-02", a["scaffold_area_m2"]), ("PRE-01", a["paint_area_m2"]),
        ("PRE-02", a["paint_area_m2"]), ("PNT-01", a["paint_area_m2"])]])
    assert server.review_estimate(est)["warnings"] == []


# --- 写真と見積書 -------------------------------------------------------------------

def test_attach_photos_validation(tmp_path):
    est = new_estimate()
    (tmp_path / "memo.txt").write_text("x", encoding="utf-8")
    res = server.attach_photos(est, [{"path": str(PHOTO), "caption": "南面"}, {"path": str(tmp_path / "memo.txt")},
                                     {"path": str(tmp_path / "none.jpg")}])
    assert res["attached"] == ["デモ用_外壁.png"] and len(res["errors"]) == 2


def test_render_marks_estimates_and_embeds_photos():
    est = new_estimate()
    server.add_items(est, [{"code": "PNT-01", "quantity": 215, "basis": "写真から推定"},
                           {"name": "<script>", "unit": "式", "unit_price": 1000, "quantity": 1, "basis": "実測"}])
    server.attach_photos(est, [{"path": str(PHOTO), "caption": "南面のひび割れ"}])
    res = server.render_estimate(est, valid_days=30)
    page = Path(res["path"]).read_text(encoding="utf-8")
    assert "概算（写真から推定）" in page and "実測後に金額が変わる" in page
    assert "data:image/png;base64," in page and "南面のひび割れ" in page
    assert "&lt;script&gt;" in page and "<script>" not in page
    assert f"{res['total']:,} 円" in page


def test_render_without_estimates_has_no_estimate_note():
    est = new_estimate()
    server.add_items(est, [{"code": "EQP-01", "quantity": 1, "basis": "実測"}, {"code": "EQP-02", "quantity": 1, "basis": "実測"}])
    page = Path(server.render_estimate(est)["path"]).read_text(encoding="utf-8")
    assert "概算" not in page and "現場写真" not in page
    with pytest.raises(ValueError, match="項目がありません"):
        server.render_estimate(new_estimate())


def test_estimates_persist(tmp_path, monkeypatch):
    path = tmp_path / "est.json"
    monkeypatch.setattr(server, "book", server.Book(path))
    est = new_estimate()
    server.add_items(est, [{"code": "PRE-01", "quantity": 12.5, "basis": "図面"}])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["estimates"][0]["items"][0]["quantity"] == "12.5"
    assert server.Book(path).get(est)["items"][0]["basis"] == "図面"
