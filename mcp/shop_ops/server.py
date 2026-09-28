"""ECストア運用MCP：Shopify ストアの売上・在庫・商品説明を Claude から扱う。

個人で EC を運営している人の「毎週の数字確認」「在庫切れの見落とし」「商品説明を書く時間が無い」を
まとめて引き受けるデモ。書き込み（商品説明の更新）は、既定では変更内容の確認だけにしている。

できること（ツール）:
  - shop_overview               : ストア名、商品数、今日の日付（集計の基準日）
  - list_products               : 商品一覧（状態・価格・在庫・説明文の文字数）
  - sales_report                : 期間の売上、注文数、客単価、売れ筋、日別推移、前の期間との比較
  - inventory_forecast          : 最近の売れ行きから「あと何日で在庫が切れるか」を予測
  - products_needing_description: 説明文が短い商品を、売れている順に
  - update_description          : 商品説明の更新（apply=true を渡すまで反映しない）

起動:
  python mcp/shop_ops/server.py
  （SHOPIFY_STORE_DOMAIN と SHOPIFY_ACCESS_TOKEN が無ければ架空ストアのサンプルモード）
"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from datetime import date, timedelta

from mcp.server.mcpserver import MCPServer

from store import BaseStore, Order, SampleStore, open_store, parse_date_arg

mcp = MCPServer("shop-ops")
store: BaseStore = open_store()


def plain_text(description_html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", description_html or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _period(date_from: str | None, date_to: str | None, default_days: int) -> tuple[date, date]:
    end = parse_date_arg(date_to, "date_to") or store.today()
    start = parse_date_arg(date_from, "date_from") or end - timedelta(days=default_days - 1)
    if start > end:
        raise ValueError(f"期間の開始 {start} が終了 {end} より後になっています")
    return start, end


def _valid(orders: list[Order]) -> list[Order]:
    return [o for o in orders if not o.cancelled]


def _units_by_variant(orders: list[Order]) -> dict[str, int]:
    units: dict[str, int] = defaultdict(int)
    for o in _valid(orders):
        for line in o.lines:
            if line.variant_id:
                units[line.variant_id] += line.quantity
    return units


def _summarize(orders: list[Order]) -> dict:
    valid = _valid(orders)
    revenue = sum(o.total for o in valid)
    return {
        "orders": len(valid),
        "revenue": round(revenue),
        "average_order_value": round(revenue / len(valid)) if valid else 0,
        "units": sum(line.quantity for o in valid for line in o.lines),
        "cancelled_orders": len(orders) - len(valid),
    }


def _change(now: float, before: float) -> str | None:
    if not before:
        return None
    return f"{(now - before) / before * 100:+.0f}%"


@mcp.tool()
def shop_overview() -> dict:
    """ストア名・通貨・商品数（状態別）と、集計の基準日（今日）。最初にこれで接続先を確認する。"""
    products = store.products()
    by_status: dict[str, int] = defaultdict(int)
    for p in products:
        by_status[p.status] += 1
    return {
        "mode": "sample" if isinstance(store, SampleStore) else "shopify",
        "shop": store.shop,
        "currency": store.currency,
        "today": store.today().isoformat(),
        "products_by_status": dict(by_status),
    }


@mcp.tool()
def list_products(status: str | None = None, query: str | None = None) -> list[dict]:
    """商品一覧。価格・在庫・説明文の文字数付き。

    Args:
        status: ACTIVE（販売中）/ DRAFT（下書き）/ ARCHIVED で絞り込む
        query: 商品名の部分一致
    """
    result = []
    for p in store.products():
        if status and p.status != status.upper():
            continue
        if query and query not in p.title:
            continue
        result.append({
            "id": p.id, "title": p.title, "status": p.status,
            "description_chars": len(plain_text(p.description_html)),
            "variants": [{"id": v.id, "title": v.title, "sku": v.sku, "price": v.price, "inventory": v.inventory}
                         for v in p.variants],
        })
    return result


@mcp.tool()
def sales_report(date_from: str | None = None, date_to: str | None = None, top: int = 5) -> dict:
    """期間の売上レポート。キャンセルされた注文は除く。同じ長さの直前の期間と比べた増減も付ける。

    Args:
        date_from: 開始日（例: 2026-09-01）。省略で終了日の6日前（直近7日間）
        date_to: 終了日。省略で今日
        top: 売れ筋として返す商品数
    """
    start, end = _period(date_from, date_to, 7)
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length - 1)

    orders = store.orders(start, end)
    current, previous = _summarize(orders), _summarize(store.orders(prev_start, prev_end))

    by_product: dict[str, dict] = {}
    daily: dict[str, float] = {(start + timedelta(days=i)).isoformat(): 0 for i in range(length)}
    for o in _valid(orders):
        daily[o.created_at.date().isoformat()] += o.total
        for line in o.lines:
            key = line.title.split(" - ")[0]           # バリエーション（色など）はまとめて商品単位で
            row = by_product.setdefault(key, {"title": key, "units": 0, "revenue": 0})
            row["units"] += line.quantity
            row["revenue"] += round(line.amount)

    return {
        "period": [start.isoformat(), end.isoformat()],
        "currency": store.currency,
        **current,
        "previous_period": [prev_start.isoformat(), prev_end.isoformat()],
        "change": {
            "revenue": _change(current["revenue"], previous["revenue"]),
            "orders": _change(current["orders"], previous["orders"]),
            "average_order_value": _change(current["average_order_value"], previous["average_order_value"]),
        },
        "previous": previous,
        "top_products": sorted(by_product.values(), key=lambda r: r["revenue"], reverse=True)[:top],
        "daily_revenue": {d: round(v) for d, v in daily.items()},
    }


@mcp.tool()
def inventory_forecast(days: int = 30, alert_days: int = 14) -> list[dict]:
    """販売中の商品について、直近の売れ行きから在庫が何日もつかを予測する。危ない順に並べる。

    Args:
        days: 売れ行きを計算する直近の日数
        alert_days: 残り日数がこれ以下なら「要発注」
    """
    end = store.today()
    units = _units_by_variant(store.orders(end - timedelta(days=days - 1), end))
    rows = []
    for p in store.products():
        if p.status != "ACTIVE":
            continue
        for v in p.variants:
            sold = units.get(v.id, 0)
            per_day = sold / days
            days_left = round(v.inventory / per_day, 1) if per_day else None
            if v.inventory <= 0:
                status = "在庫切れ"
            elif days_left is not None and days_left <= alert_days:
                status = "要発注"
            else:
                status = "余裕あり"
            rows.append({
                "product": p.title, "variant": v.title, "sku": v.sku, "inventory": v.inventory,
                f"sold_last_{days}_days": sold, "per_day": round(per_day, 2),
                "days_left": days_left, "status": status,
            })
    priority = {"在庫切れ": 0, "要発注": 1, "余裕あり": 2}
    return sorted(rows, key=lambda r: (priority[r["status"]], r["days_left"] if r["days_left"] is not None else float("inf")))


@mcp.tool()
def products_needing_description(min_chars: int = 60, days: int = 30) -> list[dict]:
    """説明文が min_chars 文字未満の商品を、直近でよく売れている順に返す（売れ筋から直すと効果が大きい）。

    Args:
        min_chars: これより短い説明文を「薄い」とみなす（HTML タグを除いた文字数）
        days: 売れ行きを数える直近の日数
    """
    end = store.today()
    units = _units_by_variant(store.orders(end - timedelta(days=days - 1), end))
    rows = []
    for p in store.products():
        text = plain_text(p.description_html)
        if len(text) >= min_chars or p.status == "ARCHIVED":
            continue
        rows.append({
            "id": p.id, "title": p.title, "status": p.status, "chars": len(text), "current": text,
            f"units_last_{days}_days": sum(units.get(v.id, 0) for v in p.variants),
            "variants": [v.title for v in p.variants],
            "price": min((v.price for v in p.variants), default=None),
        })
    return sorted(rows, key=lambda r: r[f"units_last_{days}_days"], reverse=True)


@mcp.tool()
def update_description(product_id: str, description_html: str, apply: bool = False) -> dict:
    """商品説明を更新する。apply=false（既定）では変更前後を見せるだけで、ストアは変えない。

    必ず先に apply=false で変更内容をユーザーに見せ、「反映して」と明確な了承を得てから apply=true で呼ぶこと。
    説明文には、商品名・既存の説明・ユーザーから聞いた情報にある事実だけを書く。素材・サイズ・効能などを推測で足さない。

    Args:
        product_id: list_products / products_needing_description の id
        description_html: 新しい説明文（<p> などの HTML 可）
        apply: true でストアに反映する
    """
    product = next((p for p in store.products() if p.id == product_id), None)
    if product is None:
        raise ValueError(f"商品 {product_id} はありません。list_products で id を確認してください")
    before = plain_text(product.description_html)
    after = plain_text(description_html)
    if not apply:
        return {"applied": False, "title": product.title, "before": before, "after": after,
                "note": "まだ反映していません。内容を確認してもらい、了承を得たら apply=true で呼んでください"}
    updated = store.update_description(product_id, description_html)
    return {"applied": True, "title": updated.title, "before": before, "after": plain_text(updated.description_html)}


@mcp.prompt()
def weekly_report() -> str:
    """店長向けの週次レポート。"""
    return (
        "ストアの週次レポートを作ってください。\n"
        "1. sales_report（直近7日）で売上・注文数・客単価と前週比、売れ筋を確認してください。\n"
        "2. inventory_forecast で「在庫切れ」「要発注」の商品を確認してください。\n"
        "3. 次の形でまとめてください。\n"
        "   - 今週の数字（売上・注文数・客単価、前週比）\n"
        "   - 気づいたこと 3点（数字の裏付けがあるものだけ）\n"
        "   - 今週やること（発注・説明文の改善など、具体的に）\n"
        "数字は tool の結果をそのまま使い、計算し直したり推測で補ったりしないでください。"
    )


@mcp.prompt()
def improve_descriptions(count: str = "3") -> str:
    """説明文が薄い商品を、売れ筋から順に書き直す流れ。"""
    return (
        f"説明文が薄い商品の上位 {count} 件を書き直します。\n"
        "1. products_needing_description で対象を確認してください。\n"
        "2. 商品ごとに、書くのに足りない情報（素材・サイズ・使い方・おすすめの場面など）をユーザーに質問してください。"
        "分からない情報を推測で書かないでください。\n"
        "3. 説明文は 120〜200字、<p> で2段落程度。1段落目で「誰の何が楽になるか」、2段落目で仕様を書いてください。\n"
        "4. update_description を apply=false で呼んで変更前後を見せ、了承を得た商品だけ apply=true で反映してください。"
    )


if __name__ == "__main__":
    mcp.run()
