"""士業の期限・書類MCP：顧問先ごとの申告・届出の期限と、必要書類の回収状況を管理する。

「顧問先から資料が揃わず、毎回催促に追われる」「期限が顧問先ごとにバラバラで把握しきれない」を解決するデモ。
期限は顧問先の条件（法人/個人・決算月・消費税・源泉の納期の特例・従業員など）から自動で出し、
休日なら翌営業日にずらす。登記など出来事から数える期限は手動で追加できる。催促文は Claude が下書きし、送るのは人。

期限は原則的な目安で、税務判断はしない。最終確認は事務所で行う前提。

できること（ツール）:
  - office_overview     : 顧問先数、30日以内の期限、書類が揃っていない件数
  - list_clients / add_client : 顧問先の一覧と登録
  - upcoming_deadlines  : 期間内の期限（期限切れで未完了のものも）。書類の不足数付き
  - document_status     : 顧問先の書類の束ごとの、受け取り済み・未着
  - mark_received / mark_done : 書類の受け取りと、期限の完了を記録
  - reminder_candidates : 期限が近いのに書類が揃っていない顧問先を急ぐ順に（最近催促した先は印を付ける）
  - record_reminder     : 催促したことを記録
  - add_task            : 出来事から数える期限などを手動で追加（例: 役員変更から2週間）

起動:
  python mcp/tax_office/server.py
  （OFFICE_DATA_PATH が無ければ架空の事務所のサンプルをメモリ上で使う）
"""

from __future__ import annotations

from datetime import date, timedelta

from mcp.server.mcpserver import MCPServer

from rules import Deadline, deadlines_for, next_business_day
from store import Office, open_office

mcp = MCPServer("tax-office")
office: Office = open_office()

OVERDUE_LOOKBACK_DAYS = 60
CLIENT_FLAGS = ("consumption_tax", "filing_extension", "consumption_tax_extension", "interim_filing",
                "employees", "depreciable_assets")


def _parse_day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{label}「{value}」を日付として読めません（例: 2026-10-09）") from None


def _all_deadlines(client_ref: str | None = None) -> list[Deadline]:
    today = office.today()
    clients = [office.client(client_ref)] if client_ref else office.data["clients"]
    ids = {c["id"] for c in clients}
    out = [d for c in clients for d in deadlines_for(c, range(today.year - 1, today.year + 2))]
    for t in office.data["custom"]:
        if t["client_id"] in ids:
            out.append(Deadline(t["client_id"], t["title"], date.fromisoformat(t["due"]), "個別", t["id"],
                                t.get("note", ""), list(t.get("documents", []))))
    return out


def _done_key(d: Deadline) -> str:
    return f"{d.key}|{d.title}"


def _missing(d: Deadline) -> list[str]:
    got = set(office.data["received"].get(d.key, []))
    return [doc for doc in d.documents if doc not in got]


def _name(client_id: str) -> str:
    return next(c["name"] for c in office.data["clients"] if c["id"] == client_id)


def _row(d: Deadline) -> dict:
    missing = _missing(d)
    return {
        "client": _name(d.client_id), "title": d.title, "due": d.due.isoformat(),
        "days_left": (d.due - office.today()).days, "period": d.period, "package": d.package,
        "done": _done_key(d) in office.data["done"],
        "documents": f"{len(d.documents) - len(missing)}/{len(d.documents)}" if d.documents else "（不要）",
        "note": d.note,
    }


def _window(days: int, client: str | None, include_done: bool) -> list[Deadline]:
    today = office.today()
    since = max(today - timedelta(days=OVERDUE_LOOKBACK_DAYS), office.tracking_since() or date.min)
    rows = []
    for d in _all_deadlines(client):
        done = _done_key(d) in office.data["done"]
        upcoming = today <= d.due <= today + timedelta(days=days)
        overdue = since <= d.due < today and not done
        if (upcoming or overdue) and (include_done or not done):
            rows.append(d)
    return sorted(rows, key=lambda d: (d.due, d.client_id))


@mcp.tool()
def office_overview() -> dict:
    """事務所全体の状況: 顧問先数、30日以内の期限の数、期限切れ、書類が揃っていない期限の数。"""
    deadlines = _window(30, None, include_done=False)
    today = office.today()
    return {
        "mode": "sample" if office.is_sample else "file",
        "office": office.data.get("office", ""),
        "today": today.isoformat(),
        "clients": len(office.data["clients"]),
        "deadlines_within_30_days": sum(d.due >= today for d in deadlines),
        "overdue_not_done": sum(d.due < today for d in deadlines),
        "documents_incomplete": sum(bool(_missing(d)) for d in deadlines),
        "note": "期限は原則的な目安です。特例の届出や個別の事情は事務所で確認してください",
    }


@mcp.tool()
def list_clients() -> list[dict]:
    """顧問先の一覧と、期限の計算に使っている条件。"""
    return office.data["clients"]


@mcp.tool()
def add_client(
    name: str,
    type: str,
    fiscal_year_end_month: int | None = None,
    consumption_tax: bool = False,
    withholding: str | None = None,
    employees: bool = False,
    depreciable_assets: bool = False,
    filing_extension: bool = False,
    consumption_tax_extension: bool = False,
    interim_filing: bool = False,
    contact: str = "",
) -> dict:
    """顧問先を登録する。条件から期限が自動で出るようになる。

    Args:
        name: 顧問先の名前
        type: 法人 / 個人
        fiscal_year_end_month: 決算月（法人のみ。例: 3）
        consumption_tax: 消費税の課税事業者なら true
        withholding: 源泉所得税の納付が「毎月」か「納期の特例」か。無ければ省略
        employees: 従業員がいる（年末調整・法定調書が必要）なら true
        depreciable_assets: 償却資産申告が必要なら true
        filing_extension: 法人税の申告期限の延長の特例を受けているなら true
        consumption_tax_extension: 消費税の申告期限の延長の届出をしているなら true
        interim_filing: 法人税の中間申告が必要なら true
        contact: 連絡の手段と相手（例: 「LINE（代表）」）
    """
    if type not in ("法人", "個人"):
        raise ValueError("type は「法人」か「個人」です")
    if type == "法人" and not (fiscal_year_end_month and 1 <= fiscal_year_end_month <= 12):
        raise ValueError("法人は決算月（1〜12）が必要です")
    if withholding not in (None, "毎月", "納期の特例"):
        raise ValueError("withholding は「毎月」か「納期の特例」です")
    numbers = [int(c["id"][1:]) for c in office.data["clients"] if c["id"][1:].isdigit()]
    client = {"id": f"c{max(numbers, default=0) + 1:02d}", "name": name, "type": type, "contact": contact}
    if type == "法人":
        client["fiscal_year_end_month"] = fiscal_year_end_month
    if withholding:
        client["withholding"] = withholding
    flags = dict(zip(CLIENT_FLAGS, (consumption_tax, filing_extension, consumption_tax_extension, interim_filing,
                                    employees, depreciable_assets)))
    client.update({k: v for k, v in flags.items() if v})
    office.data["clients"].append(client)
    office.save()
    return client


@mcp.tool()
def upcoming_deadlines(days: int = 60, client: str | None = None, include_done: bool = False) -> list[dict]:
    """今日から days 日以内の期限と、過去60日以内（使い始めた日以降）に期限が過ぎて未完了のもの（days_left がマイナス）。

    Args:
        days: 何日先まで見るか
        client: 顧問先の id か名前の一部で絞り込む
        include_done: 完了したものも含めるなら true
    """
    return [_row(d) for d in _window(days, client, include_done)]


@mcp.tool()
def document_status(client: str, days: int = 90) -> list[dict]:
    """顧問先の、days 日以内（と期限切れ）の期限に必要な書類の受け取り状況を、書類の束ごとに返す。"""
    groups: dict[str, dict] = {}
    for d in _window(days, client, include_done=False):
        if not d.documents:
            continue
        g = groups.setdefault(d.key, {"package": d.package, "period": d.period, "deadlines": [],
                                      "received": [x for x in d.documents if x not in _missing(d)],
                                      "missing": _missing(d)})
        g["deadlines"].append(f"{d.due.isoformat()} {d.title}")
    return list(groups.values())


@mcp.tool()
def mark_received(client: str, package: str, period: str, documents: list[str]) -> dict:
    """書類を受け取ったことを記録する。documents に ["すべて"] を渡すと、その束の書類をすべて受け取り済みにする。

    Args:
        client: 顧問先の id か名前の一部
        package: 書類の束（document_status の package。例: 決算・確定申告・年末調整・個別）
        period: 対象の期間（document_status の period。例: 2026-08期）
        documents: 受け取った書類の名前（document_status の missing と同じ名前）
    """
    c = office.client(client)
    expected = next((d.documents for d in _all_deadlines(c["id"]) if d.package == package and d.period == period), None)
    if expected is None:
        raise ValueError(f"{c['name']} に「{package} / {period}」の期限はありません。document_status で確認してください")
    docs = list(expected) if documents == ["すべて"] else documents
    unknown = [x for x in docs if x not in expected]
    if unknown:
        raise ValueError(f"この束に無い書類です: {unknown}。必要書類: {expected}")
    key = f"{c['id']}|{package}|{period}"
    got = office.data["received"].setdefault(key, [])
    got.extend(x for x in docs if x not in got)
    office.save()
    return {"client": c["name"], "package": package, "period": period,
            "received": [x for x in expected if x in got], "missing": [x for x in expected if x not in got]}


@mcp.tool()
def mark_done(client: str, title: str, period: str) -> dict:
    """期限の対応（申告・納付・提出）が済んだことを記録する。title と period は upcoming_deadlines の値。"""
    c = office.client(client)
    match = [d for d in _all_deadlines(c["id"]) if d.title == title and d.period == period]
    if not match:
        raise ValueError(f"{c['name']} に「{title} / {period}」の期限はありません")
    office.data["done"][_done_key(match[0])] = office.today().isoformat()
    office.save()
    return _row(match[0])


@mcp.tool()
def reminder_candidates(days: int = 45, quiet_days: int = 7) -> list[dict]:
    """期限が days 日以内（または期限切れ）なのに書類が揃っていない顧問先を、期限が近い順に返す。

    quiet_days 日以内に催促した先には recently_reminded を付ける（続けて催促しすぎないため）。
    """
    today = office.today()
    by_key: dict[str, dict] = {}
    for d in _window(days, None, include_done=False):
        missing = _missing(d)
        if not missing:
            continue
        if d.key not in by_key:
            client = next(c for c in office.data["clients"] if c["id"] == d.client_id)
            history = office.data["reminders"].get(d.key, [])
            last = history[-1] if history else None
            last_date = date.fromisoformat(last.split()[0]) if last else None
            by_key[d.key] = {
                "client": client["name"], "contact": client.get("contact", ""), "package": d.package,
                "period": d.period, "earliest_due": d.due.isoformat(), "days_left": (d.due - today).days,
                "deadlines": [], "missing": missing, "last_reminded": last,
                "recently_reminded": bool(last_date and (today - last_date).days < quiet_days),
            }
        by_key[d.key]["deadlines"].append(d.title)
    return sorted(by_key.values(), key=lambda r: r["days_left"])


@mcp.tool()
def record_reminder(client: str, package: str, period: str, channel: str) -> dict:
    """催促したことを記録する（例: channel="LINE"）。reminder_candidates の last_reminded に出る。"""
    c = office.client(client)
    key = f"{c['id']}|{package}|{period}"
    entry = f"{office.today().isoformat()} {channel}"
    office.data["reminders"].setdefault(key, []).append(entry)
    office.save()
    return {"client": c["name"], "package": package, "period": period, "history": office.data["reminders"][key]}


@mcp.tool()
def add_task(
    client: str,
    title: str,
    due_date: str | None = None,
    event_date: str | None = None,
    days_after: int | None = None,
    documents: list[str] | None = None,
    note: str = "",
) -> dict:
    """条件からは出ない期限を手動で追加する。期日を直接入れるか、出来事の日から何日後かで指定する。

    例: 役員変更登記（変更日から2週間以内）→ event_date="2026-09-25", days_after=14
    期日が休日なら翌営業日にずらす。

    Args:
        client: 顧問先の id か名前の一部
        title: 期限の名前
        due_date: 期日（例: 2026-10-09）
        event_date: 起点になる出来事の日
        days_after: 出来事の日から何日後が期限か
        documents: この期限に必要な書類
        note: メモ
    """
    c = office.client(client)
    due = _parse_day(due_date, "due_date")
    if due is None:
        event = _parse_day(event_date, "event_date")
        if event is None or days_after is None:
            raise ValueError("due_date か、event_date と days_after の組み合わせを指定してください")
        due = event + timedelta(days=days_after)
        note = f"{note} {event_date} から{days_after}日".strip()
    rolled = next_business_day(due)
    if rolled != due:
        note = f"{note}（本来は{due:%m/%d}、休日のため翌営業日）".strip()
    numbers = [int(t["id"][1:]) for t in office.data["custom"] if t["id"][1:].isdigit()]
    task = {"id": f"t{max(numbers, default=0) + 1}", "client_id": c["id"], "title": title, "due": rolled.isoformat(),
            "documents": documents or [], "note": note}
    office.data["custom"].append(task)
    office.save()
    return {**task, "client": c["name"]}


@mcp.prompt()
def weekly_check() -> str:
    """週の初めの期限・書類チェック。"""
    return (
        "今週の期限と書類の状況を確認します。\n"
        "1. office_overview で全体を見て、upcoming_deadlines(days=30) で期限の一覧を出してください。\n"
        "2. 期限切れ（days_left がマイナス）で未完了のものを最初に挙げてください。\n"
        "3. reminder_candidates で、書類が揃っていない顧問先を急ぐ順に挙げてください（recently_reminded の先は除くか、そう添えて）。\n"
        "4. 表（顧問先／期限／内容／書類の揃い具合／次にやること）にまとめてください。"
        "期限は原則的な目安なので、特例の届出などの確認が必要なものはそう添えてください。"
    )


@mcp.prompt()
def reminder_drafts(days: str = "45") -> str:
    """書類の催促文の下書き。"""
    return (
        f"期限まで {days} 日以内で書類が揃っていない顧問先に送る、催促文の下書きを作ります。\n"
        f"1. reminder_candidates(days={days}) で対象を確認してください。recently_reminded の先は外してください。\n"
        "2. 顧問先ごとに、連絡手段（contact）に合わせた文面を作ってください"
        "（LINE は短く箇条書き、メールは件名付き、電話なら伝える要点のメモ）。\n"
        "3. 文面には、足りない書類の一覧と、事務所に届けてほしい日（期限の2週間前を目安に、過ぎていれば早めに）を入れてください。"
        "責める言い方は避け、お願いの形にしてください。\n"
        "4. 送信はしないでください。ユーザーが送ったら record_reminder で記録するか確認してください。"
    )


if __name__ == "__main__":
    mcp.run()
