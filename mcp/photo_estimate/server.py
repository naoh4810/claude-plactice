"""写真 → 見積MCP：現場写真とメモから、単価表を当てて見積書のたたき台を作る。

「見積書を現場から帰ってから夜に作っている」外壁・リフォーム・設備の職人さん向けのデモ。
写真を読むのは Claude（画像を見て、工事項目と数量の見当をつける）、このサーバーは単価表・面積の目安・
組みになる工事の抜けのチェック・金額の計算・A4 の見積書づくりを受け持つ。

数量には根拠（実測／図面／写真から推定）を付け、写真から推定した項目は見積書に「概算」と明記する。
金額の最終判断は職人さん。

できること（ツール）:
  - price_list       : 単価表（区分で絞り込み）
  - calc_wall_area   : 外周・高さ・開口部から、塗装面積と足場面積の目安
  - create_estimate  : 見積を作る（お客さま・現場・件名）
  - add_items        : 工事項目を追加（単価表のコード、または単価表に無い項目）
  - remove_item      : 項目を削除
  - attach_photos    : 現場写真を見積に付ける（見積書の末尾に載る）
  - review_estimate  : 金額の計算と、抜け・推定数量・面積の食い違いのチェック
  - render_estimate  : A4 の見積書（HTML。ブラウザで印刷すれば PDF にもなる）を保存

起動:
  python mcp/photo_estimate/server.py
  （PRICE_LIST_CSV が無ければ架空の単価表、ESTIMATE_DATA_PATH が無ければ見積はメモリ上だけ）
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from pricing import PAIRING_RULES, SAMPLE_PRICE_LIST, load_price_list, wall_area, yen

mcp = MCPServer("photo-estimate")

BASES = ("実測", "図面", "写真から推定")
TAX_RATE = Decimal("0.10")
MAX_PHOTO_BYTES = 5 * 1024 * 1024


class Book:
    """見積の保存。ESTIMATE_DATA_PATH があればファイル、無ければメモリ上。"""

    def __init__(self, path: Path | None):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {"estimates": []}

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, estimate_id: str) -> dict:
        found = next((e for e in self.data["estimates"] if e["id"] == estimate_id), None)
        if found is None:
            raise ValueError(f"見積 {estimate_id} はありません")
        return found


prices = load_price_list(Path(os.environ.get("PRICE_LIST_CSV") or SAMPLE_PRICE_LIST))
book = Book(Path(os.environ["ESTIMATE_DATA_PATH"]) if os.environ.get("ESTIMATE_DATA_PATH") else None)


def _company() -> list[str]:
    return [line for line in os.environ.get("ESTIMATE_COMPANY", "サンプル住設工業\n（架空の会社）").split("\n") if line.strip()]


def _totals(est: dict) -> dict:
    subtotal = sum(yen(Decimal(i["quantity"]) * Decimal(i["unit_price"])) for i in est["items"])
    overhead = int((Decimal(subtotal) * Decimal(est["overhead_rate"])).to_integral_value(rounding=ROUND_DOWN))
    tax = int(((subtotal + overhead) * TAX_RATE).to_integral_value(rounding=ROUND_DOWN))   # 消費税の1円未満は切り捨て
    return {"subtotal": subtotal, "overhead": overhead, "tax": tax, "total": subtotal + overhead + tax}


def _lines(est: dict) -> list[dict]:
    return [{**i, "amount": yen(Decimal(i["quantity"]) * Decimal(i["unit_price"]))} for i in est["items"]]


@mcp.tool()
def price_list(category: str | None = None) -> list[dict]:
    """単価表。category（例: 塗装・設備・仮設）で絞り込める。"""
    return [{"code": p.code, "category": p.category, "name": p.name, "unit": p.unit, "unit_price": int(p.unit_price),
             "note": p.note} for p in prices.values() if not category or p.category == category]


@mcp.tool()
def calc_wall_area(perimeter_m: float, height_m: float, openings_m2: float = 0) -> dict:
    """外周（m）・高さ（m）・窓やドアなど開口部の合計（m2）から、外壁の塗装面積と足場面積の目安を出す。

    写真から外周や高さを見積もった場合は、add_items の basis を「写真から推定」にすること。
    """
    return wall_area(perimeter_m, height_m, openings_m2)


@mcp.tool()
def create_estimate(customer: str, site: str, title: str, overhead_rate: float = 0.0) -> dict:
    """見積を作る。

    Args:
        customer: お客さまの名前（例: ○○様）
        site: 現場の住所や呼び名
        title: 件名（例: 外壁塗装工事）
        overhead_rate: 諸経費の率（例: 0.05 で小計の5%）。単価表の項目で計上するなら 0
    """
    if not 0 <= overhead_rate < 1:
        raise ValueError("overhead_rate は 0 以上 1 未満にしてください（5% なら 0.05）")
    numbers = [int(e["id"].split("-")[1]) for e in book.data["estimates"]]
    est = {"id": f"est-{max(numbers, default=0) + 1}", "customer": customer, "site": site, "title": title,
           "created": date.today().isoformat(), "overhead_rate": str(overhead_rate), "items": [], "photos": []}
    book.data["estimates"].append(est)
    book.save()
    return {"id": est["id"], "customer": customer, "title": title}


@mcp.tool()
def add_items(estimate_id: str, items: list[dict]) -> dict:
    """工事項目を追加する。1件ずつ次の形で渡す。

    単価表の項目: {"code": "PNT-01", "quantity": 215, "basis": "写真から推定", "note": "南面・東面"}
    単価表に無い項目: {"name": "雨戸の交換", "unit": "枚", "unit_price": 28000, "quantity": 2, "basis": "実測"}

    basis（数量の根拠）は 実測 / 図面 / 写真から推定 のどれか。写真から推定した項目は見積書で「概算」と表示する。
    単価表に無い項目の単価は、ユーザーに確認した値だけを使い、推測で決めないこと。
    """
    est = book.get(estimate_id)
    added, errors = [], []
    for n, item in enumerate(items, start=1):
        try:
            quantity = Decimal(str(item.get("quantity")))
        except Exception:
            errors.append(f"{n}件目: quantity が数字ではありません")
            continue
        if quantity <= 0:
            errors.append(f"{n}件目: quantity は0より大きくしてください")
            continue
        basis = item.get("basis", "")
        if basis not in BASES:
            errors.append(f"{n}件目: basis は {' / '.join(BASES)} のどれかです")
            continue
        if item.get("code"):
            p = prices.get(item["code"])
            if p is None:
                errors.append(f"{n}件目: 単価表にコード {item['code']} がありません")
                continue
            line = {"code": p.code, "name": p.name, "unit": p.unit, "unit_price": str(p.unit_price)}
        else:
            if not (item.get("name") and item.get("unit") and item.get("unit_price") is not None):
                errors.append(f"{n}件目: 単価表に無い項目は name・unit・unit_price が必要です")
                continue
            try:
                unit_price = Decimal(str(item["unit_price"]).replace(",", ""))
            except Exception:
                errors.append(f"{n}件目: unit_price「{item['unit_price']}」が数字ではありません")
                continue
            line = {"code": None, "name": item["name"], "unit": item["unit"], "unit_price": str(unit_price)}
        line.update(quantity=str(quantity), basis=basis, note=item.get("note", ""))
        est["items"].append(line)
        added.append(line["name"])
    if added:
        book.save()
    return {"added": added, "errors": errors, "items": len(est["items"])}


@mcp.tool()
def remove_item(estimate_id: str, line_no: int) -> dict:
    """項目を削除する。line_no は review_estimate の lines の番号（1始まり）。"""
    est = book.get(estimate_id)
    if not 1 <= line_no <= len(est["items"]):
        raise ValueError(f"line_no は 1〜{len(est['items'])} です")
    removed = est["items"].pop(line_no - 1)
    book.save()
    return {"removed": removed["name"], "items": len(est["items"])}


@mcp.tool()
def attach_photos(estimate_id: str, photos: list[dict]) -> dict:
    """現場写真を見積に付ける。photos は [{"path": "/path/to/IMG_0001.jpg", "caption": "南面のひび割れ"}, ...]。

    見積書の末尾に「現場写真」として載る（JPEG / PNG、1枚5MBまで）。
    """
    est = book.get(estimate_id)
    attached, errors = [], []
    for p in photos:
        path = Path(p.get("path", ""))
        mime = mimetypes.guess_type(path.name)[0]
        if not path.is_file():
            errors.append(f"{path}: ファイルがありません")
        elif mime not in ("image/jpeg", "image/png"):
            errors.append(f"{path}: JPEG か PNG にしてください")
        elif path.stat().st_size > MAX_PHOTO_BYTES:
            errors.append(f"{path}: 5MB を超えています（縮小してから付けてください）")
        else:
            est["photos"].append({"path": str(path.resolve()), "caption": p.get("caption", "")})
            attached.append(path.name)
    if attached:
        book.save()
    return {"attached": attached, "errors": errors, "photos": len(est["photos"])}


@mcp.tool()
def review_estimate(estimate_id: str) -> dict:
    """見積の明細と金額（小計・諸経費・消費税10%・合計）と、確認すべき点。

    warnings: 組みになる工事の抜け、写真から推定した数量、足場と塗装の面積の食い違い など。
    """
    est = book.get(estimate_id)
    codes = {i["code"] for i in est["items"] if i["code"]}
    warnings = [msg for triggers, needs, msg in PAIRING_RULES if codes & triggers and not codes & needs]

    estimated = [i["name"] for i in est["items"] if i["basis"] == "写真から推定"]
    if estimated:
        warnings.append(f"写真から推定した数量が {len(estimated)} 件あります（{'、'.join(estimated)}）。現地で実測してから確定してください")

    def qty(code_set: set[str]) -> Decimal:
        return sum((Decimal(i["quantity"]) for i in est["items"] if i["code"] in code_set), Decimal(0))

    paint, scaffold = qty({"PNT-01", "PNT-02"}), qty({"SCF-01"})
    if paint and scaffold and scaffold < paint:
        warnings.append(f"足場（{scaffold}m2）が外壁塗装（{paint}m2）より小さくなっています。通常は足場のほうが大きくなります")
    if not est["items"]:
        warnings.append("項目がまだありません")

    return {"id": est["id"], "customer": est["customer"], "title": est["title"],
            "lines": [{"line_no": n, **line} for n, line in enumerate(_lines(est), start=1)],
            **_totals(est), "warnings": warnings, "photos": len(est["photos"])}


CSS = """
@page { size: A4; margin: 14mm; }
body { font-family: "Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans JP", sans-serif; color: #111; font-size: 10pt; }
h1 { text-align: center; font-size: 20pt; letter-spacing: 0.5em; margin: 0 0 6mm; }
.head { display: flex; justify-content: space-between; gap: 8mm; }
.to { font-size: 13pt; border-bottom: 1px solid #111; padding-bottom: 1mm; min-width: 70mm; }
.from { text-align: right; font-size: 9.5pt; }
.total { margin: 5mm 0; font-size: 14pt; border: 2px solid #111; padding: 2mm 4mm; display: inline-block; }
table { width: 100%; border-collapse: collapse; margin-top: 3mm; }
th, td { border: 1px solid #555; padding: 1.2mm 2mm; vertical-align: top; }
th { background: #eee; }
td.num { text-align: right; white-space: nowrap; }
.est { color: #a40; font-size: 8.5pt; }
.sum { width: 80mm; margin-left: auto; }
.sum td { border: none; text-align: right; padding: 0.8mm 2mm; }
.notes { margin-top: 5mm; font-size: 9pt; }
.photos { page-break-before: always; }
.photos figure { display: inline-block; width: 48%; margin: 0 1% 4mm 0; vertical-align: top; }
.photos img { width: 100%; border: 1px solid #999; }
.photos figcaption { font-size: 9pt; }
@media screen { body { max-width: 190mm; margin: 10mm auto; } }
"""


def _data_uri(path: str) -> str | None:
    p = Path(path)
    if not p.is_file():
        return None
    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


@mcp.tool()
def render_estimate(estimate_id: str, valid_days: int = 30) -> dict:
    """A4 の見積書（HTML）を保存する。写真から推定した項目には「概算」と付け、注記に実測で確定する旨を書く。

    review_estimate の warnings を確認し、ユーザーが了承してから作ること。
    """
    est = book.get(estimate_id)
    if not est["items"]:
        raise ValueError("項目がありません。add_items で追加してください")
    esc = html.escape
    t = _totals(est)
    today = date.today()
    rows = "".join(
        f"<tr><td>{n}</td><td>{esc(i['name'])}"
        + ("<br><span class='est'>概算（写真から推定）</span>" if i["basis"] == "写真から推定" else "")
        + (f"<br><small>{esc(i['note'])}</small>" if i["note"] else "")
        + f"</td><td class='num'>{Decimal(i['quantity']).normalize():f}</td><td>{esc(i['unit'])}</td>"
        f"<td class='num'>{int(Decimal(i['unit_price'])):,}</td><td class='num'>{i['amount']:,}</td></tr>"
        for n, i in enumerate(_lines(est), start=1)
    )
    overhead_row = (f"<tr><td>諸経費（{Decimal(est['overhead_rate']) * 100:g}%）</td><td>{t['overhead']:,} 円</td></tr>"
                    if t["overhead"] else "")
    has_estimated = any(i["basis"] == "写真から推定" for i in est["items"])
    notes = [f"本見積の有効期限は {today + timedelta(days=valid_days):%Y年%m月%d日} までです。"]
    if has_estimated:
        notes.append("「概算」の項目は現場写真から数量を推定しています。現地での実測後に金額が変わることがあります。")
    notes.append("現場の状況により、追加の工事が必要になる場合は事前にご相談します。")
    photos = "".join(
        f"<figure><img src='{uri}' alt=''><figcaption>{esc(p['caption'])}</figcaption></figure>"
        for p in est["photos"] if (uri := _data_uri(p["path"]))
    )
    company = "<br>".join(esc(line) for line in _company())
    page = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>御見積書 {esc(est['customer'])}</title><style>{CSS}</style></head>
<body>
<h1>御見積書</h1>
<div class="head">
  <div><div class="to">{esc(est['customer'])}</div><p>件名：{esc(est['title'])}<br>現場：{esc(est['site'])}</p></div>
  <div class="from">{today:%Y年%m月%d日}<br>見積番号：{esc(est['id'])}<br><br>{company}</div>
</div>
<div class="total">御見積金額（税込）　{t['total']:,} 円</div>
<table><thead><tr><th>No</th><th>項目</th><th>数量</th><th>単位</th><th>単価</th><th>金額</th></tr></thead>
<tbody>{rows}</tbody></table>
<table class="sum"><tr><td>小計</td><td>{t['subtotal']:,} 円</td></tr>{overhead_row}
<tr><td>消費税（10%）</td><td>{t['tax']:,} 円</td></tr><tr><td><b>合計</b></td><td><b>{t['total']:,} 円</b></td></tr></table>
<div class="notes"><b>備考</b><ul>{''.join(f'<li>{esc(n)}</li>' for n in notes)}</ul></div>
{f'<div class="photos"><h2>現場写真</h2>{photos}</div>' if photos else ''}
</body></html>
"""
    out_dir = Path(os.environ.get("ESTIMATE_EXPORT_DIR", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"見積書_{est['id']}_{today.isoformat()}.html"
    path.write_text(page, encoding="utf-8")
    return {"path": str(path.resolve()), "total": t["total"], "estimated_items": has_estimated,
            "photos": len(est["photos"])}


@mcp.prompt()
def estimate_from_photos(customer: str, title: str) -> str:
    """現場写真から見積書のたたき台を作る流れ。"""
    return (
        f"{customer} の「{title}」の見積書のたたき台を、添付の現場写真とメモから作ります。\n"
        "1. 写真を見て、必要そうな工事項目と、その根拠（写真のどこに何が見えるか）を箇条書きにしてください。"
        "見えないこと（建物の裏側、内部の配管など）は推測せず、ユーザーに確認してください。\n"
        "2. 外壁なら外周・高さ・開口部をユーザーに聞き（分からなければ写真からの推定と明記）、calc_wall_area で面積の目安を出してください。\n"
        "3. create_estimate で見積を作り、price_list のコードで add_items してください。数量の根拠（basis）を必ず付けてください。"
        "単価表に無い項目は、単価をユーザーに確認してから追加してください。\n"
        "4. attach_photos で写真を付け、review_estimate の warnings を一つずつユーザーと確認してください。\n"
        "5. 了承を得たら render_estimate で見積書を作り、保存先と合計金額を伝えてください。金額の最終判断はユーザーです。"
    )


if __name__ == "__main__":
    mcp.run()
