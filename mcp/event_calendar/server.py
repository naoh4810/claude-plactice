"""交流会カレンダーMCP：交流会の予定を集め、行く会を選び、参加した成果を記録して、続ける会・やめる会を決める。

自分の営業用（A3）。営業シート（A1）は「会った人」の記録、こちらは「会そのもの」の記録で、
参加費・時間に対して面談・受注がどれだけ生まれたかを、会のシリーズ（毎月の朝活 など）ごとに見る。

できること（ツール）:
  - add_events          : 告知文やスクリーンショットから Claude が読み取った予定を登録（同じ日の同じシリーズは二重にしない）
  - import_ics          : connpass・Peatix・Google カレンダーの .ics を取り込む
  - upcoming            : これからの予定（時間の重なり・週の参加数つき）
  - recommend           : 候補の会を、同じシリーズの過去の成果から並べ、週の上限と重なりを見て行く会の案を作る
  - set_status          : 候補 / 申込済 / 見送り / 参加済 を変える
  - record_result       : 参加した会の成果（名刺・面談・受注・費用・時間）を記録
  - import_sheet_results: 営業シート（交流会タブ）の CSV から、会ごとの名刺・面談・受注の数を数えて記録
  - pending_results     : 終わったのに成果を記録していない会
  - series_report       : シリーズごとの費用対効果と、続ける／様子見／見直しの目安
  - export_ics          : 申込済の予定を .ics に書き出す（Google カレンダーなどに取り込む）

起動:
  python mcp/event_calendar/server.py
  （EVENT_CALENDAR_PATH が無ければ、架空の交流会 15件・今日＝2026-10-02 のサンプル）
"""

from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from mcp.server.mcpserver import MCPServer

import ics

HERE = Path(__file__).parent
SAMPLE = HERE / "sample" / "events.json"
STATUSES = ("候補", "申込済", "見送り", "参加済")
RESULT_FIELDS = ("cards", "meetings", "deals", "revenue", "cost", "hours")
# 営業シートの「進捗」で、面談以上に進んだとみなすもの（A1 の STAGES と合わせる）
MEETING_STAGES = {"面談予定", "オフラインアポ", "オンラインアポ", "1回目商談", "1回目面談", "1対1面談"}
DEAL_RESULTS = {"受注", "成約", "契約"}
PRIOR_MEETINGS = 1.0        # 行ったことのないシリーズの「1回あたりの面談数」の見込み（1回分の重み）
MIN_EXPECTED = 0.5          # これを下回るシリーズは recommend で選ばない
TRAVEL_BUFFER_MIN = 30      # 会場が違う予定のあいだに要る移動時間

mcp = MCPServer("event-calendar")


def series_of(title: str) -> str:
    """タイトルから回数・月・号などを落として、毎回同じになる「シリーズ名」を作る。"""
    t = unicodedata.normalize("NFKC", title)
    t = re.sub(r"【[^】]*】|\[[^\]]*\]", "", t)
    t = re.sub(r"第\s*\d+\s*回|vol\.?\s*\d+|#\s*\d+|no\.?\s*\d+|\d{4}年|\d{1,2}月(\d{1,2}日)?|\d{1,2}/\d{1,2}", "", t, flags=re.I)
    t = re.sub(r"(春|夏|秋|冬)(の\d+)?$", "", t.strip())
    return re.sub(r"\s+", " ", t).strip(" -・|｜") or title


def series_key(series: str) -> str:
    """表記の揺れ（全角・半角、大文字・小文字、空白）を除いた比較用のシリーズ名。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", series)).lower()


class Calendar:
    def __init__(self, path: Path | None):
        self.path = path
        if path is None:
            self.data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        elif path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {"events": [], "next_id": 1}

    def today(self) -> date:
        return date.fromisoformat(self.data["as_of"]) if self.path is None and self.data.get("as_of") else date.today()

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, event_id: str) -> dict:
        for e in self.data["events"]:
            if e["id"] == event_id:
                return e
        raise ValueError(f"予定 {event_id} はありません。upcoming で確認してください")

    def find_same(self, day: str, series: str, uid: str | None) -> dict | None:
        for e in self.data["events"]:
            if (uid and e.get("uid") == uid) or (e["date"] == day and series_key(e["series"]) == series_key(series)):
                return e
        return None

    def add(self, item: dict, source: str) -> tuple[str, dict]:
        """("added" | "updated" | "duplicate", 予定)。既にあれば、空いている項目だけ埋める。"""
        title = (item.get("title") or "").strip()
        day = item.get("date")
        if not title or not day:
            raise ValueError("title と date（YYYY-MM-DD）が必要です")
        day = date.fromisoformat(str(day)).isoformat()
        for key in ("start", "end"):
            if item.get(key):
                item[key] = datetime.strptime(item[key], "%H:%M").strftime("%H:%M")
        series = item.get("series") or series_of(title)
        same = self.find_same(day, series, item.get("uid"))
        fields = ("start", "end", "place", "organizer", "fee", "url", "audience", "uid")
        if same:
            filled = [k for k in fields if item.get(k) not in (None, "") and same.get(k) in (None, "")]
            for k in filled:
                same[k] = item[k]
            return ("updated" if filled else "duplicate"), same
        event = {"id": f"E{self.data['next_id']:03d}", "title": title, "series": series, "date": day,
                 **{k: item.get(k) or ("" if k != "fee" else 0) for k in fields},
                 "source": source, "status": "候補", "status_reason": ""}
        if not event["uid"]:
            del event["uid"]
        self.data["next_id"] += 1
        self.data["events"].append(event)
        return "added", event


cal = Calendar(Path(os.environ["EVENT_CALENDAR_PATH"]) if os.environ.get("EVENT_CALENDAR_PATH") else None)


def _minutes(hhmm: str | None) -> int | None:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5]) if hhmm else None


def _clash(a: dict, b: dict) -> bool:
    """同じ日に時間が重なる（会場が違えば移動時間も見る）。終日の予定は重なりとみなさない。"""
    if a["date"] != b["date"] or not a.get("start") or not b.get("start"):
        return False
    a0, b0 = _minutes(a["start"]), _minutes(b["start"])
    a1, b1 = _minutes(a.get("end")) or a0 + 120, _minutes(b.get("end")) or b0 + 120
    gap = 0 if a.get("place") == b.get("place") else TRAVEL_BUFFER_MIN
    return a0 < b1 + gap and b0 < a1 + gap


def _week(day: str) -> str:
    y, w, _ = date.fromisoformat(day).isocalendar()
    return f"{y}-W{w:02d}"


def _hours(e: dict) -> float | None:
    if e.get("start") and e.get("end"):
        return round((_minutes(e["end"]) - _minutes(e["start"])) / 60, 1)
    return None


def _brief(e: dict) -> dict:
    return {k: e.get(k) for k in ("id", "date", "start", "end", "title", "series", "place", "fee", "status")}


def _history() -> dict[str, dict]:
    """シリーズごとの、成果を記録した回の合計。"""
    hist: dict[str, dict] = defaultdict(lambda: {"attended": 0, **{k: 0.0 for k in RESULT_FIELDS}, "dates": []})
    for e in cal.data["events"]:
        r = e.get("result")
        if e["status"] != "参加済" or not r:
            continue
        h = hist[series_key(e["series"])]
        h.setdefault("name", e["series"])
        h["attended"] += 1
        h["dates"].append(e["date"])
        for k in RESULT_FIELDS:
            v = r.get(k)
            if v is None and k == "cost":
                v = e.get("fee") or 0
            if v is None and k == "hours":
                v = _hours(e) or 0
            h[k] += v or 0
    return hist


def _expected(series: str, hist: dict) -> dict:
    h = hist.get(series_key(series))
    if not h:
        return {"expected_meetings": PRIOR_MEETINGS, "basis": "未経験（行ったことがない。1回試して判断）"}
    exp = round((h["meetings"] + PRIOR_MEETINGS) / (h["attended"] + 1), 2)
    return {"expected_meetings": exp,
            "basis": f"過去{h['attended']}回で名刺{int(h['cards'])}・面談{int(h['meetings'])}・受注{int(h['deals'])}"}


@mcp.tool()
def add_events(events: list[dict]) -> dict:
    """予定を登録する。告知文・チラシ・SNS の投稿・スクリーンショットから Claude が読み取って渡す。

    各要素: title, date（YYYY-MM-DD）, start / end（HH:MM, 日本時間）, place, organizer, fee（円）, url,
    audience（参加者層。例「個人事業主 20名」）, series（省略時はタイトルから回数などを落として作る）。
    同じ日の同じシリーズが既にあれば二重に登録せず、空いている項目だけ埋める。新しい予定は「候補」。
    """
    out = {"added": [], "updated": [], "duplicates": [], "errors": []}
    for i, item in enumerate(events):
        try:
            kind, e = cal.add(dict(item), "手入力")
        except (ValueError, TypeError) as exc:
            out["errors"].append(f"{i + 1}件目: {exc}")
            continue
        out[{"added": "added", "updated": "updated", "duplicate": "duplicates"}[kind]].append(_brief(e))
    cal.save()
    return out


@mcp.tool()
def import_ics(path: str) -> dict:
    """.ics ファイル（connpass・Peatix・Google カレンダーの書き出し）から予定を取り込む。

    説明文（DESCRIPTION）は audience に入れる。参加費や主催者は説明文から Claude が読み取り、
    必要なら add_events で足す（同じ予定なら空いている項目が埋まる）。
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    out = {"added": [], "updated": [], "duplicates": []}
    for item in ics.parse(text):
        item["date"] = item["date"].isoformat()
        item["audience"] = (item.pop("description", "") or "")[:200]
        kind, e = cal.add(item, "ics")
        out[{"added": "added", "updated": "updated", "duplicate": "duplicates"}[kind]].append(_brief(e))
    cal.save()
    return out


@mcp.tool()
def upcoming(days: int = 45, include_skipped: bool = False) -> dict:
    """今日から days 日先までの予定。時間が重なる予定と、週ごとの申込済の数つき。"""
    today = cal.today()
    end = today + timedelta(days=days)
    items = [e for e in cal.data["events"] if today.isoformat() <= e["date"] <= end.isoformat()
             and (include_skipped or e["status"] != "見送り")]
    items.sort(key=lambda e: (e["date"], e.get("start") or ""))
    rows = []
    for e in items:
        row = _brief(e)
        row["clashes_with"] = [o["id"] for o in items if o is not e and _clash(e, o)]
        rows.append(row)
    weeks: dict[str, int] = defaultdict(int)
    for e in items:
        if e["status"] == "申込済":
            weeks[_week(e["date"])] += 1
    return {"today": today.isoformat(), "events": rows, "booked_per_week": dict(sorted(weeks.items()))}


@mcp.tool()
def recommend(days: int = 45, max_per_week: int = 2) -> dict:
    """候補の会を、同じシリーズの過去の成果（1回あたりの面談数）で並べ、行く会の案を作る。

    行ったことのないシリーズは「1回試す」見込みで並べる。案は、申込済の予定と時間が重ならず、
    1週間の参加（申込済を含む）が max_per_week 以下になるように、見込みの高い順に選ぶ。
    見込みが低いシリーズ（過去に何度か行って面談が生まれていない）は選ばない。申し込みはしない。
    """
    today = cal.today()
    end = (today + timedelta(days=days)).isoformat()
    hist = _history()
    future = [e for e in cal.data["events"] if today.isoformat() <= e["date"] <= end]
    booked = [e for e in future if e["status"] == "申込済"]
    per_week: dict[str, int] = defaultdict(int)
    for e in booked:
        per_week[_week(e["date"])] += 1
    ranked = []
    for e in future:
        if e["status"] != "候補":
            continue
        exp = _expected(e["series"], hist)
        cost = e.get("fee") or 0
        ranked.append({**_brief(e), **exp, "audience": e.get("audience", ""),
                       "cost_per_expected_meeting": round(cost / exp["expected_meetings"]) if exp["expected_meetings"] else None})
    ranked.sort(key=lambda r: (-r["expected_meetings"], r["fee"] or 0, r["date"]))
    chosen: list[dict] = []
    for r in ranked:
        e = cal.get(r["id"])
        if r["expected_meetings"] < MIN_EXPECTED:
            r["decision"] = "見送り案（見込みが低い）"
        elif any(_clash(e, o) for o in booked + [cal.get(c["id"]) for c in chosen]):
            r["decision"] = "見送り案（時間が重なる）"
        elif per_week[_week(e["date"])] >= max_per_week:
            r["decision"] = f"見送り案（その週は{max_per_week}件まで）"
        else:
            r["decision"] = "行く案"
            chosen.append(r)
            per_week[_week(e["date"])] += 1
    return {"today": today.isoformat(), "already_booked": [_brief(e) for e in booked], "candidates": ranked,
            "plan": [r["id"] for r in chosen]}


@mcp.tool()
def set_status(event_ids: list[str], status: str, reason: str = "") -> list[dict]:
    """予定の状態を変える（候補 / 申込済 / 見送り / 参加済）。実際の申し込みはユーザーが行う。"""
    if status not in STATUSES:
        raise ValueError(f"status は {STATUSES} のどれかにしてください")
    events = [cal.get(i) for i in event_ids]
    for e in events:
        e["status"] = status
        e["status_reason"] = reason
    cal.save()
    return [_brief(e) for e in events]


@mcp.tool()
def record_result(event_id: str, cards: int | None = None, meetings: int | None = None, deals: int | None = None,
                  revenue: int | None = None, cost: int | None = None, hours: float | None = None, memo: str = "") -> dict:
    """参加した会の成果を記録する（状態は参加済になる）。渡した項目だけ上書きする。

    cards: 名刺交換した人数、meetings: 面談（アポ）につながった人数、deals: 受注、revenue: 受注金額（円）、
    cost: 参加費＋飲食・交通（省略時は参加費）、hours: 移動を含めた時間（省略時は開始〜終了）。
    """
    e = cal.get(event_id)
    if e["date"] > cal.today().isoformat():
        raise ValueError(f"{e['title']}（{e['date']}）はまだ開催前です")
    values = {"cards": cards, "meetings": meetings, "deals": deals, "revenue": revenue, "cost": cost, "hours": hours}
    if any(v is not None and v < 0 for v in values.values()):
        raise ValueError("数は 0 以上にしてください")
    if cards is not None and meetings is not None and meetings > cards:
        raise ValueError("面談の数が名刺交換の数より多くなっています")
    r = e.setdefault("result", {"cards": 0, "meetings": 0, "deals": 0, "revenue": 0, "cost": None, "hours": None,
                                "memo": "", "source": "手入力"})
    r.update({k: v for k, v in values.items() if v is not None})
    if memo:
        r["memo"] = (r["memo"] + " / " if r.get("memo") else "") + memo
    e["status"] = "参加済"
    cal.save()
    return {**_brief(e), "result": r}


@mcp.tool()
def import_sheet_results(path: str) -> dict:
    """営業シート（交流会タブ）を CSV で書き出したものから、会ごとの名刺・面談・受注の数を数えて記録する。

    行の「日時」と「交流会」で、参加済・申込済の予定に結びつける。保存するのは数だけで、名前は保存しない。
    結びつかなかった行は、交流会名ごとの件数で返す（紹介など、交流会以外で会った人も含まれる）。
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp932")
    counts: dict[str, dict] = {}
    unmatched: dict[str, int] = defaultdict(int)
    for row in csv.DictReader(io.StringIO(text)):
        day_text = (row.get("日時") or "").strip().replace("/", "-")
        try:
            day = date.fromisoformat("-".join(p.zfill(2) for p in day_text.split(" ")[0].split("-"))).isoformat()
        except ValueError:
            unmatched[row.get("交流会") or "（日付なし）"] += 1
            continue
        name = unicodedata.normalize("NFKC", row.get("交流会") or "")
        same_day = [e for e in cal.data["events"] if e["date"] == day and e["status"] in ("参加済", "申込済")]
        hits = [e for e in same_day if name and (series_key(name) in series_key(e["title"])
                                                 or series_key(e["series"]) in series_key(name))]
        if len(hits) != 1:
            unmatched[row.get("交流会") or "（交流会名なし）"] += 1
            continue
        c = counts.setdefault(hits[0]["id"], {"cards": 0, "meetings": 0, "deals": 0})
        c["cards"] += 1
        progress, result = (row.get("進捗") or "").strip(), (row.get("結果") or "").strip()
        if progress in MEETING_STAGES or result in DEAL_RESULTS:
            c["meetings"] += 1
        if result in DEAL_RESULTS:
            c["deals"] += 1
    recorded = []
    for event_id, c in counts.items():
        e = cal.get(event_id)
        if e["date"] > cal.today().isoformat():
            continue
        r = e.setdefault("result", {"revenue": 0, "cost": None, "hours": None, "memo": ""})
        r.update(c)
        r["source"] = "営業シート"
        e["status"] = "参加済"
        recorded.append({**_brief(e), **c})
    cal.save()
    return {"recorded": recorded, "unmatched_rows": dict(unmatched)}


@mcp.tool()
def pending_results() -> list[dict]:
    """開催日を過ぎたのに成果を記録していない会（申込済のまま、または参加済で成果なし）。"""
    today = cal.today().isoformat()
    return [{**_brief(e), "days_ago": (cal.today() - date.fromisoformat(e["date"])).days}
            for e in sorted(cal.data["events"], key=lambda e: e["date"])
            if e["date"] < today and e["status"] in ("申込済", "参加済") and not e.get("result")]


@mcp.tool()
def series_report(date_from: str | None = None, date_to: str | None = None) -> dict:
    """シリーズごとの費用対効果。参加回数・費用・時間・名刺・面談・受注・売上と、
    面談1件あたりの費用、続ける／様子見／見直しの目安を返す（期間は開催日で絞る）。"""
    keep = [e for e in cal.data["events"]
            if (not date_from or e["date"] >= date_from) and (not date_to or e["date"] <= date_to)]
    saved = cal.data["events"]
    try:
        cal.data["events"] = keep
        hist = _history()
    finally:
        cal.data["events"] = saved
    rows = []
    for h in hist.values():
        series = h["name"]
        meetings = h["meetings"]
        per_meeting = round(h["cost"] / meetings) if meetings else None
        if h["attended"] >= 2 and meetings == 0:
            verdict = "見直し（2回以上行って面談0）"
        elif h["revenue"] > h["cost"] or (per_meeting is not None and per_meeting <= 3000):
            verdict = "続ける"
        else:
            verdict = "様子見"
        rows.append({"series": series, "attended": h["attended"], "cost": int(h["cost"]), "hours": h["hours"],
                     "cards": int(h["cards"]), "meetings": int(meetings), "deals": int(h["deals"]),
                     "revenue": int(h["revenue"]),
                     "meeting_rate": f"{meetings / h['cards']:.0%}" if h["cards"] else "-",
                     "cost_per_meeting": per_meeting,
                     "hours_per_meeting": round(h["hours"] / meetings, 1) if meetings else None,
                     "verdict": verdict, "last": max(h["dates"])})
    rows.sort(key=lambda r: (-(r["meetings"] / r["attended"]), r["cost"]))
    total = {k: sum(r[k] for r in rows) for k in ("attended", "cost", "cards", "meetings", "deals", "revenue")}
    return {"series": rows, "total": total, "not_recorded": len(pending_results())}


@mcp.tool()
def export_ics(event_ids: list[str] | None = None) -> dict:
    """予定を .ics に書き出す（Google カレンダーなどの「インポート」で取り込める）。
    event_ids を省略すると、これからの申込済の予定をすべて書き出す。"""
    if event_ids:
        events = [cal.get(i) for i in event_ids]
    else:
        events = [e for e in cal.data["events"] if e["status"] == "申込済" and e["date"] >= cal.today().isoformat()]
    if not events:
        raise ValueError("書き出す予定がありません（申込済のこれからの予定が無い）")
    events = sorted(copy.deepcopy(events), key=lambda e: (e["date"], e.get("start") or ""))
    out_dir = Path(os.environ.get("EVENT_CALENDAR_EXPORT_DIR", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"交流会_{events[0]['date']}_{events[-1]['date']}.ics"
    path.write_text(ics.build(events, datetime.now().astimezone()), encoding="utf-8", newline="")
    return {"path": str(path.resolve()), "events": [_brief(e) for e in events]}


@mcp.prompt()
def plan_month() -> str:
    """来月までの交流会の予定を決める。"""
    return (
        "交流会の予定を決めます。\n"
        "1. ユーザーが貼った告知文・スクリーンショット・.ics があれば、add_events / import_ics で登録してください"
        "（日時・会場・参加費・参加者層を読み取る。読み取れない項目は空のまま）。\n"
        "2. pending_results で成果の記録漏れがあれば、先に聞いて record_result で記録してください。\n"
        "3. recommend で行く会の案を作り、表（日付／会／見込み／根拠／案）で見せてください。"
        "未経験のシリーズは「1回試す」と分かるように。\n"
        "4. ユーザーが決めた会だけ set_status で申込済にし、export_ics でカレンダー用のファイルを作ってください。"
        "申し込みそのものはユーザーが行います。"
    )


@mcp.prompt()
def monthly_review(month: str = "") -> str:
    """月ごとのふり返り。"""
    return (
        f"{month or '先月'}の交流会をふり返ります。\n"
        "1. pending_results の記録漏れを片づけてください（営業シートの CSV があれば import_sheet_results）。\n"
        "2. series_report で、シリーズごとの面談1件あたりの費用・時間と目安を表にしてください。\n"
        "3. 「見直し」のシリーズは、やめるか、行き方を変えるか（参加者層・時間帯・自己紹介の仕方）の案を1つずつ。\n"
        "4. 数字にない印象（memo）も踏まえて、来月の方針を3行でまとめてください。"
    )


if __name__ == "__main__":
    mcp.run()
