"""サンプルモードのツールと、偽の Shopify API を相手にした本番クライアントのテスト。

実行: python -m pytest mcp/shop_ops/test_server.py
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

os.environ.pop("SHOPIFY_STORE_DOMAIN", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
import store as store_mod  # noqa: E402
from store import SampleStore, ShopifyError, ShopifyStore  # noqa: E402

SAMPLE = json.loads(store_mod.SAMPLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    monkeypatch.setattr(server, "store", SampleStore())


# --- サンプルモードのツール ---------------------------------------------------

def test_sales_report_matches_raw_data_and_excludes_cancelled():
    report = server.sales_report(date_from="2026-09-01", date_to="2026-09-30")
    in_range = [o for o in SAMPLE["orders"] if o["created_at"][:7] == "2026-09"]
    valid = [o for o in in_range if not o["cancelled"]]
    assert report["orders"] == len(valid)
    assert report["revenue"] == sum(o["total"] for o in valid)
    assert report["cancelled_orders"] == len(in_range) - len(valid)
    assert sum(report["daily_revenue"].values()) == report["revenue"]
    assert report["previous_period"] == ["2026-08-02", "2026-08-31"]


def test_sales_report_defaults_to_last_7_days_and_groups_variants():
    report = server.sales_report()
    assert report["period"] == ["2026-09-21", "2026-09-27"]
    titles = [p["title"] for p in report["top_products"]]
    assert all(" - " not in t for t in titles)          # 色違いは商品単位にまとめる
    revenues = [p["revenue"] for p in report["top_products"]]
    assert revenues == sorted(revenues, reverse=True)


def test_inventory_forecast_flags_low_stock_first():
    rows = server.inventory_forecast(days=30, alert_days=14)
    assert [r["sku"] for r in rows[:2]] == ["PONCHO-01", "AROMA-BIR"]
    assert all(r["status"] == "要発注" for r in rows[:2])
    assert "GIFT-01" not in [r["sku"] for r in rows]   # 下書き商品は対象外


def test_out_of_stock_without_sales_comes_first(monkeypatch):
    s = SampleStore()
    s.products()[5].variants[0].inventory = 0         # 温度計を在庫ゼロに
    monkeypatch.setattr(server, "store", s)
    first = server.inventory_forecast()[0]
    assert (first["sku"], first["status"]) == ("TMP-01", "在庫切れ")


def test_products_needing_description_sorted_by_sales():
    rows = server.products_needing_description(min_chars=60)
    assert rows[0]["title"] == "ロウリュ用アロマ（白樺）"
    assert all(r["chars"] < 60 for r in rows)
    assert "サウナハット（フェルト）" not in [r["title"] for r in rows]


def test_update_description_is_preview_until_apply():
    pid = "gid://shopify/Product/7"
    preview = server.update_description(pid, "<p>折りたためる&amp;軽い</p>")
    assert preview == {**preview, "applied": False, "before": "", "after": "折りたためる&軽い"}
    assert server.list_products(query="ポンチョ")[0]["description_chars"] == 0
    done = server.update_description(pid, "<p>折りたためる&amp;軽い</p>", apply=True)
    assert done["applied"]
    assert server.list_products(query="ポンチョ")[0]["description_chars"] == len("折りたためる&軽い")


def test_bad_dates_are_rejected():
    with pytest.raises(ValueError, match="日付として読めません"):
        server.sales_report(date_from="9月1日")
    with pytest.raises(ValueError, match="より後"):
        server.sales_report(date_from="2026-09-10", date_to="2026-09-01")


# --- 本番クライアント（偽の Shopify API） ------------------------------------

def _order_node(n, created_at, cancelled=False):
    return {
        "id": f"gid://shopify/Order/{n}", "name": f"#{n}", "createdAt": created_at,
        "cancelledAt": "2026-09-03T00:00:00Z" if cancelled else None,
        "totalPriceSet": {"shopMoney": {"amount": "3980.0"}},
        "lineItems": {"nodes": [{"title": "ハット", "quantity": 1, "product": {"id": "P1"}, "variant": None,
                                 "originalTotalSet": {"shopMoney": {"amount": "3980.0"}}}]},
    }


class FakeShopify:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        q, v = payload["query"], payload["variables"]
        if "shop {" in q:
            return {"data": {"shop": {"name": "テスト店", "currencyCode": "JPY", "ianaTimezone": "Asia/Tokyo"}}}
        if "orders(" in q:
            if v["cursor"] is None:
                return {"data": {"orders": {"pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                            "nodes": [_order_node(1, "2026-09-01T23:30:00Z"),     # JST 9/2 08:30
                                                      _order_node(2, "2026-09-01T10:00:00Z")]}}}  # JST 9/1 19:00
            return {"data": {"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                        "nodes": [_order_node(3, "2026-09-02T01:00:00Z", cancelled=True)]}}}
        if "productUpdate" in q:
            if not v["product"]["descriptionHtml"]:
                return {"data": {"productUpdate": {"product": None, "userErrors": [{"field": ["descriptionHtml"], "message": "空です"}]}}}
            return {"data": {"productUpdate": {"product": {
                "id": v["product"]["id"], "title": "ハット", "status": "ACTIVE", "descriptionHtml": v["product"]["descriptionHtml"],
                "variants": {"nodes": [{"id": "V1", "title": "グレー", "sku": None, "price": "3980.00", "inventoryQuantity": 3}]}},
                "userErrors": []}}}
        return {"errors": [{"message": "unexpected query"}]}


@pytest.fixture
def shopify(monkeypatch):
    fake = FakeShopify()
    monkeypatch.setattr(ShopifyStore, "_post", lambda _self, payload: fake(payload))
    return ShopifyStore("test.myshopify.com", "token"), fake


def test_shopify_orders_paginate_and_use_shop_timezone(shopify):
    client, fake = shopify
    orders = client.orders(date(2026, 9, 2), date(2026, 9, 2))
    assert [o.name for o in orders] == ["#1", "#3"]          # #2 は JST では 9/1
    assert orders[1].cancelled and orders[0].lines[0].variant_id is None
    order_calls = [c for c in fake.calls if "orders(" in c["query"]]
    assert order_calls[1]["variables"]["cursor"] == "c1"
    assert "created_at:>=2026-09-01" in order_calls[0]["variables"]["query"]   # 前後1日広く取る


def test_shopify_update_sends_product_input_and_raises_user_errors(shopify):
    client, fake = shopify
    updated = client.update_description("gid://shopify/Product/1", "<p>新しい説明</p>")
    assert updated.description_html == "<p>新しい説明</p>" and updated.variants[0].sku == ""
    assert fake.calls[-1]["variables"] == {"product": {"id": "gid://shopify/Product/1", "descriptionHtml": "<p>新しい説明</p>"}}
    with pytest.raises(ShopifyError, match="空です"):
        client.update_description("gid://shopify/Product/1", "")


def test_shopify_graphql_errors_are_raised(shopify):
    client, _ = shopify
    with pytest.raises(ShopifyError, match="unexpected query"):
        client.products()
