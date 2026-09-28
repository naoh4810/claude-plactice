"""営業シートMCP：交流会・マッチングアプリで出会った人の営業ログを Claude から扱う。

できること（ツール）:
  - list_sheets        : タブ一覧と列構成
  - search_contacts    : 名前・事業名・メモのキーワード検索
  - stalled_contacts   : 進捗が止まっている人と、次の一手の候補
  - pipeline_summary   : 進捗の内訳と、交流会ごとの面談化率
  - add_contact        : 1人分を追記（名刺の写真は Claude が読み取ってからこれを呼ぶ）
  - update_contact     : 進捗・結果の更新とメモの追記

起動:
  python mcp/sales_sheet/server.py
  （SALES_SHEET_ID と GOOGLE_APPLICATION_CREDENTIALS が無ければ架空データのサンプルモード）
"""

from __future__ import annotations

from collections import Counter, defaultdict

from mcp.server.fastmcp import FastMCP

from store import NAME_COL, BaseStore, Contact, SampleStore, open_store

mcp = FastMCP("sales-sheet")
store: BaseStore = open_store()

CLOSED = {"否決", "NG", "失注"}
# 進捗の段階。数字が大きいほど先に進んでいる
STAGES = {
    "名刺配布のみ": 1,
    "面談予定": 2,
    "オフラインアポ": 2,
    "オンラインアポ": 2,
    "1回目商談": 3,
    "1回目面談": 3,
    "1対1面談": 3,
}
NEXT_STEP = {
    0: "進捗が未記入。まず状況をシートに記録する",
    1: "お礼の連絡に、相手の業種に合わせた一言提案を添えて面談を打診する",
    2: "日程を確定し、相手の業種に合わせたデモを事前に用意する",
    3: "1回目で聞いた悩みに合わせた提案・デモを持って2回目を設定する",
}
NOTE_COLS = ("内容・感想", "メモ")


def _stage(c: Contact) -> int:
    return STAGES.get(c.fields.get("進捗", ""), 0)


def _is_closed(c: Contact) -> bool:
    return c.fields.get("進捗", "") in CLOSED or c.fields.get("結果", "") in CLOSED


def _note(c: Contact) -> str:
    return " / ".join(c.fields[k] for k in NOTE_COLS if c.fields.get(k))


@mcp.tool()
def list_sheets() -> dict:
    """営業シートのタブ名と、それぞれの列名を返す。どのタブに書くか迷ったらまずこれを見る。"""
    store.refresh()
    return {
        "mode": "sample" if isinstance(store, SampleStore) else "google_sheet",
        "sheets": {name: store.header(name) for name in store.sheet_names()},
    }


@mcp.tool()
def search_contacts(query: str = "", sheet: str | None = None, progress: str | None = None) -> list[dict]:
    """名前・事業名・メモなど全列を対象にキーワード検索する。

    Args:
        query: 部分一致で探す文字列。空なら全件
        sheet: タブ名で絞り込む（例: 交流会）。省略で全タブ
        progress: 進捗の値で絞り込む（例: 名刺配布のみ）
    """
    store.refresh()
    hits = []
    for c in store.contacts(sheet):
        if progress and c.fields.get("進捗") != progress:
            continue
        if query and not any(query in v for v in c.fields.values()):
            continue
        hits.append(c.as_dict())
    return hits


@mcp.tool()
def stalled_contacts(sheet: str | None = None) -> list[dict]:
    """否決・NG になっていない人を、同じ名前はまとめて一番進んでいる行で返す。

    段階が手前の人（名刺配布のみ）から順に並べ、次の一手の候補を付ける。
    フォロー漏れの洗い出しや、今週連絡する人の選定に使う。
    """
    store.refresh()
    by_name: dict[str, list[Contact]] = defaultdict(list)
    for c in store.contacts(sheet):
        by_name[c.name].append(c)

    result = []
    for name, rows in by_name.items():
        if any(_is_closed(r) for r in rows):
            continue
        best = max(rows, key=_stage)
        result.append({
            "name": name,
            "stage": best.fields.get("進捗", "") or "（未記入）",
            "next_step": NEXT_STEP[_stage(best)],
            "business": next((r.fields["事業名"] for r in rows if r.fields.get("事業名")), ""),
            "notes": [n for n in (_note(r) for r in rows) if n],
            "rows": [{"sheet": r.sheet, "row": r.row} for r in rows],
        })
    return sorted(result, key=lambda x: STAGES.get(x["stage"], 0))


@mcp.tool()
def pipeline_summary() -> dict:
    """タブごとの進捗の内訳と、交流会ごとの「面談以上に進んだ人数 / 会った人数」を返す。"""
    store.refresh()
    contacts = store.contacts()
    by_sheet = defaultdict(Counter)
    for c in contacts:
        by_sheet[c.sheet][c.fields.get("進捗", "") or "（未記入）"] += 1

    events: dict[str, dict] = defaultdict(lambda: {"met": 0, "meeting_or_later": 0})
    for c in contacts:
        event = c.fields.get("交流会")
        if not event:
            continue
        events[event]["met"] += 1
        if _stage(c) >= 2:
            events[event]["meeting_or_later"] += 1

    return {"progress_by_sheet": {k: dict(v) for k, v in by_sheet.items()}, "by_event": dict(events)}


@mcp.tool()
def add_contact(
    name: str,
    sheet: str = "交流会",
    date: str = "",
    event: str = "",
    business: str = "",
    progress: str = "名刺配布のみ",
    note: str = "",
    email: str = "",
    phone: str = "",
    allow_duplicate: bool = False,
) -> dict:
    """1人分を指定タブの空き行に書き込む。

    名刺の写真を受け取ったときは、Claude が画像から名前・事業名・連絡先を読み取り、
    内容をユーザーに確認してからこのツールを呼ぶこと。
    タブに無い列（例: 営業タブの「交流会」）は書き込まずに skipped として返す。

    Args:
        name: お客さまの名前
        sheet: 書き込むタブ（営業 / 交流会 / マッチングアプリ）
        date: 日時（例: 9/28）
        event: 交流会の名前
        business: 事業名・会社名
        progress: 進捗（名刺配布のみ / 面談予定 / 1回目商談 など）
        note: 内容・感想やメモ
        email: メールアドレス
        phone: 電話番号
        allow_duplicate: 同じ名前が既にあっても書き込むなら true
    """
    store.refresh()
    existing = [c.as_dict() for c in store.contacts(sheet) if c.name == name]
    if existing and not allow_duplicate:
        return {"written": False, "reason": "同じ名前が既にあります。追記なら update_contact を使う", "existing": existing}

    header = store.header(sheet)
    note_col = next((k for k in NOTE_COLS if k in header), None)
    wanted = {
        NAME_COL: name, "日時": date, "交流会": event, "事業名": business, "進捗": progress,
        note_col or "メモ": note, "メールアドレス": email, "電話番号": phone,
    }
    values = {k: v for k, v in wanted.items() if v and k in header}
    skipped = {k: v for k, v in wanted.items() if v and k not in header}
    contact = store.append_contact(sheet, values)
    return {"written": True, "contact": contact.as_dict(), "skipped": skipped}


@mcp.tool()
def update_contact(
    sheet: str,
    row: int,
    progress: str | None = None,
    result: str | None = None,
    note_append: str | None = None,
) -> dict:
    """既存の行の進捗・結果を更新し、メモに追記する（上書きではなく改行して足す）。

    Args:
        sheet: タブ名
        row: 行番号（search_contacts / stalled_contacts の row）
        progress: 新しい進捗
        result: 新しい結果（検討 / 否決 など。結果列があるタブのみ）
        note_append: メモに追記する文
    """
    store.refresh()
    current = next((c for c in store.contacts(sheet) if c.row == row), None)
    if current is None:
        raise ValueError(f"シート「{sheet}」の {row} 行目にお客さまが見つかりません")

    header = store.header(sheet)
    values: dict[str, str] = {}
    if progress:
        values["進捗"] = progress
    if result:
        if "結果" not in header:
            raise ValueError(f"シート「{sheet}」には結果列がありません")
        values["結果"] = result
    if note_append:
        note_col = next((k for k in NOTE_COLS if k in header), None)
        if note_col is None:
            raise ValueError(f"シート「{sheet}」にはメモ列がありません")
        old = current.fields.get(note_col, "")
        values[note_col] = f"{old}\n{note_append}" if old else note_append
    if not values:
        return {"updated": False, "contact": current.as_dict()}
    return {"updated": True, "contact": store.update_contact(sheet, row, values).as_dict()}


@mcp.prompt()
def followup_message(name: str) -> str:
    """指定した人へのフォロー連絡（LINE想定）の下書きを作らせるプロンプト。"""
    return (
        f"営業シートから「{name}」さんを search_contacts で探し、進捗・事業名・メモを確認してください。\n"
        "そのうえで、LINE で送るフォロー連絡の下書きを1通作ってください。\n"
        "- 会った場所と、話した内容に一言触れる\n"
        "- 相手の業種でよくある手作業を1つ挙げ、AIでどう楽になるかを1文で添える\n"
        "- 30分の無料相談（オンライン可）を、押し付けずに提案する\n"
        "- 200字以内、敬語だが堅すぎない文面\n"
        "送信はしないでください。下書きだけ提示し、送った後に update_contact でメモに残すか確認してください。"
    )


@mcp.prompt()
def business_card_intake() -> str:
    """名刺の写真から営業シートに登録する流れのプロンプト。"""
    return (
        "添付された名刺の写真から、名前・会社名/事業名・メールアドレス・電話番号を読み取ってください。\n"
        "読み取れなかった項目は空欄にし、推測で埋めないでください。\n"
        "読み取り結果を表で見せ、どの交流会で会ったか・一言メモをユーザーに聞いてから、"
        "add_contact で「交流会」タブに登録してください。"
    )


if __name__ == "__main__":
    mcp.run()
