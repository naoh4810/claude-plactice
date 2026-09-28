"""Shopify ストアの商品・注文の読み書き。

バックエンドは2種類:
  - ShopifyStore : 本番。Admin GraphQL API をカスタムアプリのアクセストークンで呼ぶ
  - SampleStore  : 認証なしのデモ用。sample_store.json（架空のサウナ用品店）。書き込みはメモリ上のみ

どちらも同じ Product / Order に変換して返すので、集計側は接続先を意識しない。
"""

from __future__ import annotations

import copy
import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAMPLE_PATH = Path(__file__).with_name("sample_store.json")
DEFAULT_API_VERSION = "2026-07"


def parse_date_arg(value: str | None, label: str) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"{label}「{value}」を日付として読めません（例: 2026-09-01）")


@dataclass
class Variant:
    id: str
    title: str
    sku: str
    price: float
    inventory: int


@dataclass
class Product:
    id: str
    title: str
    status: str                      # ACTIVE / DRAFT / ARCHIVED
    description_html: str
    variants: list[Variant] = field(default_factory=list)


@dataclass
class Line:
    product_id: str | None           # 削除済み商品の注文では None
    variant_id: str | None
    title: str
    quantity: int
    amount: float


@dataclass
class Order:
    id: str
    name: str
    created_at: datetime
    cancelled: bool
    total: float
    lines: list[Line] = field(default_factory=list)


class BaseStore:
    shop: str = ""
    currency: str = "JPY"

    def products(self) -> list[Product]:
        raise NotImplementedError

    def orders(self, date_from: date, date_to: date) -> list[Order]:
        """期間内（両端を含む）に作成された注文。キャンセル済みも含めて返す。"""
        raise NotImplementedError

    def update_description(self, product_id: str, description_html: str) -> Product:
        raise NotImplementedError

    def today(self) -> date:
        return date.today()


class SampleStore(BaseStore):
    def __init__(self, path: Path = SAMPLE_PATH):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.shop = data["shop"]
        self.currency = data["currency"]
        self._products = [
            Product(p["id"], p["title"], p["status"], p["description_html"], [Variant(**v) for v in p["variants"]])
            for p in copy.deepcopy(data["products"])
        ]
        self._orders = [
            Order(o["id"], o["name"], datetime.fromisoformat(o["created_at"]), o["cancelled"], o["total"],
                  [Line(**line) for line in o["lines"]])
            for o in data["orders"]
        ]

    def products(self) -> list[Product]:
        return self._products

    def orders(self, date_from: date, date_to: date) -> list[Order]:
        return [o for o in self._orders if date_from <= o.created_at.date() <= date_to]

    def update_description(self, product_id: str, description_html: str) -> Product:
        product = next((p for p in self._products if p.id == product_id), None)
        if product is None:
            raise ValueError(f"商品 {product_id} はありません")
        product.description_html = description_html
        return product

    def today(self) -> date:
        """サンプルは過去のデータなので、最後の注文日を「今日」とみなす。"""
        return max(o.created_at for o in self._orders).date()


# 1回の問い合わせコストが上限（1000）を超えないよう、ページサイズ × 入れ子の件数を約500に抑える
PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 25, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title status descriptionHtml
      variants(first: 20) { nodes { id title sku price inventoryQuantity } }
    }
  }
}
"""

ORDERS_QUERY = """
query($cursor: String, $query: String) {
  orders(first: 25, after: $cursor, query: $query, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt cancelledAt
      totalPriceSet { shopMoney { amount } }
      lineItems(first: 20) {
        nodes {
          title quantity
          product { id }
          variant { id }
          originalTotalSet { shopMoney { amount } }
        }
      }
    }
  }
}
"""

UPDATE_MUTATION = """
mutation($product: ProductUpdateInput!) {
  productUpdate(product: $product) {
    product { id title status descriptionHtml variants(first: 20) { nodes { id title sku price inventoryQuantity } } }
    userErrors { field message }
  }
}
"""

SHOP_QUERY = "{ shop { name currencyCode ianaTimezone } }"


class ShopifyError(RuntimeError):
    pass


def _product_from_node(node: dict) -> Product:
    return Product(
        node["id"], node["title"], node["status"], node.get("descriptionHtml") or "",
        [Variant(v["id"], v["title"], v.get("sku") or "", float(v["price"]), v.get("inventoryQuantity") or 0)
         for v in node["variants"]["nodes"]],
    )


def _order_from_node(node: dict, tz: ZoneInfo) -> Order:
    created = datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")).astimezone(tz)
    return Order(
        node["id"], node["name"], created,
        node["cancelledAt"] is not None, float(node["totalPriceSet"]["shopMoney"]["amount"]),
        [Line((li.get("product") or {}).get("id"), (li.get("variant") or {}).get("id"), li["title"],
              li["quantity"], float(li["originalTotalSet"]["shopMoney"]["amount"]))
         for li in node["lineItems"]["nodes"]],
    )


class ShopifyStore(BaseStore):
    """必要なスコープ: read_products, write_products, read_orders, read_inventory。
    60日より前の注文も集計するなら read_all_orders も必要。"""

    def __init__(self, domain: str, token: str, api_version: str = DEFAULT_API_VERSION):
        self._url = f"https://{domain}/admin/api/{api_version}/graphql.json"
        self._token = token
        shop = self._graphql(SHOP_QUERY)["shop"]
        self.shop, self.currency = shop["name"], shop["currencyCode"]
        # 注文日時は UTC で返るので、日付の集計はストアのタイムゾーンで行う
        self._tz = ZoneInfo(shop.get("ianaTimezone") or "Asia/Tokyo")

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": self._token},
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))

    def _graphql(self, query: str, variables: dict | None = None) -> dict:
        body = self._post({"query": query, "variables": variables or {}})
        if body.get("errors"):
            raise ShopifyError(f"Shopify API エラー: {body['errors']}")
        return body["data"]

    def _paginate(self, query: str, key: str, variables: dict | None = None) -> list[dict]:
        nodes, cursor = [], None
        while True:
            conn = self._graphql(query, {**(variables or {}), "cursor": cursor})[key]
            nodes.extend(conn["nodes"])
            if not conn["pageInfo"]["hasNextPage"]:
                return nodes
            cursor = conn["pageInfo"]["endCursor"]

    def products(self) -> list[Product]:
        return [_product_from_node(n) for n in self._paginate(PRODUCTS_QUERY, "products")]

    def orders(self, date_from: date, date_to: date) -> list[Order]:
        # API 側の日付の解釈（UTC かストア時刻か）に左右されないよう前後1日広く取り、下で正確に絞る
        wide_from, wide_to = date_from - timedelta(days=1), date_to + timedelta(days=1)
        query = f"created_at:>={wide_from.isoformat()} created_at:<={wide_to.isoformat()}"
        orders = [_order_from_node(n, self._tz) for n in self._paginate(ORDERS_QUERY, "orders", {"query": query})]
        return [o for o in orders if date_from <= o.created_at.date() <= date_to]

    def today(self) -> date:
        return datetime.now(self._tz).date()

    def update_description(self, product_id: str, description_html: str) -> Product:
        result = self._graphql(UPDATE_MUTATION, {"product": {"id": product_id, "descriptionHtml": description_html}})
        payload = result["productUpdate"]
        if payload["userErrors"]:
            raise ShopifyError(f"商品を更新できませんでした: {payload['userErrors']}")
        return _product_from_node(payload["product"])


def open_store() -> BaseStore:
    """SHOPIFY_STORE_DOMAIN と SHOPIFY_ACCESS_TOKEN があれば本番、無ければサンプル。"""
    domain = os.environ.get("SHOPIFY_STORE_DOMAIN")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if domain and token:
        return ShopifyStore(domain, token, os.environ.get("SHOPIFY_API_VERSION", DEFAULT_API_VERSION))
    return SampleStore()
