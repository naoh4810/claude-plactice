"""回答の集計と、帳票（印刷用 HTML / Excel 用 CSV）の作成。

質問の種類は回答の中身から判定する:
  - number : すべて数値（睡眠時間、点数など）→ 平均・最小・最大
  - choice : 選択肢が少なく繰り返し出てくる（体調、食事量など）→ 件数
             チェックボックスの複数回答（「A, B」）は分けて数える
  - text   : それ以外の自由記述 → 日付付きで一覧
"""

from __future__ import annotations

import csv
import html
import io
from collections import Counter
from datetime import date

from store import TIMESTAMP_COL, Response

MAX_CHOICES = 8
MULTI_SEP = ", "


def _to_number(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def classify(question: str, responses: list[Response]) -> str:
    values = [r.answers.get(question, "") for r in responses]
    values = [v for v in values if v]
    if not values:
        return "text"
    if all(_to_number(v) is not None for v in values):
        return "number"
    pieces = [p for v in values for p in v.split(MULTI_SEP)]
    if len(set(pieces)) <= MAX_CHOICES and len(set(pieces)) < len(pieces):
        return "choice"
    return "text"


def aggregate(question: str, responses: list[Response], kind: str | None = None) -> dict:
    kind = kind or classify(question, responses)
    values = [r.answers.get(question, "") for r in responses]
    answered = [v for v in values if v]
    result: dict = {"question": question, "type": kind, "answered": len(answered), "unanswered": len(values) - len(answered)}
    if kind == "number":
        nums = [n for n in (_to_number(v) for v in answered) if n is not None]
        if nums:
            result.update(average=round(sum(nums) / len(nums), 1), min=min(nums), max=max(nums))
    elif kind == "choice":
        result["counts"] = dict(Counter(p for v in answered for p in v.split(MULTI_SEP)))
    else:
        result["entries"] = [
            {"date": r.date.isoformat() if r.date else "", "text": r.answers[question]}
            for r in responses if r.answers.get(question)
        ]
    return result


def _format_summary(agg: dict) -> str:
    if agg["type"] == "number":
        if "average" not in agg:
            return "回答なし"
        return f"平均 {agg['average']} ／ 最小 {agg['min']:g} ／ 最大 {agg['max']:g}"
    if agg["type"] == "choice":
        return " ／ ".join(f"{k} {v}件" for k, v in agg["counts"].items())
    return f"記入 {agg['answered']}件"


def _day(r: Response) -> str:
    return r.date.strftime("%m/%d") if r.date else r.answers.get(TIMESTAMP_COL, "")


CSS = """
@page { size: A4; margin: 14mm; }
body { font-family: "Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans JP", sans-serif; color: #111; font-size: 10.5pt; }
h1 { font-size: 16pt; margin: 0 0 4mm; border-bottom: 2px solid #111; padding-bottom: 2mm; }
h2 { font-size: 11.5pt; margin: 6mm 0 2mm; border-left: 4px solid #111; padding-left: 2mm; }
table { width: 100%; border-collapse: collapse; }
th, td { border: 1px solid #555; padding: 1.5mm 2mm; vertical-align: top; text-align: left; }
th { background: #eee; white-space: nowrap; }
.meta { width: auto; }
.meta td { border: none; padding: 0.5mm 6mm 0.5mm 0; }
.comment { border: 1px solid #555; min-height: 30mm; padding: 2mm 3mm; white-space: pre-wrap; }
.empty { color: #888; }
.stamps { margin-top: 6mm; width: 60mm; margin-left: auto; }
.stamps td { height: 16mm; text-align: center; }
@media screen { body { max-width: 190mm; margin: 10mm auto; } }
"""


def render_html(
    *,
    title: str,
    organization: str,
    person_column: str,
    person: str | None,
    date_from: date | None,
    date_to: date | None,
    responses: list[Response],
    questions: list[str],
    kinds: dict[str, str],
    comment: str,
    created: date,
) -> str:
    esc = html.escape
    columns = [q for q in questions if q not in (TIMESTAMP_COL, person_column)]
    if person is None:
        columns = [person_column] + columns
    dates = [r.date for r in responses if r.date]
    period = f"{date_from or min(dates, default='')} 〜 {date_to or max(dates, default='')}"

    meta_rows = [("事業所", organization), ("対象者", person or "全員"), ("期間", period),
                 ("記録件数", f"{len(responses)}件"), ("作成日", created.isoformat())]
    meta = "".join(f"<tr><td>{esc(k)}</td><td>{esc(str(v))}</td></tr>" for k, v in meta_rows if v)

    summary_rows = []
    for q in columns:
        if q == person_column:
            continue
        agg = aggregate(q, responses, kinds.get(q))
        if agg["type"] != "text":
            summary_rows.append(f"<tr><th>{esc(q)}</th><td>{esc(_format_summary(agg))}</td></tr>")
    summary = f"<table>{''.join(summary_rows)}</table>" if summary_rows else '<p class="empty">集計できる項目はありません</p>'

    head = "".join(f"<th>{esc(c)}</th>" for c in ["日付", *columns])
    body = "".join(
        "<tr>" + f"<td>{esc(_day(r))}</td>" + "".join(f"<td>{esc(r.answers.get(c, ''))}</td>" for c in columns) + "</tr>"
        for r in responses
    )
    comment_html = esc(comment) if comment.strip() else '<span class="empty">（所見を記入）</span>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head><meta charset="utf-8"><title>{esc(title)}</title><style>{CSS}</style></head>
<body>
<h1>{esc(title)}</h1>
<table class="meta">{meta}</table>
<h2>集計</h2>
{summary}
<h2>記録一覧</h2>
<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
<h2>所見</h2>
<div class="comment">{comment_html}</div>
<table class="stamps"><tr><th>記入者</th><th>確認者</th></tr><tr><td></td><td></td></tr></table>
</body>
</html>
"""


def render_csv(responses: list[Response], columns: list[str]) -> str:
    """Excel でそのまま開けるよう BOM 付き UTF-8 で保存する前提の CSV 文字列。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["日付", *columns])
    for r in responses:
        writer.writerow([r.date.isoformat() if r.date else "", *(r.answers.get(c, "") for c in columns)])
    return buf.getvalue()
