"""銀行・カードの明細 CSV の読み込みと、支出の分類。

明細の形式は金融機関ごとに違うので、列名のよくある呼び方を自動で対応づける。
  - 銀行: 日付・摘要・お引出し（出金）・お預入れ（入金）の形が多い
  - カード: 利用日・利用店名・利用金額の形が多い（マイナスは返金として扱う）

分類は「ユーザーが決めたルール → 既定のルール」の順に、摘要に含まれる言葉で決める。
銀行のカード引き落としは、カードの明細と二重に数えないよう「振替（集計外）」にする。
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

CATEGORIES = [
    "収入", "住居", "水道光熱", "通信", "保険", "食費", "外食", "日用品", "車・交通", "教育", "医療",
    "サブスク", "趣味・娯楽", "旅行・レジャー", "買い物", "手数料", "現金引き出し", "その他", "振替（集計外）",
]
EXCLUDED = {"振替（集計外）"}

# 先に書いたものが優先。摘要は全角半角・大文字小文字をそろえてから比べる
DEFAULT_RULES: list[tuple[str, str]] = [
    ("カード 引落", "振替（集計外）"), ("カード引落", "振替（集計外）"), ("カード代金", "振替（集計外）"),
    ("口座振替 カード", "振替（集計外）"), ("自分の口座", "振替（集計外）"),
    ("給与", "収入"), ("賞与", "収入"), ("児童手当", "収入"),
    ("atm手数料", "手数料"), ("振込手数料", "手数料"), ("手数料", "手数料"), ("年会費", "手数料"),
    ("atm 引出", "現金引き出し"), ("atm引出", "現金引き出し"), ("カードキャッシュ", "現金引き出し"),
    ("家賃", "住居"), ("住宅ローン", "住居"), ("管理費", "住居"),
    ("電気", "水道光熱"), ("ガス", "水道光熱"), ("水道", "水道光熱"),
    ("通信", "通信"), ("モバイル", "通信"), ("インターネット", "通信"), ("携帯", "通信"),
    ("保険", "保険"), ("共済", "保険"),
    ("スーパー", "食費"), ("マート", "食費"), ("生協", "食費"), ("精肉", "食費"), ("青果", "食費"),
    ("食堂", "外食"), ("カフェ", "外食"), ("ファミレス", "外食"), ("レストラン", "外食"), ("居酒屋", "外食"),
    ("ドラッグ", "日用品"), ("100円ショップ", "日用品"), ("ホームセンター", "日用品"),
    ("ガソリン", "車・交通"), ("石油", "車・交通"), ("駐車", "車・交通"), ("高速", "車・交通"), ("鉄道", "車・交通"),
    ("学習塾", "教育"), ("塾", "教育"), ("学校", "教育"),
    ("病院", "医療"), ("クリニック", "医療"), ("薬局", "医療"), ("歯科", "医療"),
    ("動画配信", "サブスク"), ("音楽配信", "サブスク"), ("クラウド", "サブスク"),
    ("ジム", "趣味・娯楽"), ("フィットネス", "趣味・娯楽"),
    ("旅館", "旅行・レジャー"), ("ホテル", "旅行・レジャー"),
    ("通販", "買い物"),
]

COLUMN_ALIASES = {
    "date": ["日付", "取引日", "利用日", "ご利用日", "年月日", "お取引日"],
    "desc": ["摘要", "内容", "取引内容", "お取引内容", "利用店名", "ご利用店名", "利用先", "店名"],
    "amount": ["金額", "利用金額", "ご利用金額", "支払金額"],
    "out": ["お引出し", "出金", "出金額", "お支払金額", "引出額", "支払"],
    "in": ["お預入れ", "入金", "入金額", "預入額", "受取"],
}
DATE_FORMATS = ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日", "%Y%m%d")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower().strip()


@dataclass
class Txn:
    date: str          # YYYY-MM-DD
    description: str
    amount: int        # 正の数
    direction: str     # out（支出）/ in（収入・返金）
    source: str
    category: str = ""

    @property
    def month(self) -> str:
        return self.date[:7]

    @property
    def key(self) -> tuple:
        return (self.date, self.description, self.amount, self.direction, self.source)


def _parse_date(value: str) -> str:
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"日付「{value}」を読めません")


def _parse_amount(value: str) -> int | None:
    value = (value or "").replace(",", "").replace("円", "").replace("¥", "").replace("\\", "").strip()
    if not value:
        return None
    return int(float(value))


def _columns(header: list[str]) -> dict[str, str]:
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        col = next((h for h in header if h.strip() in aliases), None)
        if col:
            mapping[field] = col
    if "date" not in mapping or "desc" not in mapping or not ({"amount", "out", "in"} & set(mapping)):
        raise ValueError(f"日付・摘要（利用店名）・金額（または出金/入金）の列が必要です。CSV の列: {header}")
    return mapping


def decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文字コードを判定できません（UTF-8 か Shift_JIS で保存してください）")


def parse_statement(text: str, source: str) -> tuple[list[Txn], list[str]]:
    reader = csv.DictReader(io.StringIO(text))
    cols = _columns(reader.fieldnames or [])
    txns, problems = [], []
    for line_no, row in enumerate(reader, start=2):
        get = lambda f: row.get(cols[f], "") if f in cols else ""  # noqa: E731
        try:
            d = _parse_date(get("date"))
            out_amt, in_amt, amt = _parse_amount(get("out")), _parse_amount(get("in")), _parse_amount(get("amount"))
        except ValueError as e:
            problems.append(f"{line_no}行目: {e}")
            continue
        desc = (get("desc") or "").strip()
        if out_amt:
            txns.append(Txn(d, desc, abs(out_amt), "out", source))
        if in_amt:
            txns.append(Txn(d, desc, abs(in_amt), "in", source))
        if amt:
            txns.append(Txn(d, desc, abs(amt), "out" if amt > 0 else "in", source))   # カードのマイナスは返金
        if not (out_amt or in_amt or amt):
            problems.append(f"{line_no}行目: 金額が空か0です（{desc}）")
    return txns, problems


def categorize(txn: Txn, user_rules: list[list[str]]) -> str:
    text = normalize(txn.description)
    for keyword, category in [*user_rules, *DEFAULT_RULES]:
        if normalize(keyword) in text:
            return category
    return "収入" if txn.direction == "in" and txn.amount >= 50_000 else ""


class Household:
    def __init__(self, path: Path | None, sample_files: list[Path] | None = None):
        self.path = path
        self.data = {"household": "", "transactions": [], "rules": []}
        if path and path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        self.txns = [Txn(**t) for t in self.data["transactions"]]
        if sample_files:
            self.data["household"] = "サンプル家（架空）"
            for f in sample_files:
                self.import_text(decode(f.read_bytes()), f.name, save=False)

    def recategorize(self) -> None:
        for t in self.txns:
            t.category = categorize(t, self.data["rules"])

    def import_text(self, text: str, source: str, save: bool = True) -> dict:
        new, problems = parse_statement(text, source)
        seen = {t.key for t in self.txns}
        added = [t for t in new if t.key not in seen]
        for t in added:
            t.category = categorize(t, self.data["rules"])
        self.txns.extend(added)
        if save:
            self.save()
        return {"source": source, "added": len(added), "duplicates": len(new) - len(added), "problems": problems}

    def save(self) -> None:
        self.data["transactions"] = [asdict(t) for t in self.txns]
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def months(self) -> list[str]:
        return sorted({t.month for t in self.txns})

    @staticmethod
    def valid_date(value: str) -> str:
        return date.fromisoformat(value).isoformat()
