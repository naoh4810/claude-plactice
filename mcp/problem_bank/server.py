"""問題バンクMCP：問題を溜め、先生が確認し、生徒ごとの苦手と復習に合わせてプリントを作る。

「チャットで毎回問題を作ってもらっている」家庭教師・塾から、「放っておいても問題が溜まり、
生徒ごとに出し分けられる」状態に進むためのデモ。問題を作るのは Claude、保存・確認の管理・
出題の選び方・プリントづくりはこのサーバー、問題の正しさを確かめるのは先生。

AI が作った問題は「下書き」で入り、先生が確認して「確認済み」にしたものだけがプリントに出る。

できること（ツール）:
  - coverage        : 単元×難易度ごとの確認済みの問題数と、目標に足りないところ
  - add_problems    : 問題を追加（AI生成は下書き、自作は確認済み。同じ問題は二重に入らない）
  - review_queue    : 確認待ちの下書き
  - approve / reject / edit_problem : 下書きの確認・却下・修正
  - list_problems   : 条件で問題を探す
  - add_student / list_students     : 生徒の登録と一覧
  - record_results  : 生徒の正誤を記録
  - student_progress: 単元ごとの正答率、苦手な単元、復習が必要な問題
  - make_worksheet  : 復習 → 苦手 → 指定の単元 の順に選んで、A4 のプリント（問題＋解答・解説）を保存

起動:
  python mcp/problem_bank/server.py
  （BANK_DATA_PATH が無ければ架空の生徒と中1数学のサンプルをメモリ上で使う）
"""

from __future__ import annotations

import html
import json
import os
import random
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer

SAMPLE_PATH = Path(__file__).with_name("sample_bank.json")
EMPTY = {"targets_per_cell": 5, "problems": [], "students": [], "results": [], "worksheets": []}
REVIEW_AFTER_DAYS = 7      # 間違えてから7日たったら復習に出す
AVOID_REPEAT_DAYS = 14     # 14日以内に出した問題は避ける
WEAK_RATE = 0.6            # 正答率60%未満（3問以上解いた単元）を苦手とする

mcp = MCPServer("problem-bank")


class Bank:
    def __init__(self, path: Path | None):
        self.path = path
        if path is None:
            self.data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        elif path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = json.loads(json.dumps(EMPTY))
        self.data.pop("_note", None)

    def today(self) -> date:
        return date.fromisoformat(self.data["as_of"]) if self.path is None and self.data.get("as_of") else date.today()

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def problem(self, pid: str) -> dict:
        found = next((p for p in self.data["problems"] if p["id"] == pid), None)
        if found is None:
            raise ValueError(f"問題 {pid} はありません")
        return found

    def student(self, ref: str) -> dict:
        found = [s for s in self.data["students"] if s["id"] == ref or ref in s["name"]]
        if not found:
            raise ValueError(f"生徒「{ref}」はいません。list_students で確認してください")
        if len(found) > 1:
            raise ValueError(f"「{ref}」に当てはまる生徒が複数います: {[s['name'] for s in found]}")
        return found[0]


bank = Bank(Path(os.environ["BANK_DATA_PATH"]) if os.environ.get("BANK_DATA_PATH") else None)


def _norm(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split()).lower()


def _confirmed() -> list[dict]:
    return [p for p in bank.data["problems"] if p["status"] == "確認済み"]


def _public(p: dict, with_answer: bool = True) -> dict:
    keys = ["id", "subject", "grade", "unit", "difficulty", "question", "status", "source"]
    if with_answer:
        keys += ["answer", "explanation"]
    return {k: p[k] for k in keys}


@mcp.tool()
def coverage(subject: str | None = None, grade: str | None = None) -> dict:
    """単元×難易度（1〜3）ごとの、確認済みの問題数と下書きの数。目標（targets_per_cell）に足りないところを gaps で返す。

    Claude はこの gaps を見て、足りない単元・難易度の問題を作って add_problems するとよい。
    """
    target = bank.data.get("targets_per_cell", 5)
    cells: dict[tuple, dict] = defaultdict(lambda: {"confirmed": 0, "drafts": 0})
    for p in bank.data["problems"]:
        if (subject and p["subject"] != subject) or (grade and p["grade"] != grade) or p["status"] == "却下":
            continue
        cell = cells[(p["subject"], p["grade"], p["unit"], p["difficulty"])]
        cell["confirmed" if p["status"] == "確認済み" else "drafts"] += 1
    table, gaps = [], []
    for s, g, u in sorted({k[:3] for k in cells}):
        row = {"subject": s, "grade": g, "unit": u}
        for d in (1, 2, 3):
            c = cells.get((s, g, u, d), {"confirmed": 0, "drafts": 0})
            row[f"difficulty_{d}"] = c
            if c["confirmed"] < target:
                gaps.append({"subject": s, "grade": g, "unit": u, "difficulty": d,
                             "need": target - c["confirmed"], "drafts_waiting": c["drafts"]})
        table.append(row)
    return {"target_per_cell": target, "units": table, "gaps": gaps}


@mcp.tool()
def add_problems(problems: list[dict]) -> dict:
    """問題を追加する。1問ずつ次の形で渡す。

    {"subject": "数学", "grade": "中1", "unit": "一次方程式", "difficulty": 2,
     "question": "方程式 4x−5＝2x＋9 を解きなさい。", "answer": "x＝7", "explanation": "移項して…",
     "source": "AI生成"}

    difficulty は 1（基本）〜3（応用）。source が「AI生成」のものは下書き（先生の確認待ち）、
    「自作」は確認済みとして入る。問題文が同じものは二重に入らない。
    答えは必ず自分で解き直して確かめてから渡すこと。
    """
    existing = {_norm(p["question"]) for p in bank.data["problems"]}
    numbers = [int(p["id"][1:]) for p in bank.data["problems"]]
    next_no = max(numbers, default=0) + 1
    added, duplicates, errors = [], 0, []
    for n, item in enumerate(problems, start=1):
        missing = [k for k in ("subject", "grade", "unit", "question", "answer") if not str(item.get(k, "")).strip()]
        if missing:
            errors.append(f"{n}件目: {missing} が空です")
            continue
        if item.get("difficulty") not in (1, 2, 3):
            errors.append(f"{n}件目: difficulty は 1・2・3 のどれかです")
            continue
        if _norm(item["question"]) in existing:
            duplicates += 1
            continue
        source = item.get("source", "AI生成")
        p = {"id": f"P{next_no:03d}", "subject": item["subject"], "grade": item["grade"], "unit": item["unit"],
             "difficulty": item["difficulty"], "question": item["question"].strip(), "answer": str(item["answer"]).strip(),
             "explanation": str(item.get("explanation", "")).strip(), "status": "確認済み" if source == "自作" else "下書き",
             "source": source, "created": bank.today().isoformat()}
        bank.data["problems"].append(p)
        existing.add(_norm(p["question"]))
        added.append(p["id"])
        next_no += 1
    if added:
        bank.save()
    return {"added": added, "duplicates": duplicates, "errors": errors}


@mcp.tool()
def review_queue(limit: int = 20) -> list[dict]:
    """先生の確認を待っている下書き。答えと解説も付く。

    Claude が先に問題を解き直し、答えが合っているか・問題文に不備がないかを確かめてから先生に見せること。
    """
    return [_public(p) for p in bank.data["problems"] if p["status"] == "下書き"][:limit]


@mcp.tool()
def approve(problem_ids: list[str]) -> dict:
    """下書きを確認済みにする（先生が問題と答えを確かめたもの）。確認済みになった問題だけがプリントに出る。"""
    for pid in problem_ids:
        if bank.problem(pid)["status"] != "下書き":
            raise ValueError(f"{pid} は下書きではありません")
    for pid in problem_ids:
        bank.problem(pid)["status"] = "確認済み"
    bank.save()
    return {"approved": problem_ids}


@mcp.tool()
def reject(problem_id: str, reason: str) -> dict:
    """下書きを却下する（プリントにも coverage にも出なくなる）。reason は記録に残る。"""
    p = bank.problem(problem_id)
    p["status"], p["rejected_reason"] = "却下", reason
    bank.save()
    return {"rejected": problem_id, "reason": reason}


@mcp.tool()
def edit_problem(problem_id: str, question: str | None = None, answer: str | None = None,
                 explanation: str | None = None, difficulty: int | None = None) -> dict:
    """問題を直す。確認済みの問題を直したときは、もう一度確認が必要なので下書きに戻す。"""
    p = bank.problem(problem_id)
    if difficulty is not None and difficulty not in (1, 2, 3):
        raise ValueError("difficulty は 1・2・3 のどれかです")
    for key, value in (("question", question), ("answer", answer), ("explanation", explanation), ("difficulty", difficulty)):
        if value is not None:
            p[key] = value
    if p["status"] == "確認済み":
        p["status"] = "下書き"
    bank.save()
    return _public(p)


@mcp.tool()
def list_problems(unit: str | None = None, difficulty: int | None = None, status: str | None = None,
                  subject: str | None = None, limit: int = 50) -> list[dict]:
    """条件で問題を探す。status は 確認済み / 下書き / 却下。"""
    rows = [p for p in bank.data["problems"]
            if (not unit or p["unit"] == unit) and (not difficulty or p["difficulty"] == difficulty)
            and (not status or p["status"] == status) and (not subject or p["subject"] == subject)]
    return [_public(p) for p in rows[:limit]]


@mcp.tool()
def add_student(name: str, grade: str, subjects: list[str]) -> dict:
    """生徒を登録する。個人を特定しにくい呼び名（イニシャルなど）でもよい。"""
    numbers = [int(s["id"][1:]) for s in bank.data["students"]]
    s = {"id": f"S{max(numbers, default=0) + 1}", "name": name, "grade": grade, "subjects": subjects}
    bank.data["students"].append(s)
    bank.save()
    return s


@mcp.tool()
def list_students() -> list[dict]:
    """生徒の一覧と、解いた問題の数。"""
    counts = defaultdict(int)
    for r in bank.data["results"]:
        counts[r["student_id"]] += 1
    return [{**s, "answered": counts[s["id"]]} for s in bank.data["students"]]


@mcp.tool()
def record_results(student: str, results: list[dict], date_str: str | None = None) -> dict:
    """生徒の正誤を記録する。results は [{"problem_id": "P013", "correct": true}, ...]。date_str 省略で今日。"""
    s = bank.student(student)
    day = date.fromisoformat(date_str) if date_str else bank.today()
    for r in results:
        bank.problem(r["problem_id"])
        if not isinstance(r.get("correct"), bool):
            raise ValueError(f"correct は true / false です: {r}")
    for r in results:
        bank.data["results"].append({"student_id": s["id"], "problem_id": r["problem_id"],
                                     "correct": r["correct"], "date": day.isoformat()})
    bank.save()
    return {"student": s["name"], "recorded": len(results), "date": day.isoformat()}


def _history(student_id: str) -> dict[str, list[dict]]:
    by_problem: dict[str, list[dict]] = defaultdict(list)
    for r in sorted(bank.data["results"], key=lambda r: r["date"]):
        if r["student_id"] == student_id:
            by_problem[r["problem_id"]].append(r)
    return by_problem


def _due_reviews(student_id: str) -> list[str]:
    """最後に解いたとき間違えていて、それから7日以上たっている問題。"""
    today = bank.today()
    return [pid for pid, rs in _history(student_id).items()
            if not rs[-1]["correct"] and (today - date.fromisoformat(rs[-1]["date"])).days >= REVIEW_AFTER_DAYS]


def _unit_stats(student_id: str) -> dict[str, dict]:
    stats: dict[str, dict] = defaultdict(lambda: {"answered": 0, "correct": 0})
    for r in bank.data["results"]:
        if r["student_id"] == student_id:
            unit = bank.problem(r["problem_id"])["unit"]
            stats[unit]["answered"] += 1
            stats[unit]["correct"] += r["correct"]
    return stats


def _weak_units(student_id: str) -> list[str]:
    stats = _unit_stats(student_id)
    weak = [u for u, s in stats.items() if s["answered"] >= 3 and s["correct"] / s["answered"] < WEAK_RATE]
    return sorted(weak, key=lambda u: stats[u]["correct"] / stats[u]["answered"])


@mcp.tool()
def student_progress(student: str) -> dict:
    """生徒の単元ごとの正答率、苦手な単元（3問以上解いて正答率60%未満）、復習が必要な問題。"""
    s = bank.student(student)
    stats = _unit_stats(s["id"])
    return {
        "student": s["name"], "grade": s["grade"],
        "units": {u: {**v, "rate": f"{v['correct'] / v['answered'] * 100:.0f}%"} for u, v in stats.items()},
        "weak_units": _weak_units(s["id"]),
        "due_reviews": [{"id": pid, "unit": bank.problem(pid)["unit"], "question": bank.problem(pid)["question"]}
                        for pid in _due_reviews(s["id"])],
    }


def _pick(student_id: str | None, unit: str | None, difficulty: int | None, count: int, include_reviews: bool,
          seed: int | None) -> list[tuple[dict, str]]:
    """(問題, 選んだ理由) の一覧。復習 → 苦手な単元 → 指定の単元（無ければ全体）の順。"""
    rng = random.Random(seed)
    today = bank.today()
    recent: set[str] = set()
    if student_id:
        for w in bank.data["worksheets"]:
            if w.get("student_id") == student_id and (today - date.fromisoformat(w["date"])).days < AVOID_REPEAT_DAYS:
                recent.update(w["problem_ids"])
    pool = [p for p in _confirmed() if (not difficulty or p["difficulty"] == difficulty)]
    chosen: list[tuple[dict, str]] = []
    used: set[str] = set()

    def take(cands: list[dict], reason: str, limit: int) -> None:
        cands = [p for p in cands if p["id"] not in used and p["id"] not in recent]
        rng.shuffle(cands)
        for p in sorted(cands, key=lambda p: p["difficulty"])[:limit]:
            chosen.append((p, reason))
            used.add(p["id"])

    solved = {pid for pid, rs in _history(student_id).items() if rs[-1]["correct"]} if student_id else set()
    if student_id and include_reviews:
        due = set(_due_reviews(student_id))
        take([p for p in _confirmed() if p["id"] in due], "復習（前に間違えた問題）", max(1, count * 3 // 10))
    if student_id and not unit:
        for weak in _weak_units(student_id):
            take([p for p in pool if p["unit"] == weak and p["id"] not in solved], f"苦手な単元（{weak}）", count - len(chosen))
    in_scope = [p for p in pool if not unit or p["unit"] == unit]
    label = unit or "全体から"
    take([p for p in in_scope if p["id"] not in solved], label, count - len(chosen))      # まだ正解していない問題から
    take(in_scope, label, count - len(chosen))                                            # 足りなければ正解済みも
    # プリントはやさしい順に並べる（選んだ理由はそのまま残す）
    return sorted(chosen[:count], key=lambda pr: pr[0]["difficulty"])


CSS = """
@page { size: A4; margin: 15mm; }
body { font-family: "Hiragino Kaku Gothic ProN", "Yu Gothic", "Noto Sans JP", sans-serif; color: #111; font-size: 11pt; }
h1 { font-size: 15pt; margin: 0 0 2mm; }
.meta { display: flex; justify-content: space-between; border-bottom: 2px solid #111; padding-bottom: 2mm; margin-bottom: 5mm; }
ol { padding-left: 7mm; }
li { margin-bottom: 10mm; }
.answer-space { border-bottom: 1px dotted #888; height: 8mm; margin-top: 3mm; }
.tag { font-size: 8pt; color: #666; }
.answers { page-break-before: always; }
.answers li { margin-bottom: 4mm; }
.answers .exp { font-size: 9.5pt; color: #333; }
@media screen { body { max-width: 180mm; margin: 10mm auto; } .answers { margin-top: 15mm; border-top: 3px double #111; padding-top: 5mm; } }
"""


@mcp.tool()
def make_worksheet(student: str | None = None, unit: str | None = None, count: int = 10,
                   difficulty: int | None = None, include_reviews: bool = True, title: str | None = None,
                   seed: int | None = None) -> dict:
    """確認済みの問題からプリント（問題ページ＋解答・解説ページ）を作って保存する。

    student を指定すると、復習が必要な問題（約3割）→ 苦手な単元 → unit（指定があれば）の順に選び、
    14日以内にその生徒に出した問題は避ける。作ったプリントは記録され、次回の重複を避けるのに使う。

    Args:
        student: 生徒の id か名前の一部（省略で生徒を決めないプリント）
        unit: 単元を指定（例: 一次方程式）
        count: 問題数
        difficulty: 難易度を 1〜3 で指定
        include_reviews: 復習の問題を入れるか
        title: プリントのタイトル
        seed: 同じ選び方を再現したいときの数
    """
    if not 1 <= count <= 30:
        raise ValueError("count は 1〜30 にしてください")
    s = bank.student(student) if student else None
    picked = _pick(s["id"] if s else None, unit, difficulty, count, include_reviews, seed)
    if not picked:
        raise ValueError("条件に合う確認済みの問題がありません。coverage で足りないところを確認してください")
    today = bank.today()
    esc = html.escape
    title = title or f"{unit or '復習と練習'}　プリント"
    questions = "".join(f"<li>{esc(p['question'])}<div class='tag'>{esc(p['unit'])}・難易度{p['difficulty']}"
                        f"{'・復習' if reason.startswith('復習') else ''}</div><div class='answer-space'></div></li>"
                        for p, reason in picked)
    answers = "".join(f"<li><b>{esc(p['answer'])}</b><div class='exp'>{esc(p['explanation'])}</div></li>" for p, _ in picked)
    page = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><title>{esc(title)}</title><style>{CSS}</style></head><body>
<h1>{esc(title)}</h1>
<div class="meta"><span>{esc(s['name']) + ' さん' if s else '名前：＿＿＿＿＿＿＿＿'}</span><span>{today:%Y年%m月%d日}</span><span>　／{len(picked)}</span></div>
<ol>{questions}</ol>
<div class="answers"><h1>解答・解説</h1><ol>{answers}</ol></div>
</body></html>
"""
    ws_id = f"W{len(bank.data['worksheets']) + 1}"
    bank.data["worksheets"].append({"id": ws_id, "student_id": s["id"] if s else None, "date": today.isoformat(),
                                    "problem_ids": [p["id"] for p, _ in picked]})
    bank.save()
    out_dir = Path(os.environ.get("BANK_EXPORT_DIR", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"プリント_{(s['name'] if s else '共通').replace(' ', '')}_{today.isoformat()}_{ws_id}.html"
    path.write_text(page, encoding="utf-8")
    return {"path": str(path.resolve()), "worksheet_id": ws_id,
            "problems": [{"id": p["id"], "unit": p["unit"], "difficulty": p["difficulty"], "reason": r} for p, r in picked]}


@mcp.prompt()
def fill_bank(subject: str = "数学", grade: str = "中1") -> str:
    """足りない単元・難易度の問題を作って溜める流れ（繰り返し使う）。"""
    return (
        f"{grade}{subject}の問題バンクを補充します。\n"
        f"1. coverage(subject=\"{subject}\", grade=\"{grade}\") で gaps（足りない単元・難易度）を確認してください。"
        "下書きが確認待ちで溜まっている所は後回しにしてください。\n"
        "2. 足りない所ごとに、教科書の範囲に沿った問題を作ってください。難易度1は基本の計算、2は2〜3段階の手順、3は応用です。\n"
        "3. 作った問題は、答えを別の方法でもう一度解いて確かめてから add_problems してください（source は AI生成）。\n"
        "4. 最後に、追加した数と、先生に確認してほしい問題の一覧を伝えてください。"
    )


@mcp.prompt()
def check_drafts() -> str:
    """先生の確認作業を手伝う流れ。"""
    return (
        "確認待ちの下書きを先生と一緒に確認します。\n"
        "1. review_queue で下書きを取得してください。\n"
        "2. 1問ずつ自分で解き直し、答え・解説と一致するか、問題文に不備がないかを確かめてください。\n"
        "3. 問題なさそうなもの／答えが合わないもの／直したほうがよいもの に分けて表にし、先生に見せてください。"
        "答えが合わないものは、どこが違うかを具体的に示してください。\n"
        "4. 先生の判断で approve / edit_problem / reject を行ってください。自分の判断だけで approve しないでください。"
    )


@mcp.prompt()
def parent_report(student: str) -> str:
    """保護者への報告の下書き。"""
    return (
        f"{student} さんの保護者向けの報告を下書きします。\n"
        "1. student_progress で単元ごとの正答率・苦手な単元・復習の状況を確認してください。\n"
        "2. できるようになったこと → 今取り組んでいること → 家庭でお願いしたいこと の順に、300字程度でまとめてください。\n"
        "3. 数字は student_progress の結果だけを使い、成績の予想や他の生徒との比較は書かないでください。"
    )


if __name__ == "__main__":
    mcp.run()
