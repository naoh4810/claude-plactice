"""交流会マッチングMCP：申込一覧から、相性の良い組み合わせ・席替えの計画・紹介とフォローの材料を作る。

交流会の主催者が頭の中でやっている「この人とこの人をつなぎたい」を、申込フォームの
「提供できること」「探していること」から出すデモ。紹介文やフォローの文面は Claude が書き、
送るのは主催者。「紹介してもよい」と答えていない人は紹介の対象にしない。

できること（ツール）:
  - import_participants : 申込フォームの CSV を取り込む
  - participants        : 参加者の一覧（初参加・紹介の同意）
  - suggestions         : 人ごとの「話すとよい相手」上位と理由
  - unmatched_wants     : 参加者の誰とも結びつかなかった「探していること」（主催者が個別につなぐ材料）
  - table_plan          : 卓割り（ラウンドをまたいで同じ二人が重ならないように）
  - pair_detail         : 二人の情報と、つながる理由（紹介文の材料）
  - record_meetings     : 実際に同じ卓になった組を記録（次回以降は同じ組を避ける）
  - follow_ups          : 会った組の紹介・フォロー候補と、会えなかった相性の良い組（次回つなぐ候補）

起動:
  python mcp/event_matching/server.py
  （EVENT_DATA_PATH が無ければ、架空の交流会 16人のサンプル）
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from matching import Person, is_client_seeking, pair_score, phrase_match, phrases, plan_tables

HERE = Path(__file__).parent
SAMPLE_CSV = HERE / "sample" / "申込一覧_10月.csv"
SAMPLE_EVENT = "サンプル交流会 10月"
COLUMN_ALIASES = {
    "name": ["お名前", "名前", "氏名"],
    "business": ["事業名", "会社名", "屋号", "お店の名前"],
    "industry": ["業種", "職種", "お仕事"],
    "gives": ["提供できること", "できること", "強み", "得意なこと"],
    "wants": ["探していること", "求めていること", "困りごと", "つながりたい人"],
    "visits": ["参加回数", "参加した回数"],
    "consent": ["紹介してもよいか", "紹介の可否", "紹介可", "同意"],
}
YES = ("はい", "可", "ok", "yes", "○", "同意する")

mcp = MCPServer("event-matching")


class Events:
    def __init__(self, path: Path | None):
        self.path = path
        self.data = {"events": {}, "met": []}
        if path and path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        if path is None:
            self.import_csv(SAMPLE_CSV.read_text(encoding="utf-8-sig"), SAMPLE_EVENT, "2026-10-20", save=False)
            # 9月の会で同じ卓になった組（今回は避ける）
            self.data["met"] = [["青木 さくら", "井上 健太", "サンプル交流会 9月"], ["井上 健太", "上田 みなみ", "サンプル交流会 9月"]]

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def import_csv(self, text: str, event: str, date: str, save: bool = True) -> dict:
        reader = csv.DictReader(io.StringIO(text))
        header = reader.fieldnames or []
        cols = {f: next((h for h in header if h.strip() in names), None) for f, names in COLUMN_ALIASES.items()}
        if not cols["name"] or not (cols["gives"] or cols["wants"]):
            raise ValueError(f"名前と、提供できること・探していることの列が必要です。CSV の列: {header}")
        people, problems = [], []
        for line_no, row in enumerate(reader, start=2):
            get = lambda f: (row.get(cols[f]) or "").strip() if cols[f] else ""  # noqa: E731
            if not get("name"):
                problems.append(f"{line_no}行目: 名前が空です")
                continue
            visits = get("visits")
            consent = get("consent")
            people.append({
                "name": get("name"), "business": get("business"), "industry": get("industry"),
                "gives": phrases(get("gives")), "wants": phrases(get("wants")),
                "visits": int(visits) if visits.isdigit() else 1,
                # 同意の列が無いときは紹介しない側に倒す
                "consent": bool(consent) and consent.lower() in YES,
            })
        names = [p["name"] for p in people]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            problems.append(f"同じ名前が複数あります: {dupes}（区別できるよう名前を変えてください）")
        self.data["events"][event] = {"date": date, "participants": people, "plan": None}
        if save:
            self.save()
        return {"event": event, "participants": len(people), "problems": problems,
                "no_consent_column": cols["consent"] is None}

    def people(self, event: str) -> list[Person]:
        if event not in self.data["events"]:
            raise ValueError(f"交流会「{event}」はありません。登録済み: {list(self.data['events'])}")
        return [Person(**p) for p in self.data["events"][event]["participants"]]

    def met(self) -> set[frozenset]:
        return {frozenset(m[:2]) for m in self.data["met"]}


events = Events(Path(os.environ["EVENT_DATA_PATH"]) if os.environ.get("EVENT_DATA_PATH") else None)


def _event(event: str | None) -> str:
    if event:
        return event
    if not events.data["events"]:
        raise ValueError("交流会がまだありません。import_participants で申込一覧を取り込んでください")
    return max(events.data["events"], key=lambda e: events.data["events"][e]["date"])


def _person(people: list[Person], ref: str) -> Person:
    found = [p for p in people if p.name == ref] or [p for p in people if ref in p.name or ref in p.business]
    if len(found) != 1:
        raise ValueError(f"「{ref}」に当てはまる参加者が {len(found)} 人います")
    return found[0]


@mcp.tool()
def import_participants(path: str, event: str, date: str) -> dict:
    """申込フォームの CSV（UTF-8 / Shift_JIS）を取り込む。

    列名は「お名前・事業名・業種・提供できること・探していること・参加回数・紹介してもよいか」などを自動で対応づける。
    提供できること・探していることは「、」区切り。紹介の同意の列が無い場合は、全員を紹介しない扱いにする。
    """
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("文字コードを判定できません（UTF-8 か Shift_JIS で保存してください）")
    return events.import_csv(text, event, date)


@mcp.tool()
def participants(event: str | None = None) -> dict:
    """参加者の一覧。初参加（newcomer）と、紹介してよいか（consent）付き。"""
    ev = _event(event)
    people = events.people(ev)
    return {"event": ev, "date": events.data["events"][ev]["date"], "count": len(people),
            "people": [{"name": p.name, "business": p.business, "industry": p.industry, "newcomer": p.newcomer,
                        "consent": p.consent, "gives": p.gives, "wants": p.wants} for p in people]}


@mcp.tool()
def suggestions(person: str | None = None, event: str | None = None, top: int = 3) -> list[dict]:
    """人ごとの「話すとよい相手」上位と、その理由。以前の交流会で会った相手は下げる。

    紹介に同意していない人は、相手の候補には出さない（本人の候補は出す）。
    """
    ev = _event(event)
    people = events.people(ev)
    met = events.met()
    targets = [_person(people, person)] if person else people
    out = []
    for p in targets:
        ranked = []
        for q in people:
            if q is p or not q.consent:
                continue
            score, why = pair_score(p, q, met)
            if score > 0:
                ranked.append({"name": q.name, "business": q.business, "score": score, "reasons": why,
                               "met_before": frozenset((p.name, q.name)) in met})
        ranked.sort(key=lambda r: -r["score"])
        out.append({"person": p.name, "newcomer": p.newcomer, "suggestions": ranked[:top]})
    return out


@mcp.tool()
def unmatched_wants(event: str | None = None) -> list[dict]:
    """参加者の誰の「提供できること」とも結びつかなかった「探していること」。

    「顧問先の紹介」「採用に困っている会社」のようにお客さんを探している内容は、相手側の「探していること」と
    対になっていることが多いので、主催者が個別につなぐ材料にする。
    """
    people = events.people(_event(event))
    out = []
    for p in people:
        for want in p.wants:
            if not any(phrase_match(want, g) for q in people if q is not p for g in q.gives):
                out.append({"person": p.name, "want": want,
                            "kind": "お客さん探し" if is_client_seeking(want) else "参加者にいない"})
    return out


@mcp.tool()
def table_plan(table_size: int = 4, rounds: int = 3, event: str | None = None, seed: int = 0) -> dict:
    """卓割りを作る。ラウンドをまたいで同じ二人が同じ卓にならず、相性の合計が高く、
    初参加の人だけの卓ができないように配置する。以前の交流会で会った組は避ける。"""
    if not 2 <= table_size <= 10 or not 1 <= rounds <= 6:
        raise ValueError("table_size は 2〜10、rounds は 1〜6 にしてください")
    ev = _event(event)
    people = events.people(ev)
    met = events.met()
    plan = plan_tables(people, table_size, rounds, met, seed)
    by_name = {p.name: p for p in people}
    pairs = [frozenset(pr) for rnd in plan for t in rnd for pr in itertools.combinations(t, 2)]
    rounds_out = []
    for n, rnd in enumerate(plan, start=1):
        tables = []
        for i, t in enumerate(rnd, start=1):
            highlights = []
            for a, b in itertools.combinations(t, 2):
                score, why = pair_score(by_name[a], by_name[b], met)
                if why:
                    highlights.append({"pair": [a, b], "score": score, "themes": sorted({w["theme"] for w in why})})
            tables.append({"table": i, "members": t, "newcomers": [m for m in t if by_name[m].newcomer],
                           "good_pairs": sorted(highlights, key=lambda h: -h["score"])[:3]})
        rounds_out.append({"round": n, "tables": tables})
    events.data["events"][ev]["plan"] = plan
    events.save()
    return {"event": ev, "rounds": rounds_out,
            "repeated_pairs": len(pairs) - len(set(pairs)),
            "met_before_pairs_seated": sum(1 for p in set(pairs) if p in met)}


@mcp.tool()
def pair_detail(a: str, b: str, event: str | None = None) -> dict:
    """二人の情報と、つながる理由。紹介文を書く材料にする（同意していない人がいれば紹介しない旨を返す）。"""
    people = events.people(_event(event))
    pa, pb = _person(people, a), _person(people, b)
    score, why = pair_score(pa, pb, events.met())
    return {
        "a": {"name": pa.name, "business": pa.business, "industry": pa.industry, "gives": pa.gives, "wants": pa.wants},
        "b": {"name": pb.name, "business": pb.business, "industry": pb.industry, "gives": pb.gives, "wants": pb.wants},
        "score": score, "reasons": why, "can_introduce": pa.consent and pb.consent,
        "met_before": frozenset((pa.name, pb.name)) in events.met(),
    }


@mcp.tool()
def record_meetings(event: str | None = None, use_plan: bool = True, extra_pairs: list[list[str]] | None = None) -> dict:
    """実際に同じ卓になった組を記録する。use_plan=true なら最後に作った卓割りのとおりに記録する。

    当日に席が変わった場合は use_plan=false にして extra_pairs で実際の組を渡す。次回以降の候補・卓割りでは避ける。
    """
    ev = _event(event)
    pairs: set[frozenset] = set()
    if use_plan:
        plan = events.data["events"][ev].get("plan")
        if not plan:
            raise ValueError("卓割りがありません。table_plan を作るか、use_plan=false で extra_pairs を渡してください")
        pairs |= {frozenset(pr) for rnd in plan for t in rnd for pr in itertools.combinations(t, 2)}
    names = {p.name for p in events.people(ev)}
    for pr in extra_pairs or []:
        if len(pr) != 2 or not set(pr) <= names:
            raise ValueError(f"参加者2人の組にしてください: {pr}")
        pairs.add(frozenset(pr))
    existing = {(frozenset(m[:2]), m[2]) for m in events.data["met"]}
    new = [sorted(p) for p in pairs if (p, ev) not in existing]
    events.data["met"].extend([*p, ev] for p in new)
    events.save()
    return {"event": ev, "recorded_pairs": len(new)}


@mcp.tool()
def follow_ups(event: str | None = None, top: int = 10) -> dict:
    """この交流会で会った組のうち相性の良いもの（紹介・フォローの候補）と、会えなかった相性の良い組（次回つなぐ候補）。"""
    ev = _event(event)
    people = events.people(ev)
    met_here = {frozenset(m[:2]) for m in events.data["met"] if m[2] == ev}
    met_before = {frozenset(m[:2]) for m in events.data["met"] if m[2] != ev}
    met_rows, missed_rows = [], []
    for a, b in itertools.combinations(people, 2):
        key = frozenset((a.name, b.name))
        if key in met_before:
            continue
        score, why = pair_score(a, b, set())
        if not why:
            continue
        row = {"pair": [a.name, b.name], "score": score, "reasons": why, "can_introduce": a.consent and b.consent}
        (met_rows if key in met_here else missed_rows).append(row)
    return {"event": ev,
            "met_good_pairs": sorted(met_rows, key=lambda r: -r["score"])[:top],
            "missed_good_pairs": sorted(missed_rows, key=lambda r: -r["score"])[:top]}


@mcp.prompt()
def before_event(event: str = "") -> str:
    """交流会の前の準備。"""
    return (
        f"交流会{('「' + event + '」') if event else ''}の準備をします。\n"
        "1. participants で参加者を確認し、初参加の人と、紹介に同意していない人を把握してください。\n"
        "2. table_plan で卓割りを作り、ラウンドごとの表（卓番号／メンバー／その卓の注目の組み合わせ）にしてください。\n"
        "3. 主催者が当日声をかけるときの一言メモを、初参加の人ごとに1行で作ってください（suggestions の理由を使う）。\n"
        "4. unmatched_wants を見て、主催者が個別につなげそうなものがあれば挙げてください。"
    )


@mcp.prompt()
def after_event(event: str = "") -> str:
    """交流会の後のフォロー。"""
    return (
        f"交流会{('「' + event + '」') if event else ''}の後のフォローをします。\n"
        "1. 当日の卓が計画どおりだったかをユーザーに確認し、record_meetings で記録してください。\n"
        "2. follow_ups の met_good_pairs のうち can_introduce が true の組について、主催者から二人に送る"
        "「あらためてのご紹介」の文面を作ってください（150字程度、理由は reasons から、誇張しない）。\n"
        "3. missed_good_pairs は「次回ぜひ」の候補として一覧にしてください。\n"
        "4. 送信はしないでください。can_introduce が false の人は紹介文の対象にしないでください。"
    )


if __name__ == "__main__":
    mcp.run()
