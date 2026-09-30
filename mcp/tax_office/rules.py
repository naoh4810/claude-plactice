"""顧問先の条件から、申告・納付などの期限を計算する。

ここにあるのは実務でよく使う原則的な期限の目安で、個別の事情（特例の届出の有無、中間申告の要否、
災害による延長など）は反映しきれない。最終確認は事務所で行う前提。

期限が土日・祝日・年末年始（12/29〜1/3）に当たるときは、翌営業日にずらす（国税通則法10条2項の考え方）。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

try:
    import holidays as _holidays
except ImportError:          # 無ければ土日と年末年始だけで判定する
    _holidays = None

# 書類の束（package）ごとの必要書類。同じ束の期限（例: 法人税と消費税）は1回の依頼で済む
DOCUMENTS = {
    "決算": ["通帳のコピー（期末まで）", "売上・経費の請求書と領収書", "売掛金・買掛金の残高一覧",
             "期末の在庫表", "固定資産の購入・売却の資料", "借入金の残高証明書"],
    "確定申告": ["売上の一覧（請求書の控え）", "経費の領収書", "通帳のコピー", "国民年金・国民健康保険の支払額",
                 "生命保険料・地震保険料の控除証明書", "医療費の領収書（医療費控除を使う場合）"],
    "源泉所得税": ["給与・報酬の支払額の一覧"],
    "年末調整": ["扶養控除等申告書", "保険料控除申告書", "基礎控除申告書（兼 配偶者控除等申告書）",
                 "前職の源泉徴収票（年の途中で入社した人）"],
    "償却資産": ["今年取得・廃棄した資産の一覧"],
}


@dataclass
class Deadline:
    client_id: str
    title: str
    due: date
    package: str          # 書類の束（DOCUMENTS のキー、または手動の期限は "個別"）
    period: str           # 対象の期間（例: 2026-08期、2026年分、2026-09分）
    note: str = ""
    documents: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.client_id}|{self.package}|{self.period}"


def _jp_holidays(year: int) -> set[date]:
    return set(_holidays.Japan(years=year)) if _holidays else set()


def is_closed(d: date) -> bool:
    if d.weekday() >= 5:
        return True
    if (d.month == 12 and d.day >= 29) or (d.month == 1 and d.day <= 3):
        return True
    return d in _jp_holidays(d.year)


def next_business_day(d: date) -> date:
    while is_closed(d):
        d += timedelta(days=1)
    return d


def month_end(year: int, month: int) -> date:
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _deadline(client: dict, title: str, raw_due: date, package: str, period: str, note: str = "") -> Deadline:
    due = next_business_day(raw_due)
    if due != raw_due:
        note = f"{note}（本来は{raw_due:%m/%d}、休日のため翌営業日）".strip()
    return Deadline(client["id"], title, due, package, period, note, list(DOCUMENTS.get(package, [])))


def deadlines_for(client: dict, years: range) -> list[Deadline]:
    """years の各年について、顧問先の条件から出てくる期限をすべて返す。"""
    out: list[Deadline] = []
    for y in years:
        if client["type"] == "法人":
            m = client["fiscal_year_end_month"]
            fy_end = month_end(y, m)
            period = f"{fy_end:%Y-%m}期"
            ext = client.get("filing_extension", False)
            ct_ext = ext and client.get("consumption_tax_extension", False)
            normal_due = month_end(y, m + 2)
            out.append(_deadline(client, "法人税・地方法人税の確定申告", month_end(y, m + (3 if ext else 2)), "決算", period,
                                 "申告期限の延長の特例（1か月）" if ext else ""))
            if client.get("consumption_tax"):
                out.append(_deadline(client, "消費税の確定申告", month_end(y, m + (3 if ct_ext else 2)), "決算", period,
                                     "消費税の申告期限の延長（届出済み）" if ct_ext else ""))
            if ext:
                # 延長しても納付期限は延びない（延長期間は利子税がかかる）ので、納付を別の期限として出す
                out.append(_deadline(client, "法人税（見込み額）の納付", normal_due, "決算", period,
                                     "申告を延長していても納付期限は2か月。見込み額で納付する"))
            if client.get("interim_filing"):
                out.append(_deadline(client, "法人税の中間申告", month_end(y, m + 8), "中間", f"{fy_end:%Y-%m}期の翌期",
                                     "前期の法人税額が一定額を超える場合に必要"))
        else:
            period = f"{y}年分"
            out.append(_deadline(client, "所得税の確定申告", date(y + 1, 3, 15), "確定申告", period))
            if client.get("consumption_tax"):
                out.append(_deadline(client, "消費税の確定申告（個人）", date(y + 1, 3, 31), "確定申告", period))

        withholding = client.get("withholding")
        if withholding == "毎月":
            for month in range(1, 13):
                nxt = date(y, month, 1) + timedelta(days=32)
                out.append(_deadline(client, "源泉所得税の納付", date(nxt.year, nxt.month, 10), "源泉所得税", f"{y}-{month:02d}分"))
        elif withholding == "納期の特例":
            out.append(_deadline(client, "源泉所得税の納付（納期の特例・1〜6月分）", date(y, 7, 10), "源泉所得税", f"{y}年1〜6月分"))
            out.append(_deadline(client, "源泉所得税の納付（納期の特例・7〜12月分）", date(y + 1, 1, 20), "源泉所得税", f"{y}年7〜12月分"))

        if client.get("employees"):
            out.append(_deadline(client, "法定調書合計表・給与支払報告書の提出", date(y + 1, 1, 31), "年末調整", f"{y}年分",
                                 "年末調整の書類は12月の給与計算までに回収する"))
        if client.get("depreciable_assets"):
            out.append(_deadline(client, "償却資産申告", date(y + 1, 1, 31), "償却資産", f"{y + 1}年度"))
    return out
