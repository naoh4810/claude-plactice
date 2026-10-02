"""単価表の読み込み、面積の目安の計算、組みになる工事の抜けのチェック。

単価表は CSV（コード,区分,項目,単位,単価,備考）。職人さんが Excel で直せる形にしている。
面積の式は現場でよく使う目安で、正確な数量は実測か図面で確かめる前提。
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SAMPLE_PRICE_LIST = Path(__file__).with_name("sample_price_list.csv")


@dataclass(frozen=True)
class PriceItem:
    code: str
    category: str
    name: str
    unit: str
    unit_price: Decimal
    note: str = ""


def load_price_list(path: Path) -> dict[str, PriceItem]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"{path} の文字コードを判定できません（UTF-8 か Shift_JIS で保存してください）")
    items = {}
    for line_no, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            price = Decimal(str(row["単価"]).replace(",", "").strip())
        except Exception:
            raise ValueError(f"単価表 {line_no}行目: 単価「{row.get('単価')}」が数字ではありません") from None
        code = (row.get("コード") or "").strip()
        if not code:
            raise ValueError(f"単価表 {line_no}行目: コードが空です")
        items[code] = PriceItem(code, (row.get("区分") or "").strip(), row["項目"].strip(), row["単位"].strip(),
                                price, (row.get("備考") or "").strip())
    return items


def yen(value: Decimal) -> int:
    """1円未満は四捨五入（明細の金額）。"""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def wall_area(perimeter_m: float, height_m: float, openings_m2: float = 0) -> dict:
    """外壁の塗装面積と足場の面積の目安。

    塗装面積 = 外周 × 高さ − 開口部（窓・ドア）
    足場面積 = (外周 + 8m) × (高さ + 1m)   ※建物から足場までの離れと、上端の手すり分を見込む目安
    """
    if perimeter_m <= 0 or height_m <= 0:
        raise ValueError("外周と高さは0より大きい値にしてください")
    if openings_m2 < 0:
        raise ValueError("開口部の面積はマイナスにできません")
    paint = Decimal(str(perimeter_m)) * Decimal(str(height_m)) - Decimal(str(openings_m2))
    if paint <= 0:
        raise ValueError("開口部が壁の面積より大きくなっています")
    scaffold = (Decimal(str(perimeter_m)) + 8) * (Decimal(str(height_m)) + 1)
    return {
        "paint_area_m2": float(paint.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        "scaffold_area_m2": float(scaffold.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        "formula": "塗装面積＝外周×高さ−開口部、足場面積＝(外周+8m)×(高さ+1m)（目安）",
    }


# ある工事が入っているのに、普通は一緒に必要になる工事が入っていないときに警告する
PAIRING_RULES = [
    ({"PNT-01", "PNT-02", "PNT-04"}, {"PRE-01"}, "塗装があるのに高圧洗浄が入っていません"),
    ({"PNT-01", "PNT-02"}, {"PRE-02"}, "外壁塗装があるのに養生が入っていません"),
    ({"PNT-01", "PNT-02", "PNT-04"}, {"SCF-01"}, "塗装があるのに足場が入っていません（平屋で脚立で届く場合などは不要）"),
    ({"SCF-01"}, {"SCF-02"}, "足場があるのに飛散防止ネットが入っていません"),
    ({"EQP-01"}, {"EQP-02"}, "給湯器交換があるのに既存品の撤去・処分が入っていません"),
]
