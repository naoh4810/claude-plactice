"""Cloudflare の地域情報（国・都道府県コード・地域名）を日本語の都道府県名に直す。

Cloudflare は日本のアクセスに ISO 3166-2 の都道府県コード（JIS と同じ 01〜47。例: "13" = 東京都）と
英語の地域名（例: "Tokyo"）を付ける。コードを優先し、無ければ英語名で引く。
"""

from __future__ import annotations

PREFECTURES = [
    ("北海道", "Hokkaido"), ("青森県", "Aomori"), ("岩手県", "Iwate"), ("宮城県", "Miyagi"),
    ("秋田県", "Akita"), ("山形県", "Yamagata"), ("福島県", "Fukushima"), ("茨城県", "Ibaraki"),
    ("栃木県", "Tochigi"), ("群馬県", "Gunma"), ("埼玉県", "Saitama"), ("千葉県", "Chiba"),
    ("東京都", "Tokyo"), ("神奈川県", "Kanagawa"), ("新潟県", "Niigata"), ("富山県", "Toyama"),
    ("石川県", "Ishikawa"), ("福井県", "Fukui"), ("山梨県", "Yamanashi"), ("長野県", "Nagano"),
    ("岐阜県", "Gifu"), ("静岡県", "Shizuoka"), ("愛知県", "Aichi"), ("三重県", "Mie"),
    ("滋賀県", "Shiga"), ("京都府", "Kyoto"), ("大阪府", "Osaka"), ("兵庫県", "Hyogo"),
    ("奈良県", "Nara"), ("和歌山県", "Wakayama"), ("鳥取県", "Tottori"), ("島根県", "Shimane"),
    ("岡山県", "Okayama"), ("広島県", "Hiroshima"), ("山口県", "Yamaguchi"), ("徳島県", "Tokushima"),
    ("香川県", "Kagawa"), ("愛媛県", "Ehime"), ("高知県", "Kochi"), ("福岡県", "Fukuoka"),
    ("佐賀県", "Saga"), ("長崎県", "Nagasaki"), ("熊本県", "Kumamoto"), ("大分県", "Oita"),
    ("宮崎県", "Miyazaki"), ("鹿児島県", "Kagoshima"), ("沖縄県", "Okinawa"),
]
BY_CODE = {f"{i:02d}": ja for i, (ja, _) in enumerate(PREFECTURES, start=1)}
BY_ENGLISH = {en.lower(): ja for ja, en in PREFECTURES}
UNKNOWN_JP = "日本（都道府県不明）"


def prefecture_label(country: str | None, region_code: str | None, region: str | None) -> str:
    if not country:
        return "不明"
    if country != "JP":
        return f"海外（{country}）"
    code = (region_code or "").strip()
    if code.isdigit() and f"{int(code):02d}" in BY_CODE:
        return BY_CODE[f"{int(code):02d}"]
    name = (region or "").lower().replace(" prefecture", "").replace("-ken", "").replace("-fu", "").replace("-to", "").strip()
    return BY_ENGLISH.get(name, UNKNOWN_JP)
