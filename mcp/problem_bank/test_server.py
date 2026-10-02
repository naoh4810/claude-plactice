"""問題の追加と確認・単元の充足・生徒の進み具合・プリントの選び方のテスト。

実行: python -m pytest mcp/problem_bank/test_server.py
"""

import json
import os
import sys
from fractions import Fraction
from pathlib import Path

import pytest

os.environ.pop("BANK_DATA_PATH", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402


@pytest.fixture(autouse=True)
def sample_bank(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bank", server.Bank(None))
    monkeypatch.setenv("BANK_EXPORT_DIR", str(tmp_path))


def problem(pid):
    return server.bank.problem(pid)


# --- サンプルの答えが正しいこと（生徒に見せるデモなので、答えを別の方法で確かめる） -------------------

def test_sample_arithmetic_answers():
    computed = {"P001": -3 + 8, "P002": -7 - (-2), "P003": (-4) * 3 - (-6), "P004": (-2) ** 3,
                "P005": -(3 ** 2) + (-3) ** 2, "P006": 12 // (-4) * (-2) ** 2, "P011": 3 * (-2) ** 2 - (-2),
                "P019": 3 * 4}
    for pid, value in computed.items():
        assert problem(pid)["answer"].replace("−", "-") == str(value), pid


def test_sample_equation_answers_satisfy_the_equation():
    def x_of(pid):
        return Fraction(problem(pid)["answer"].replace("x＝", "").replace("−", "-"))
    checks = {
        "P013": lambda x: x + 7 == 12, "P014": lambda x: 3 * x == -18, "P015": lambda x: 4 * x - 5 == 2 * x + 9,
        "P016": lambda x: 3 * (x - 1) == x + 5, "P017": lambda x: x / 2 - 1 == x / 3,
        "P018": lambda x: Fraction(3, 10) * x + Fraction(12, 10) == Fraction(5, 10) * x - Fraction(4, 10),
        "P022": lambda x: -2 * x == 10,
    }
    for pid, holds in checks.items():
        assert holds(x_of(pid)), pid
    a = Fraction(problem("P024")["answer"].replace("a＝", "").replace("−", "-"))
    assert a * -4 == 6                                   # y＝ax が(−4, 6)を通る
    assert Fraction(-12, -3) == int(problem("P023")["answer"])   # a＝2×(−6)＝−12、x＝−3 のとき


def test_wrong_draft_is_still_a_draft():
    p = problem("P025")                                  # 2x＋3＝11 の答えが x＝7 になっている（正しくは 4）
    assert p["status"] == "下書き" and 2 * 7 + 3 != 11


# --- 追加と確認 ---------------------------------------------------------------------

def test_add_problems_draft_vs_own_and_duplicates():
    res = server.add_problems([
        {"subject": "数学", "grade": "中1", "unit": "一次方程式", "difficulty": 1, "question": "方程式 x−4＝9 を解きなさい。",
         "answer": "x＝13", "source": "AI生成"},
        {"subject": "数学", "grade": "中1", "unit": "一次方程式", "difficulty": 1, "question": "方程式 2x＝14 を解きなさい。",
         "answer": "x＝7", "source": "自作"},
        {"subject": "数学", "grade": "中1", "unit": "一次方程式", "difficulty": 1,
         "question": "方程式  x＋7＝12  を解きなさい。", "answer": "x＝5"},                       # 空白違いの重複
        {"subject": "数学", "grade": "中1", "unit": "一次方程式", "difficulty": 4, "question": "?", "answer": "?"},
        {"subject": "数学", "grade": "中1", "unit": "一次方程式", "difficulty": 1, "question": "答えなし"},
    ])
    assert res["added"] == ["P028", "P029"] and res["duplicates"] == 1 and len(res["errors"]) == 2
    assert problem("P028")["status"] == "下書き" and problem("P029")["status"] == "確認済み"


def test_review_flow():
    assert [p["id"] for p in server.review_queue()] == ["P025", "P026", "P027"]
    server.edit_problem("P025", answer="x＝4", explanation="2x＝8、x＝4。")
    server.approve(["P025", "P026"])
    server.reject("P027", "反比例はまだ習っていない範囲")
    assert server.review_queue() == []
    assert problem("P027")["status"] == "却下" and problem("P025")["answer"] == "x＝4"
    with pytest.raises(ValueError, match="下書きではありません"):
        server.approve(["P001"])
    server.edit_problem("P001", answer="5（＋5）")
    assert problem("P001")["status"] == "下書き"        # 確認済みを直したら確認し直す


def test_coverage_gaps_and_rejected_are_ignored():
    gaps = {(g["unit"], g["difficulty"]): g for g in server.coverage()["gaps"]}
    assert gaps[("一次方程式", 1)] == {"subject": "数学", "grade": "中1", "unit": "一次方程式", "difficulty": 1,
                                        "need": 1, "drafts_waiting": 1}
    server.reject("P025", "答えが違う")
    gaps = {(g["unit"], g["difficulty"]): g for g in server.coverage()["gaps"]}
    assert gaps[("一次方程式", 1)]["drafts_waiting"] == 0


# --- 生徒 ---------------------------------------------------------------------------

def test_student_progress_weak_units_and_due_reviews():
    ichiro = server.student_progress("一郎")
    assert ichiro["units"]["一次方程式"] == {"answered": 7, "correct": 3, "rate": "43%"}
    assert ichiro["weak_units"] == ["一次方程式"]
    # P016・P017 は 9/12 に間違えたまま（19日前）→ 復習。P018 は 9/26（5日前）なのでまだ
    assert {d["id"] for d in ichiro["due_reviews"]} == {"P016", "P017"}
    assert server.student_progress("花")["weak_units"] == ["正負の数"]


def test_record_results_validation():
    with pytest.raises(ValueError, match="P999"):
        server.record_results("花", [{"problem_id": "P999", "correct": True}])
    with pytest.raises(ValueError, match="correct"):
        server.record_results("花", [{"problem_id": "P001", "correct": "まる"}])
    server.record_results("花", [{"problem_id": "P002", "correct": True}])
    assert "P002" not in {d["id"] for d in server.student_progress("花")["due_reviews"]}


# --- プリント -------------------------------------------------------------------------

def test_worksheet_order_reviews_weak_units_and_no_drafts():
    res = server.make_worksheet(student="一郎", count=6, seed=1)
    reasons = [p["reason"] for p in res["problems"]]
    ids = [p["id"] for p in res["problems"]]
    assert any(r.startswith("復習") for r in reasons)
    assert any(r == "苦手な単元（一次方程式）" for r in reasons)
    assert not {"P025", "P026", "P027"} & set(ids)                      # 下書きは出さない
    assert [p["difficulty"] for p in res["problems"]] == sorted(p["difficulty"] for p in res["problems"])
    page = Path(res["path"]).read_text(encoding="utf-8")
    assert "サンプル 一郎 さん" in page and "解答・解説" in page


def test_worksheet_avoids_recent_repeats_and_prefers_unsolved():
    first = {p["id"] for p in server.make_worksheet(student="一郎", count=6, seed=1)["problems"]}
    second = {p["id"] for p in server.make_worksheet(student="一郎", count=6, seed=1)["problems"]}
    assert not first & second
    solved_by_ichiro = {"P013", "P014", "P001", "P002", "P007", "P009", "P015", "P003"}
    assert not solved_by_ichiro & (first | second)


def test_worksheet_for_unit_without_student():
    res = server.make_worksheet(unit="比例と反比例", count=10)
    assert {p["unit"] for p in res["problems"]} == {"比例と反比例"} and len(res["problems"]) == 6
    with pytest.raises(ValueError, match="確認済みの問題がありません"):
        server.make_worksheet(unit="図形", count=5)


def test_bank_persists(tmp_path, monkeypatch):
    path = tmp_path / "bank.json"
    monkeypatch.setattr(server, "bank", server.Bank(path))
    s = server.add_student("A.K", "中2", ["数学"])
    server.add_problems([{"subject": "数学", "grade": "中2", "unit": "連立方程式", "difficulty": 1,
                         "question": "x＋y＝5、x−y＝1 を解きなさい。", "answer": "x＝3、y＝2", "source": "自作"}])
    server.record_results(s["id"], [{"problem_id": "P001", "correct": True}])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["students"][0]["name"] == "A.K" and saved["results"][0]["problem_id"] == "P001"
    assert server.Bank(path).problem("P001")["status"] == "確認済み"
