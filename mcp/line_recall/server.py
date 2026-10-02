"""LINE予約・配信MCP：予約台帳から来院が途切れた人を見つけ、LINE の再来院の案内を下書きし、効果を測る。

「2回目の来院につながらない」「定期で来ていた人がいつの間にか来なくなる」という整体・歯科・サロン向けのデモ。
途切れの判定は一律の日数ではなく、その人の来院ペース（来院間隔の中央値）と比べて行う。
次の予約が入っている人は案内の対象から外す。配信そのものは LINE公式アカウントなどで人が行う。

できること（ツール）:
  - overview          : 顧客数、直近30日の来院、次の予約がある人、分類ごとの人数
  - segments          : 分類（2回目未来院・途切れ・休眠・通院中・予約あり）の人数と判定のしかた
  - recall_list       : 分類ごとの案内候補を優先順に（最近案内した人には印）
  - customer_history  : 1人の来院履歴とペース
  - visit_stats       : 期間の来院数・新規数・2回目来院率・キャンセル率
  - export_list       : 案内候補を CSV に書き出す（配信ツールへの取り込み・手作業の配信用）
  - record_message    : 案内を送ったことを記録
  - message_effect    : 送った案内ごとに、何人が何日以内に戻ったか

起動:
  python mcp/line_recall/server.py
  （RESERVATIONS_CSV が無ければ架空の整体院のサンプル）
"""

from __future__ import annotations

import csv
import io
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from store import Ledger, open_ledger

mcp = MCPServer("line-recall")
ledger: Ledger = open_ledger()

NEW_WAIT_DAYS = 14          # 初回から2週間は様子を見る
NEW_GIVE_UP_DAYS = 120      # 初回から4か月を過ぎたら休眠扱い
DORMANT_DAYS = 180          # 最後の来院から半年で休眠
SECOND_VISIT_WINDOW = 60    # 2回目来院率は「初回から60日以内に2回目」で数える

SEGMENTS = {
    "予約あり": "次の予約が入っている（案内しない）",
    "新規（2週間以内）": "初回来院から2週間以内（まだ様子を見る）",
    "2回目未来院": f"来院1回だけで、初回から{NEW_WAIT_DAYS}〜{NEW_GIVE_UP_DAYS}日",
    "途切れ": "2回以上来ていて、その人のペース（来院間隔の中央値）の1.5倍かつ+14日を超えて空いている",
    "休眠": f"最後の来院から{DORMANT_DAYS}日以上（来院1回の人は{NEW_GIVE_UP_DAYS}日超）",
    "通院中": "いつものペースで来ている",
}
RECALL_SEGMENTS = ("2回目未来院", "途切れ", "休眠")
MESSAGE_RULES = [
    "「治る」「改善します」など施術の効果をうたう表現は使わない（医療・施術の広告のルールに注意）",
    "責めたり急かしたりせず、近況を気づかう一言から始める",
    "予約のしかた（リンク・返信など）を1つだけはっきり書く",
    "同じ人への案内は月1回まで。最近案内した人（recently_contacted）は外す",
    "名前や来院履歴など個人の情報を、一斉配信の本文に入れない",
]


@dataclass
class Customer:
    id: str
    name: str = ""
    visits: list[date] = field(default_factory=list)
    bookings: list[date] = field(default_factory=list)
    cancels: int = 0
    no_shows: int = 0
    menus: Counter = field(default_factory=Counter)

    @property
    def rhythm(self) -> float | None:
        gaps = [(b - a).days for a, b in zip(self.visits, self.visits[1:])]
        return statistics.median(gaps) if gaps else None


def _customers(as_of: date | None = None) -> dict[str, Customer]:
    """as_of を指定すると、その日時点で分かっていた来院だけで組み立てる（過去の予約状況は分からないので予約は含めない）。"""
    today = as_of or ledger.today()
    people: dict[str, Customer] = {}
    for r in sorted(ledger.reservations, key=lambda r: r.date):
        if as_of and r.date > as_of:
            continue
        c = people.setdefault(r.customer_id, Customer(r.customer_id))
        c.name = r.name or c.name
        if r.status == "cancelled":
            c.cancels += 1
        elif r.status == "no_show":
            c.no_shows += 1
        elif r.date > today or r.status == "booked":
            if r.date >= today:
                c.bookings.append(r.date)       # 今日以降の予約（来院予定）
        else:
            c.visits.append(r.date)
            if r.menu:
                c.menus[r.menu] += 1
    return people


def classify(c: Customer, today: date) -> str:
    if c.bookings:
        return "予約あり"
    if not c.visits:
        return "その他"
    days = (today - c.visits[-1]).days
    if days >= DORMANT_DAYS or (len(c.visits) == 1 and days > NEW_GIVE_UP_DAYS):
        return "休眠"
    if len(c.visits) == 1:
        return "2回目未来院" if days >= NEW_WAIT_DAYS else "新規（2週間以内）"
    r = c.rhythm
    return "途切れ" if days >= max(r * 1.5, r + 14) else "通院中"


def _last_contacted(customer_id: str) -> str | None:
    dates = [m["date"] for m in ledger.messages if customer_id in m["customer_ids"]]
    return max(dates) if dates else None


def _summary(c: Customer, today: date) -> dict:
    last = c.visits[-1] if c.visits else None
    return {
        "customer_id": c.id, "name": c.name, "segment": classify(c, today),
        "visits": len(c.visits), "first_visit": c.visits[0].isoformat() if c.visits else None,
        "last_visit": last.isoformat() if last else None,
        "days_since_last": (today - last).days if last else None,
        "usual_interval_days": round(c.rhythm) if c.rhythm else None,
        "main_menu": c.menus.most_common(1)[0][0] if c.menus else "",
        "next_booking": c.bookings[0].isoformat() if c.bookings else None,
        "cancels": c.cancels, "no_shows": c.no_shows,
        "last_contacted": _last_contacted(c.id),
    }


def _find(ref: str) -> Customer:
    people = _customers()
    if ref in people:
        return people[ref]
    found = [c for c in people.values() if ref and ref in c.name]
    if not found:
        raise ValueError(f"「{ref}」に当てはまるお客さまがいません")
    if len(found) > 1:
        raise ValueError(f"「{ref}」に当てはまるお客さまが複数います: {[f'{c.id} {c.name}' for c in found]}")
    return found[0]


def _parse_day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{label}「{value}」を日付として読めません（例: 2026-09-01）") from None


@mcp.tool()
def overview() -> dict:
    """店全体の状況。分類ごとの人数（案内の対象になる 2回目未来院・途切れ・休眠 を含む）。"""
    today = ledger.today()
    people = _customers()
    counts = Counter(classify(c, today) for c in people.values())
    recent = [r for r in ledger.reservations if r.status == "visited" and today - timedelta(days=30) < r.date <= today]
    return {
        "mode": "sample" if ledger.is_sample else "csv",
        "shop": ledger.shop, "today": today.isoformat(),
        "customers": len(people),
        "visits_last_30_days": len(recent),
        "segments": {s: counts.get(s, 0) for s in list(SEGMENTS) if counts.get(s)},
        "recall_targets": sum(counts.get(s, 0) for s in RECALL_SEGMENTS),
        "csv_problems": ledger.problems[:20],
    }


@mcp.tool()
def segments() -> dict:
    """分類の判定のしかたと人数。"""
    today = ledger.today()
    counts = Counter(classify(c, today) for c in _customers().values())
    return {name: {"definition": rule, "customers": counts.get(name, 0)} for name, rule in SEGMENTS.items()}


@mcp.tool()
def recall_list(segment: str, limit: int = 30, recently_contacted_days: int = 30) -> dict:
    """案内の候補を、分類ごとに優先順で返す。recently_contacted_days 日以内に案内した人には印を付ける。

    優先順: 2回目未来院は初回から日が浅い順（早いほど戻りやすい）、途切れはペースからの遅れが小さい順、
    休眠は来院回数が多い順（以前よく来ていた人から）。

    Args:
        segment: 2回目未来院 / 途切れ / 休眠
        limit: 返す人数
        recently_contacted_days: この日数以内に案内した人を recently_contacted にする
    """
    if segment not in RECALL_SEGMENTS:
        raise ValueError(f"segment は {' / '.join(RECALL_SEGMENTS)} のどれかです")
    today = ledger.today()
    rows = [_summary(c, today) for c in _customers().values() if classify(c, today) == segment]
    if segment == "2回目未来院":
        rows.sort(key=lambda r: r["days_since_last"])
    elif segment == "途切れ":
        rows.sort(key=lambda r: r["days_since_last"] / r["usual_interval_days"])
    else:
        rows.sort(key=lambda r: (-r["visits"], r["days_since_last"]))
    cutoff = (today - timedelta(days=recently_contacted_days)).isoformat()
    for r in rows:
        r["recently_contacted"] = bool(r["last_contacted"] and r["last_contacted"] >= cutoff)
    return {"segment": segment, "definition": SEGMENTS[segment], "total": len(rows),
            "customers": rows[:limit], "message_rules": MESSAGE_RULES}


@mcp.tool()
def customer_history(customer: str) -> dict:
    """1人の来院履歴（日付・メニュー・状態）と、ペース・分類。customer は顧客ID か名前の一部。"""
    c = _find(customer)
    history = [{"date": r.date.isoformat(), "menu": r.menu, "staff": r.staff, "status": r.status}
               for r in sorted(ledger.reservations, key=lambda r: r.date) if r.customer_id == c.id]
    return {**_summary(c, ledger.today()), "history": history}


@mcp.tool()
def visit_stats(date_from: str | None = None, date_to: str | None = None) -> dict:
    """期間の来院数・お客さまの数・新規の数・キャンセル率・無断キャンセル率と、2回目来院率。

    2回目来院率は、期間内に初めて来た人のうち、初回から60日以内に2回目に来た人の割合。
    初回から60日たっていない人はまだ判定できないので、分母から外す。

    Args:
        date_from: 開始日（省略で終了日の89日前＝直近90日）
        date_to: 終了日（省略で今日）
    """
    end = _parse_day(date_to, "date_to") or ledger.today()
    start = _parse_day(date_from, "date_from") or end - timedelta(days=89)
    in_range = [r for r in ledger.reservations if start <= r.date <= end and r.date <= ledger.today()]
    status = Counter(r.status for r in in_range)
    booked_total = sum(status.values())

    people = _customers()
    new = [c for c in people.values() if c.visits and start <= c.visits[0] <= end]
    judgeable = [c for c in new if (ledger.today() - c.visits[0]).days >= SECOND_VISIT_WINDOW]
    returned = [c for c in judgeable if len(c.visits) > 1 and (c.visits[1] - c.visits[0]).days <= SECOND_VISIT_WINDOW]

    weekly: dict[str, int] = defaultdict(int)
    for r in in_range:
        if r.status == "visited":
            weekly[(r.date - timedelta(days=r.date.weekday())).isoformat()] += 1
    return {
        "period": [start.isoformat(), end.isoformat()],
        "visits": status.get("visited", 0),
        "customers": len({r.customer_id for r in in_range if r.status == "visited"}),
        "new_customers": len(new),
        "second_visit_rate": f"{len(returned) / len(judgeable) * 100:.0f}%" if judgeable else None,
        "second_visit_judgeable": len(judgeable),
        "cancel_rate": f"{status.get('cancelled', 0) / booked_total * 100:.1f}%" if booked_total else None,
        "no_show_rate": f"{status.get('no_show', 0) / booked_total * 100:.1f}%" if booked_total else None,
        "weekly_visits": dict(sorted(weekly.items())),
    }


@mcp.tool()
def export_list(segment: str, exclude_recently_contacted: bool = True) -> dict:
    """案内の候補を CSV（Excel で開ける）に書き出す。配信ツールへの取り込みや手作業での配信に使う。

    取り込み用の列の形式は配信ツールごとに違うので、必要に応じて列を合わせること。
    """
    data = recall_list(segment, limit=10_000)
    rows = [r for r in data["customers"] if not (exclude_recently_contacted and r["recently_contacted"])]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["顧客ID", "名前", "分類", "最終来院日", "来院回数", "いつもの間隔（日）", "主なメニュー"])
    for r in rows:
        writer.writerow([r["customer_id"], r["name"], r["segment"], r["last_visit"], r["visits"],
                         r["usual_interval_days"] or "", r["main_menu"]])
    out_dir = Path(os.environ.get("LINE_EXPORT_DIR", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"案内候補_{segment}_{ledger.today().isoformat()}.csv"
    path.write_text(buf.getvalue(), encoding="utf-8-sig")
    return {"path": str(path.resolve()), "rows": len(rows), "excluded_recently_contacted": len(data["customers"]) - len(rows)}


@mcp.tool()
def record_message(segment: str, customer_ids: list[str], channel: str = "LINE", note: str = "") -> dict:
    """案内を送ったことを記録する。message_effect で「何人戻ったか」を測るのに使う。

    Args:
        segment: 送った相手の分類（例: 途切れ）
        customer_ids: 送った相手の顧客ID
        channel: 送った手段（LINE・ハガキ・電話など）
        note: 内容のメモ（例:「秋のお疲れケアの案内」）
    """
    people = _customers()
    unknown = [cid for cid in customer_ids if cid not in people]
    if unknown:
        raise ValueError(f"台帳に無い顧客ID です: {unknown}")
    if not customer_ids:
        raise ValueError("customer_ids が空です")
    entry = {"date": ledger.today().isoformat(), "segment": segment, "channel": channel,
             "customer_ids": sorted(set(customer_ids)), "note": note}
    ledger.messages.append(entry)
    ledger.save_messages()
    return {**entry, "count": len(entry["customer_ids"]), "persisted": ledger.state_path is not None}


def _rate(n: int, total: int) -> str | None:
    return f"{n / total * 100:.0f}%" if total else None


@mcp.tool()
def message_effect(days: int = 30) -> list[dict]:
    """送った案内ごとに、送った日から days 日以内に来院した人（予約が入った人を含む）の数と割合。

    比べる相手（baseline）として、送った日に同じ分類だったのに案内しなかった人が、同じ期間に戻った割合も出す。
    案内した人の戻りが baseline より高ければ、案内の効果があったと考えられる（人数が少ないうちは参考程度）。
    まだ days 日たっていない案内は judging=true（途中経過）として返す。
    """
    today = ledger.today()
    people = _customers()

    def came_back(cid: str, sent: date, window_end: date) -> bool:
        c = people.get(cid)
        return bool(c) and any(sent < d <= window_end for d in c.visits + c.bookings)

    out = []
    for m in sorted(ledger.messages, key=lambda m: m["date"]):
        sent = date.fromisoformat(m["date"])
        window_end = sent + timedelta(days=days)
        targets = set(m["customer_ids"])
        came = [cid for cid in targets if came_back(cid, sent, window_end)]
        then = _customers(as_of=sent)
        others = [cid for cid, c in then.items() if cid not in targets and classify(c, sent) == m["segment"]]
        others_came = [cid for cid in others if came_back(cid, sent, window_end)]
        out.append({
            "date": m["date"], "segment": m["segment"], "channel": m["channel"], "note": m.get("note", ""),
            "sent": len(targets), "returned": len(came), "return_rate": _rate(len(came), len(targets)),
            "baseline": {"not_sent": len(others), "returned": len(others_came),
                         "return_rate": _rate(len(others_came), len(others))},
            "judging": today < window_end,
        })
    return out


@mcp.prompt()
def recall_campaign(segment: str = "途切れ") -> str:
    """分類を選んで、LINE の再来院の案内を作る流れ。"""
    return (
        f"「{segment}」のお客さまに、LINE で再来院の案内を送る準備をします。\n"
        f"1. recall_list(segment=\"{segment}\") で対象と人数を確認してください。recently_contacted の人は外してください。\n"
        "2. 一斉配信の文面を1つ、200字以内で下書きしてください。message_rules を必ず守ってください"
        "（効果をうたわない、名前や来院履歴を本文に入れない、予約のしかたを1つだけ書く）。\n"
        "3. 季節の話題や、店から伝えたいこと（空き枠・新メニューなど）をユーザーに聞いて、一言入れてください。推測で書かないでください。\n"
        "4. 必要なら export_list で対象者の CSV を作り、保存先を伝えてください。\n"
        "5. 送信はしないでください。ユーザーが送ったら record_message で記録するか確認し、"
        "1か月後に message_effect で効果を見ることを提案してください。"
    )


@mcp.prompt()
def monthly_review() -> str:
    """月に1回の振り返り。"""
    return (
        "今月の来院の振り返りをします。\n"
        "1. visit_stats で直近90日の来院数・新規数・2回目来院率・キャンセル率を確認してください。\n"
        "2. overview で、案内の対象（2回目未来院・途切れ・休眠）の人数を確認してください。\n"
        "3. message_effect で、これまでの案内から何人戻ったかを確認してください。\n"
        "4. 次の形でまとめてください。\n"
        "   - 数字（来院数・新規・2回目来院率・キャンセル率）\n"
        "   - 一番手を打つべき分類と、その理由（人数と、戻りやすさ）\n"
        "   - 今月送る案内（分類・時期・内容のテーマ）"
    )


if __name__ == "__main__":
    mcp.run()
