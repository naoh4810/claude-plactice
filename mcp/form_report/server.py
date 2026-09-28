"""フォーム回答 → 帳票MCP：Google フォームの回答から、個人別の記録や集計レポートを作る。

「回答を印刷して書き写す」作業をなくすためのデモ。Claude が回答を読んで所見の下書きを作り、
人が確認したうえで印刷用 HTML（A4）と Excel 用 CSV に出力する。

できること（ツール）:
  - describe_form   : 質問一覧と、それぞれの種類（数値 / 選択肢 / 自由記述）
  - list_people     : 対象者ごとの回答件数と最終回答日
  - get_responses   : 対象者・期間で絞った回答
  - summarize       : 対象者・期間の集計（平均、選択肢の件数、自由記述の一覧）
  - create_report   : 印刷用 HTML の帳票を保存（所見は Claude の下書きを人が確認してから渡す）
  - export_csv      : Excel で開ける CSV を保存

起動:
  python mcp/form_report/server.py
  （FORM_SHEET_ID と GOOGLE_APPLICATION_CREDENTIALS が無ければ架空データのサンプルモード）
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from report import aggregate, classify, render_csv, render_html
from store import TIMESTAMP_COL, BaseStore, Response, SampleStore, open_store, parse_date

mcp = MCPServer("form-report")
store: BaseStore = open_store()

PERSON_CANDIDATES = ("利用者名", "氏名", "お名前", "名前", "生徒名")


def _output_dir() -> Path:
    path = Path(os.environ.get("FORM_REPORT_DIR", "reports"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _person_column() -> str:
    configured = os.environ.get("FORM_PERSON_COLUMN")
    questions = store.questions()
    if configured:
        if configured not in questions:
            raise ValueError(f"FORM_PERSON_COLUMN「{configured}」がフォームの質問にありません: {questions}")
        return configured
    for name in PERSON_CANDIDATES:
        if name in questions:
            return name
    raise ValueError(f"対象者の列が見つかりません。FORM_PERSON_COLUMN で質問名を指定してください: {questions}")


def _parse_arg(value: str | None, label: str) -> date | None:
    if not value:
        return None
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"{label}「{value}」を日付として読めません（例: 2026-09-01）")
    return parsed


def _select(person: str | None, date_from: str | None, date_to: str | None) -> tuple[list[Response], date | None, date | None]:
    start, end = _parse_arg(date_from, "date_from"), _parse_arg(date_to, "date_to")
    responses = store.responses()
    if person:
        column = _person_column()
        responses = [r for r in responses if r.answers.get(column) == person]
        if not responses:
            known = sorted({r.answers.get(column, "") for r in store.responses()} - {""})
            raise ValueError(f"「{person}」の回答がありません。登録されている名前: {known}")
    if start:
        responses = [r for r in responses if r.date and r.date >= start]
    if end:
        responses = [r for r in responses if r.date and r.date <= end]
    return responses, start, end


def _safe_filename(*parts: str) -> str:
    return "_".join(re.sub(r'[\\/:*?"<>|\s]+', "-", p).strip("-") for p in parts if p)


def _data_questions() -> list[str]:
    person = _person_column()
    return [q for q in store.questions() if q and q not in (TIMESTAMP_COL, person)]


def _kinds() -> dict[str, str]:
    """質問の種類は、絞り込む前の全回答で判定する（少数だと選択肢が自由記述に見えるため）。"""
    responses = store.responses()
    return {q: classify(q, responses) for q in store.questions() if q}


@mcp.tool()
def describe_form() -> dict:
    """フォームの質問一覧と種類、回答件数、期間を返す。最初にこれでフォームの形を把握する。"""
    responses = store.responses()
    dates = [r.date for r in responses if r.date]
    try:
        person = _person_column()
    except ValueError:
        person = None
    return {
        "mode": "sample" if isinstance(store, SampleStore) else "google_sheet",
        "form": store.title,
        "person_column": person,
        "responses": len(responses),
        "period": [min(dates).isoformat(), max(dates).isoformat()] if dates else None,
        "questions": {q: classify(q, responses) for q in store.questions() if q and q not in (TIMESTAMP_COL, person)},
    }


@mcp.tool()
def list_people() -> list[dict]:
    """対象者（利用者・生徒など）ごとの回答件数と最終回答日。"""
    column = _person_column()
    responses = store.responses()
    counts = Counter(r.answers.get(column, "") for r in responses)
    last: dict[str, date] = {}
    for r in responses:
        name = r.answers.get(column, "")
        if r.date and (name not in last or r.date > last[name]):
            last[name] = r.date
    return [
        {"name": name, "responses": n, "last_date": last[name].isoformat() if name in last else None}
        for name, n in counts.most_common() if name
    ]


@mcp.tool()
def get_responses(person: str | None = None, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """対象者・期間で絞った回答をそのまま返す。所見を書く前に中身を読むのに使う。

    Args:
        person: 対象者の名前（省略で全員）
        date_from: この日以降（例: 2026-09-01）
        date_to: この日以前（例: 2026-09-30）
    """
    responses, _, _ = _select(person, date_from, date_to)
    return [{"row": r.row, **{k: v for k, v in r.answers.items() if v}} for r in responses]


@mcp.tool()
def summarize(
    person: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    question: str | None = None,
) -> dict:
    """対象者・期間の集計。数値は平均・最小・最大、選択肢は件数、自由記述は日付付きの一覧。

    Args:
        person: 対象者の名前（省略で全員）
        date_from: この日以降（例: 2026-09-01）
        date_to: この日以前（例: 2026-09-30）
        question: 1つの質問だけ集計したいときの質問文
    """
    responses, _, _ = _select(person, date_from, date_to)
    questions = [question] if question else _data_questions()
    unknown = [q for q in questions if q not in store.questions()]
    if unknown:
        raise ValueError(f"フォームに無い質問です: {unknown}")
    kinds = _kinds()
    return {"responses": len(responses), "questions": [aggregate(q, responses, kinds[q]) for q in questions]}


@mcp.tool()
def create_report(
    person: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    title: str | None = None,
    comment: str = "",
) -> dict:
    """印刷用（A4）の HTML 帳票を保存し、パスを返す。ブラウザで開いて印刷すれば PDF にもできる。

    person を指定すると個人の記録（例: 支援記録）、省略すると全員分の集計レポートになる。
    comment（所見）は、get_responses / summarize で読んだ事実だけをもとに Claude が下書きし、
    ユーザーに見せて確認を取ってから渡すこと。診断や推測は書かない。

    Args:
        person: 対象者の名前（省略で全員）
        date_from: この日以降（例: 2026-09-01）
        date_to: この日以前（例: 2026-09-30）
        title: 帳票のタイトル（省略時は「個別記録」または「集計レポート」）
        comment: 所見の本文。空なら手書き用の空欄になる
    """
    responses, start, end = _select(person, date_from, date_to)
    if not responses:
        raise ValueError("指定した条件の回答がありません")
    title = title or ("個別記録" if person else "集計レポート")
    today = date.today()
    html_text = render_html(
        title=title,
        organization=os.environ.get("FORM_ORGANIZATION", ""),
        person_column=_person_column(),
        person=person,
        date_from=start,
        date_to=end,
        responses=responses,
        questions=store.questions(),
        kinds=_kinds(),
        comment=comment,
        created=today,
    )
    path = _output_dir() / f"{_safe_filename(today.isoformat(), person or '全員', title)}.html"
    path.write_text(html_text, encoding="utf-8")
    return {"path": str(path.resolve()), "responses": len(responses), "has_comment": bool(comment.strip())}


@mcp.tool()
def export_csv(person: str | None = None, date_from: str | None = None, date_to: str | None = None) -> dict:
    """対象者・期間の回答を、Excel でそのまま開ける CSV（BOM 付き UTF-8）で保存する。

    Args:
        person: 対象者の名前（省略で全員。その場合は名前の列も含める）
        date_from: この日以降（例: 2026-09-01）
        date_to: この日以前（例: 2026-09-30）
    """
    responses, _, _ = _select(person, date_from, date_to)
    columns = _data_questions()
    if person is None:
        columns = [_person_column(), *columns]
    path = _output_dir() / f"{_safe_filename(date.today().isoformat(), person or '全員', '回答一覧')}.csv"
    path.write_text(render_csv(responses, columns), encoding="utf-8-sig")
    return {"path": str(path.resolve()), "rows": len(responses)}


@mcp.prompt()
def monthly_record(person: str, date_from: str, date_to: str) -> str:
    """1人分の期間の記録から、所見付きの帳票を作る流れ。"""
    return (
        f"「{person}」さんの {date_from} 〜 {date_to} の記録を帳票にします。\n"
        "1. summarize と get_responses で、期間の回答を読んでください。\n"
        "2. 所見の下書きを3〜5文で作ってください。回答に書かれた事実（体調の傾向、参加した活動、"
        "記入者が気づいたこと）だけを使い、診断・推測・回答に無い情報は書かないでください。\n"
        "3. 下書きをユーザーに見せ、修正があれば反映してください。\n"
        "4. 確認が取れたら create_report に comment として渡し、保存先のパスを伝えてください。"
    )


if __name__ == "__main__":
    mcp.run()
