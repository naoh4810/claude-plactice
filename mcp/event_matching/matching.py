"""参加者どうしの相性（探していること ↔ 提供できること）と、席替えの計画。

日本語は言い回しの揺れが大きい（「撮影できるカメラマン」と「プロフィール写真の撮影」など）ので、
言葉の一致だけでなく、言葉をまとめた「テーマ」で突き合わせる。テーマは CONCEPTS で足せる。
"""

from __future__ import annotations

import itertools
import random
import unicodedata
from dataclasses import dataclass, field

# テーマ → そのテーマに入る言葉
CONCEPTS: dict[str, list[str]] = {
    "撮影": ["撮影", "カメラ", "写真"],
    "ホームページ": ["ホームページ", "web", "サイト", "seo"],
    "SNS・LINE": ["sns", "line", "インスタ", "instagram"],
    "チラシ・印刷": ["チラシ", "名刺", "印刷", "デザイン", "販促"],
    "集客": ["集客"],
    "税務・経理": ["確定申告", "税務", "節税", "税理士", "経理", "記帳", "会計"],
    "採用・人事": ["採用", "求人", "人事"],
    "創業・手続き": ["創業", "許認可", "補助金", "開業"],
    "不動産": ["不動産", "物件", "空き家"],
    "リフォーム・工事": ["リフォーム", "工務店", "外壁", "水回り"],
    "食・ギフト": ["パン", "焼き菓子", "ケータリング", "飲食", "野菜", "直売", "ギフト", "食材"],
    "出店": ["出店"],
    "業務の自動化・AI": ["自動化", "ai", "業務効率", "dx"],
    "家計・保険": ["家計", "保険", "教育資金"],
}
# 「探していること」のテーマを、どのテーマの「提供できること」が満たすか（自分自身に加えて）
SATISFIED_BY: dict[str, set[str]] = {
    "集客": {"ホームページ", "SNS・LINE", "チラシ・印刷"},
}

# お客さんを探している言い方。「採用に困っている会社」は採用の専門家ではなく、困っている会社が相手なので
# テーマでは突き合わせず、主催者が個別につなぐ材料（unmatched_wants）に回す
CLIENT_SEEKING = ("困っている", "必要とする", "顧問先", "お客", "見込み客")

MET_BEFORE_PENALTY = 5.0
SAME_INDUSTRY_PENALTY = 0.5
NEWCOMER_WITH_REGULAR_BONUS = 0.5


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower().strip()


def concepts_of(phrase: str) -> set[str]:
    p = normalize(phrase)
    return {c for c, words in CONCEPTS.items() if any(w in p for w in words)}


def phrases(text: str) -> list[str]:
    """「、」「,」「/」や改行で区切る。「・」は語の一部（経理・確定申告）なので区切らない。"""
    for sep in ("，", ",", "/", "／", "\n"):
        text = (text or "").replace(sep, "、")
    return [p.strip() for p in text.split("、") if p.strip()]


@dataclass
class Person:
    name: str
    business: str = ""
    industry: str = ""
    gives: list[str] = field(default_factory=list)
    wants: list[str] = field(default_factory=list)
    visits: int = 1
    consent: bool = True

    @property
    def newcomer(self) -> bool:
        return self.visits <= 1


def is_client_seeking(want: str) -> bool:
    return any(m in want for m in CLIENT_SEEKING)


def phrase_match(want: str, give: str) -> str | None:
    """want が give で満たされるなら、その理由（テーマ名か「同じ言葉」）を返す。"""
    if is_client_seeking(want):
        return None
    w, g = normalize(want), normalize(give)
    if w and g and (w in g or g in w):
        return "同じ言葉"
    want_c, give_c = concepts_of(want), concepts_of(give)
    for c in sorted(want_c):
        if c in give_c or SATISFIED_BY.get(c, set()) & give_c:
            return c
    return None


def reasons(a: Person, b: Person) -> list[dict]:
    """a の探していることを b が満たす組み合わせ（1つの「探していること」につき1つまで）。"""
    out = []
    for want in a.wants:
        for give in b.gives:
            why = phrase_match(want, give)
            if why:
                out.append({"seeker": a.name, "want": want, "provider": b.name, "give": give, "theme": why})
                break
    return out


def pair_score(a: Person, b: Person, met: set[frozenset]) -> tuple[float, list[dict]]:
    found = reasons(a, b) + reasons(b, a)
    score = float(len(found))
    if a.newcomer != b.newcomer:
        score += NEWCOMER_WITH_REGULAR_BONUS
    if a.industry and a.industry == b.industry:
        score -= SAME_INDUSTRY_PENALTY
    if frozenset((a.name, b.name)) in met:
        score -= MET_BEFORE_PENALTY
    return score, found


def plan_tables(people: list[Person], table_size: int, rounds: int, met: set[frozenset], seed: int = 0,
                iterations: int = 3000) -> list[list[list[str]]]:
    """ラウンドごとの卓割り。同じ二人が二度同じ卓にならないよう、相性の合計が高い配置を探す（山登り法）。"""
    rng = random.Random(seed)
    names = [p.name for p in people]
    by_name = {p.name: p for p in people}
    scores = {frozenset((a.name, b.name)): pair_score(a, b, met)[0] for a, b in itertools.combinations(people, 2)}
    used: set[frozenset] = set()
    plan = []
    n_tables = max(1, -(-len(names) // table_size))

    def table_value(table: list[str]) -> float:
        value = 0.0
        for x, y in itertools.combinations(table, 2):
            key = frozenset((x, y))
            value += scores[key] - (MET_BEFORE_PENALTY if key in used else 0)
        members = [by_name[n] for n in table]
        if any(m.newcomer for m in members) and not any(not m.newcomer for m in members):
            value -= 1.0                         # 初参加だけの卓は避ける
        return value

    for _ in range(rounds):
        order = names[:]
        rng.shuffle(order)
        tables = [order[i::n_tables] for i in range(n_tables)]
        for _ in range(iterations):
            t1, t2 = rng.sample(range(n_tables), 2) if n_tables > 1 else (0, 0)
            if t1 == t2:
                break
            i, j = rng.randrange(len(tables[t1])), rng.randrange(len(tables[t2]))
            before = table_value(tables[t1]) + table_value(tables[t2])
            tables[t1][i], tables[t2][j] = tables[t2][j], tables[t1][i]
            if table_value(tables[t1]) + table_value(tables[t2]) < before:
                tables[t1][i], tables[t2][j] = tables[t2][j], tables[t1][i]
        for table in tables:
            used.update(frozenset(p) for p in itertools.combinations(table, 2))
        plan.append([sorted(t) for t in tables])
    return plan
