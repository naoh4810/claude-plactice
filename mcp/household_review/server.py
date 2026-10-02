"""家計見直しMCP：銀行・カードの明細から支出を分類し、固定費・見直し候補・削減のシミュレーションを出す。

FP（ファイナンシャルプランナー）が相談者の家計を見直すときの下準備を引き受けるデモ。
明細の読み込み・分類・集計・シミュレーション・報告書づくりまでで、
特定の保険・金融商品を勧めることはしない（提案は FP が行う）。

できること（ツール）:
  - overview          : 対象期間・取引数・月平均の収入と支出・未分類の数
  - import_statement  : 明細 CSV を取り込む（重複は飛ばす）
  - add_transactions  : PDF・紙の明細を Claude が読み取った取引を追加する
  - uncategorized     : 分類できなかった取引（摘要ごとの件数と金額）
  - set_category      : 「この言葉を含む取引はこの分類」というルールを追加
  - monthly_summary   : 月ごと・分類ごとの支出と、月平均
  - fixed_costs       : 毎月続いている支払い（固定費・サブスク）
  - review_points     : 見直しの候補（サブスク・手数料・通信・保険・現金引き出し など）と事実
  - simulate          : 見直し案ごとの月額・年額・累計（運用を仮定する場合は利回りを前提として明記）
  - render_report     : A4 の報告書（HTML）を保存

起動:
  python mcp/household_review/server.py
  （HOUSEHOLD_DATA_PATH が無ければ、架空の家庭の明細3か月分を読み込んだサンプル）
"""

from __future__ import annotations

import html
import os
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from ledger import CATEGORIES, EXCLUDED, Household, Txn, categorize, decode, normalize

HERE = Path(__file__).parent
SAMPLE_FILES = sorted((HERE / "sample").glob("*.csv"))

mcp = MCPServer("household-review")


def _open() -> Household:
    path = os.environ.get("HOUSEHOLD_DATA_PATH")
    return Household(Path(path)) if path else Household(None, SAMPLE_FILES)


book: Household = _open()


def _expenses() -> list[Txn]:
    return [t for t in book.txns if t.direction == "out" and t.category not in EXCLUDED]


def _income() -> list[Txn]:
    return [t for t in book.txns if t.direction == "in" and t.category == "収入"]


def _months() -> int:
    return max(1, len(book.months()))


def _per_month(total: int) -> int:
    return round(total / _months())


def _by_category() -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for t in _expenses():
        totals[t.category or "（未分類）"] += t.amount
    return dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))


@mcp.tool()
def overview() -> dict:
    """対象期間・取引数・月平均の収入と支出（カード引き落としなどの振替は除く）・未分類の数。"""
    income, spend = sum(t.amount for t in _income()), sum(t.amount for t in _expenses())
    return {
        "mode": "sample" if book.path is None else "file",
        "household": book.data.get("household", ""),
        "months": book.months(),
        "transactions": len(book.txns),
        "monthly_income": _per_month(income),
        "monthly_spending": _per_month(spend),
        "monthly_balance": _per_month(income - spend),
        "excluded_transfers": sum(t.amount for t in book.txns if t.category in EXCLUDED),
        "uncategorized": sum(1 for t in book.txns if not t.category),
    }


@mcp.tool()
def import_statement(path: str) -> dict:
    """銀行・カードの明細 CSV（UTF-8 / Shift_JIS）を取り込む。同じ取引は二重に入らない。"""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"ファイルがありません: {path}")
    return book.import_text(decode(p.read_bytes()), p.name)


@mcp.tool()
def add_transactions(transactions: list[dict], source: str = "手入力") -> dict:
    """PDF・紙・画面の明細を読み取った取引を追加する。

    1件ずつ {"date": "2026-09-05", "description": "サンプルスーパー", "amount": 3200, "direction": "out"} の形で渡す。
    direction は out（支出）か in（収入・返金）。読み取れなかった金額は推測で埋めず、ユーザーに確認すること。
    """
    added, errors = 0, []
    seen = {t.key for t in book.txns}
    for n, item in enumerate(transactions, start=1):
        try:
            t = Txn(Household.valid_date(str(item["date"])), str(item["description"]).strip(), abs(int(item["amount"])),
                    item.get("direction", "out"), source)
        except (KeyError, ValueError, TypeError) as e:
            errors.append(f"{n}件目: 読めません（{e}）")
            continue
        if t.direction not in ("in", "out") or t.amount == 0:
            errors.append(f"{n}件目: direction は in/out、amount は0以外にしてください")
            continue
        if t.key in seen:
            continue
        t.category = categorize(t, book.data["rules"])
        book.txns.append(t)
        seen.add(t.key)
        added += 1
    book.save()
    return {"added": added, "errors": errors}


@mcp.tool()
def uncategorized(limit: int = 30) -> list[dict]:
    """分類できなかった取引を摘要ごとにまとめて返す。set_category で分類を決める材料にする。"""
    groups: dict[str, dict] = {}
    for t in book.txns:
        if t.category:
            continue
        g = groups.setdefault(t.description, {"description": t.description, "direction": t.direction, "count": 0, "total": 0})
        g["count"] += 1
        g["total"] += t.amount
    return sorted(groups.values(), key=lambda g: g["total"], reverse=True)[:limit]


@mcp.tool()
def set_category(keyword: str, category: str) -> dict:
    """摘要に keyword を含む取引を category に分類するルールを追加し、全体を分類し直す。

    category は 収入・住居・水道光熱・通信・保険・食費・外食・日用品・車・交通・教育・医療・サブスク・
    趣味・娯楽・旅行・レジャー・買い物・手数料・現金引き出し・その他・振替（集計外）のどれか。
    自分の口座どうしの移動やカード代金の引き落としは「振替（集計外）」にすると二重に数えない。
    """
    if category not in CATEGORIES:
        raise ValueError(f"category は {' / '.join(CATEGORIES)} のどれかです")
    if not keyword.strip():
        raise ValueError("keyword が空です")
    book.data["rules"] = [r for r in book.data["rules"] if normalize(r[0]) != normalize(keyword)]
    book.data["rules"].insert(0, [keyword.strip(), category])
    book.recategorize()
    book.save()
    matched = [t for t in book.txns if normalize(keyword) in normalize(t.description)]
    return {"keyword": keyword, "category": category, "matched_transactions": len(matched),
            "still_uncategorized": sum(1 for t in book.txns if not t.category)}


@mcp.tool()
def monthly_summary() -> dict:
    """月ごと・分類ごとの支出と収入、月平均。振替（カード引き落とし等）は支出に含めない。"""
    months = book.months()
    table: dict[str, dict[str, int]] = defaultdict(lambda: {m: 0 for m in months})
    for t in _expenses():
        table[t.category or "（未分類）"][t.month] += t.amount
    income = {m: sum(t.amount for t in _income() if t.month == m) for m in months}
    spend = {m: sum(t.amount for t in _expenses() if t.month == m) for m in months}
    return {
        "months": months,
        "income": income,
        "spending": spend,
        "balance": {m: income[m] - spend[m] for m in months},
        "by_category": {cat: {**vals, "monthly_average": _per_month(sum(vals.values()))}
                        for cat, vals in sorted(table.items(), key=lambda kv: -sum(kv[1].values()))},
    }


def _recurring() -> list[dict]:
    """2か月以上続けて出ていて、金額のばらつきが中央値の±15%に収まる支払い。"""
    groups: dict[str, list[Txn]] = defaultdict(list)
    for t in _expenses():
        if t.category == "現金引き出し":      # 毎月同じ額でも固定費ではない（使い道は review_points で聞く）
            continue
        groups[normalize(t.description)].append(t)
    rows = []
    for items in groups.values():
        months = {t.month for t in items}
        if len(months) < 2:
            continue
        amounts = [t.amount for t in items]
        median = statistics.median(amounts)
        if all(abs(a - median) <= median * 0.15 for a in amounts) and len(items) <= len(months) * 2:
            rows.append({"description": items[0].description, "category": items[0].category or "（未分類）",
                         "monthly": round(sum(amounts) / len(months)), "months": len(months)})
    return sorted(rows, key=lambda r: r["monthly"], reverse=True)


@mcp.tool()
def fixed_costs() -> dict:
    """毎月続いている支払い（固定費・サブスク）と合計、手取り収入に占める割合。"""
    rows = _recurring()
    total = sum(r["monthly"] for r in rows)
    income = _per_month(sum(t.amount for t in _income()))
    return {"items": rows, "monthly_total": total,
            "share_of_income": f"{total / income * 100:.0f}%" if income else None}


@mcp.tool()
def review_points() -> list[dict]:
    """見直しの候補を、根拠になる数字と一緒に返す。どれを提案するかは FP が判断する。"""
    cats = {k: _per_month(v) for k, v in _by_category().items()}
    income = _per_month(sum(t.amount for t in _income()))
    recurring = _recurring()
    subs = [r for r in recurring if r["category"] in ("サブスク", "趣味・娯楽")]
    points = []
    if subs:
        points.append({"topic": "サブスク・会費", "monthly": sum(r["monthly"] for r in subs),
                       "facts": [f"{r['description']} 月{r['monthly']:,}円" for r in subs],
                       "question": "使っていないもの、重複しているもの（動画配信が複数など）はないか"})
    if cats.get("手数料"):
        points.append({"topic": "手数料", "monthly": cats["手数料"], "facts": [f"年間で約{cats['手数料'] * 12:,}円"],
                       "question": "ATM の時間帯・回数、振込の方法で減らせないか"})
    if cats.get("通信"):
        points.append({"topic": "通信費", "monthly": cats["通信"],
                       "facts": [f"{r['description']} 月{r['monthly']:,}円" for r in recurring if r["category"] == "通信"],
                       "question": "今の使い方（データ量・通話）に対してプランが大きすぎないか"})
    if cats.get("保険"):
        share = f"{cats['保険'] / income * 100:.0f}%" if income else "不明"
        points.append({"topic": "保険料", "monthly": cats["保険"], "facts": [f"手取り収入の{share}"] +
                       [f"{r['description']} 月{r['monthly']:,}円" for r in recurring if r["category"] == "保険"],
                       "question": "保障の内容が今の家族構成・貯蓄と合っているか、重複していないか（個別の商品の判断は FP が行う）"})
    if cats.get("現金引き出し"):
        points.append({"topic": "現金引き出し", "monthly": cats["現金引き出し"], "facts": ["使い道が明細に残らない支出"],
                       "question": "何に使っているか。キャッシュレスにして見えるようにできないか"})
    if cats.get("外食"):
        points.append({"topic": "外食", "monthly": cats["外食"], "facts": [f"食費（自炊）は月{cats.get('食費', 0):,}円"],
                       "question": "回数と1回あたりの金額、どちらを変えるか"})
    return points


@mcp.tool()
def simulate(changes: list[dict], years: int = 10, annual_return: float = 0.0) -> dict:
    """見直し案ごとの削減額と、積み上げた場合の累計を出す。

    changes は [{"label": "スマホを格安プランに", "monthly_saving": 5000}, ...] の形。
    annual_return を 0 より大きくすると、浮いた分を毎月積み立てて年利で運用した場合の試算になる
    （例: 0.03 は年3%。将来の運用成果を約束するものではない前提として必ず添えること）。
    """
    if not changes:
        raise ValueError("changes が空です")
    if not 0 <= annual_return <= 0.1:
        raise ValueError("annual_return は 0〜0.1（10%）の範囲にしてください")
    if not 1 <= years <= 40:
        raise ValueError("years は 1〜40 にしてください")
    for c in changes:
        if int(c.get("monthly_saving", 0)) <= 0:
            raise ValueError(f"monthly_saving は正の数にしてください: {c}")
    monthly = sum(int(c["monthly_saving"]) for c in changes)
    r = annual_return / 12
    balance, rows = 0.0, []
    for year in range(1, years + 1):
        for _ in range(12):
            balance = balance * (1 + r) + monthly
        rows.append({"year": year, "saved": monthly * 12 * year, "with_return": round(balance)})
    return {
        "changes": [{"label": c["label"], "monthly_saving": int(c["monthly_saving"]),
                     "annual_saving": int(c["monthly_saving"]) * 12} for c in changes],
        "monthly_total": monthly, "annual_total": monthly * 12,
        "annual_return": annual_return,
        "by_year": rows if annual_return else [{"year": r_["year"], "saved": r_["saved"]} for r_ in rows],
        "assumption": (f"浮いた分を毎月積み立て、年{annual_return * 100:g}%で運用できた場合の試算。運用成果を約束するものではありません"
                       if annual_return else "運用はしない（積み上げるだけ）前提"),
    }


CSS = """
@page { size: A4; margin: 14mm; }
body { font-family: "Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans JP", sans-serif; color: #111; font-size: 10pt; }
h1 { font-size: 16pt; border-bottom: 2px solid #111; padding-bottom: 2mm; margin: 0 0 4mm; }
h2 { font-size: 11.5pt; border-left: 4px solid #111; padding-left: 2mm; margin: 6mm 0 2mm; }
table { width: 100%; border-collapse: collapse; }
th, td { border: 1px solid #555; padding: 1.2mm 2mm; vertical-align: top; }
th { background: #eee; text-align: left; }
td.num { text-align: right; white-space: nowrap; }
.big { font-size: 14pt; }
.note { font-size: 8.5pt; color: #444; }
@media screen { body { max-width: 190mm; margin: 10mm auto; } }
"""


@mcp.tool()
def render_report(changes: list[dict], years: int = 10, annual_return: float = 0.0, client: str = "") -> dict:
    """家計の現状・固定費・見直しの候補・シミュレーションを A4 の報告書（HTML）にまとめて保存する。

    changes は simulate と同じ形。内容は相談者と FP が合意したものだけを入れること。
    """
    esc = html.escape
    ov, sim, fixed = overview(), simulate(changes, years, annual_return), fixed_costs()
    cats = monthly_summary()["by_category"]
    cat_rows = "".join(f"<tr><td>{esc(c)}</td><td class='num'>{v['monthly_average']:,} 円</td></tr>" for c, v in cats.items())
    fixed_rows = "".join(f"<tr><td>{esc(r['description'])}</td><td>{esc(r['category'])}</td><td class='num'>{r['monthly']:,} 円</td></tr>"
                         for r in fixed["items"])
    change_rows = "".join(f"<tr><td>{esc(c['label'])}</td><td class='num'>{c['monthly_saving']:,} 円</td>"
                          f"<td class='num'>{c['annual_saving']:,} 円</td></tr>" for c in sim["changes"])
    pick = [y for y in sim["by_year"] if y["year"] in (1, 3, 5, 10, 20, 30, years)]
    year_head = "<th>累計（積み上げ）</th>" + ("<th>運用した場合</th>" if annual_return else "")
    year_rows = "".join(f"<tr><td>{y['year']}年後</td><td class='num'>{y['saved']:,} 円</td>"
                        + (f"<td class='num'>{y['with_return']:,} 円</td>" if annual_return else "") + "</tr>" for y in pick)
    page = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>家計見直しのご提案</title><style>{CSS}</style></head><body>
<h1>家計見直しのご提案{f'　{esc(client)}' if client else ''}</h1>
<p>対象期間：{esc(ov['months'][0])} 〜 {esc(ov['months'][-1])}（{len(ov['months'])}か月）　作成日：{date.today():%Y年%m月%d日}</p>
<h2>現在の家計（月平均）</h2>
<table><tr><th>手取り収入</th><td class='num'>{ov['monthly_income']:,} 円</td><th>支出</th><td class='num'>{ov['monthly_spending']:,} 円</td>
<th>収支</th><td class='num'>{ov['monthly_balance']:,} 円</td></tr></table>
<p class="note">カード代金の引き落としなど、口座間の移動（月平均 {_per_month(ov['excluded_transfers']):,} 円）は二重計上を避けるため支出に含めていません。</p>
<h2>分類ごとの支出（月平均）</h2><table>{cat_rows}</table>
<h2>毎月の固定費</h2><table><tr><th>支払先</th><th>分類</th><th>月額</th></tr>{fixed_rows}
<tr><th colspan="2">合計（手取りの {esc(str(fixed['share_of_income']))}）</th><td class='num'>{fixed['monthly_total']:,} 円</td></tr></table>
<h2>見直し案</h2><table><tr><th>内容</th><th>月の削減額</th><th>年の削減額</th></tr>{change_rows}
<tr><th>合計</th><td class='num big'>{sim['monthly_total']:,} 円</td><td class='num big'>{sim['annual_total']:,} 円</td></tr></table>
<h2>続けた場合</h2><table><tr><th></th>{year_head}</tr>{year_rows}</table>
<p class="note">{esc(sim['assumption'])}。金額は明細の実績から計算した目安です。保険・金融商品の選択は、内容を確認したうえでご判断ください。</p>
</body></html>
"""
    out_dir = Path(os.environ.get("HOUSEHOLD_EXPORT_DIR", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"家計見直し_{date.today().isoformat()}.html"
    path.write_text(page, encoding="utf-8")
    return {"path": str(path.resolve()), "monthly_total": sim["monthly_total"], "annual_total": sim["annual_total"]}


@mcp.prompt()
def review_session(client: str = "") -> str:
    """家計見直しの面談の下準備。"""
    return (
        f"{client + ' の' if client else ''}家計見直しの下準備をします。\n"
        "1. overview で期間・収入・支出を確認し、uncategorized があれば分類を提案して、ユーザーの了承を得て set_category で登録してください。\n"
        "2. monthly_summary と fixed_costs で、支出の大きい分類と固定費を確認してください。\n"
        "3. review_points の候補ごとに、事実（金額）と、面談で相談者に聞くことを整理してください。"
        "特定の保険・金融商品やプランの名前を挙げて勧めないでください。\n"
        "4. ユーザー（FP）と見直し案と削減額を決めたら simulate で試算し、render_report で報告書を作ってください。"
        "運用を仮定する場合は、利回りが前提であることを必ず添えてください。"
    )


if __name__ == "__main__":
    mcp.run()
