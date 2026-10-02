"""相性の判定・候補・卓割り・記録とフォロー・取り込みのテスト。

実行: python -m pytest mcp/event_matching/test_server.py
"""

import itertools
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.pop("EVENT_DATA_PATH", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
from matching import Person, pair_score, phrase_match, phrases  # noqa: E402


@pytest.fixture(autouse=True)
def sample_events(monkeypatch):
    monkeypatch.setattr(server, "events", server.Events(None))


# --- 相性の判定 -----------------------------------------------------------------------

def test_phrases_keep_nakaguro_words_together():
    assert phrases("経理・確定申告、ホームページ制作,  名刺 / チラシ") == ["経理・確定申告", "ホームページ制作", "名刺", "チラシ"]


def test_phrase_match_by_words_and_themes():
    assert phrase_match("プロフィール写真", "写真撮影") == "撮影"
    assert phrase_match("集客の相談", "Instagram運用代行") == "集客"          # 集客は SNS で満たせる
    assert phrase_match("ＬＩＮＥの運用", "line公式アカウント構築") == "SNS・LINE"   # 全角・大文字の揺れ
    assert phrase_match("採用に困っている会社", "ホームページ制作") is None
    assert phrase_match("採用に困っている会社", "採用の相談") is None      # お客さん探しは同業者に当てない


def test_pair_score_penalties_and_bonus():
    a = Person("A", industry="Web", wants=["チラシ"], gives=["ホームページ"], visits=1)
    b = Person("B", industry="印刷", wants=["ホームページ"], gives=["チラシ印刷"], visits=5)
    score, why = pair_score(a, b, set())
    assert len(why) == 2 and score == 2.5                     # 双方向 2 ＋ 初参加と常連 0.5
    assert pair_score(a, b, {frozenset(("A", "B"))})[0] == 2.5 - 5.0


# --- 候補 -----------------------------------------------------------------------------

def test_suggestions_skip_people_without_consent_and_mark_met():
    everyone = server.suggestions(top=20)
    assert not any(s["name"] == "工藤 翼" for r in everyone for s in r["suggestions"])
    kudo = server.suggestions("工藤")[0]                       # 本人には候補を出す
    assert kudo["suggestions"] and kudo["newcomer"]
    inoue = server.suggestions("井上", top=20)[0]["suggestions"]
    assert not any(s["name"] in ("青木 さくら", "上田 みなみ") for s in inoue)   # 9月に会った相手は 0 以下に下がる


def test_unmatched_wants_list_client_seeking_wants():
    got = {(u["person"], u["want"]) for u in server.unmatched_wants()}
    assert ("青木 さくら", "顧問先の紹介") in got and ("小林 恵", "採用に困っている会社") in got
    kinds = {(u["person"], u["want"]): u["kind"] for u in server.unmatched_wants()}
    assert kinds[("高田 直", "採用に困っている会社")] == "お客さん探し"
    assert kinds[("千葉 拓", "イベント出店先")] == "参加者にいない"
    assert not any(u["person"] == "江藤 誠" for u in server.unmatched_wants())


def test_person_lookup_errors():
    with pytest.raises(ValueError, match="0 人"):
        server.suggestions("存在しない人")


# --- 卓割り ---------------------------------------------------------------------------

def test_table_plan_has_no_repeats_and_no_newcomer_only_tables():
    plan = server.table_plan(table_size=4, rounds=3, seed=1)
    newcomers = {p["name"] for p in server.participants()["people"] if p["newcomer"]}
    seen = []
    for rnd in plan["rounds"]:
        members = [m for t in rnd["tables"] for m in t["members"]]
        assert sorted(members) == sorted(p["name"] for p in server.participants()["people"])   # 全員1回ずつ
        for t in rnd["tables"]:
            assert not set(t["members"]) <= newcomers
            seen += [frozenset(p) for p in itertools.combinations(t["members"], 2)]
    assert plan["repeated_pairs"] == 0 and len(seen) == len(set(seen))


def test_table_plan_beats_random_seating():
    people = server.events.people(server._event(None))
    by = {p.name: p for p in people}
    plan = server.table_plan(table_size=4, rounds=1, seed=0)["rounds"][0]["tables"]

    def total(tables):
        return sum(pair_score(by[a], by[b], set())[0] for t in tables for a, b in itertools.combinations(t, 2))
    names = sorted(by)
    naive = [names[i:i + 4] for i in range(0, len(names), 4)]
    assert total([t["members"] for t in plan]) > total(naive)


def test_table_plan_validation():
    with pytest.raises(ValueError, match="table_size"):
        server.table_plan(table_size=1)


# --- 記録とフォロー ---------------------------------------------------------------------

def test_record_meetings_and_follow_ups():
    with pytest.raises(ValueError, match="卓割りがありません"):
        server.record_meetings()
    server.table_plan(rounds=2, seed=1)
    first = server.record_meetings()
    assert first["recorded_pairs"] == 2 * 4 * 6                   # 2ラウンド×4卓×(4人の組 6)
    assert server.record_meetings()["recorded_pairs"] == 0        # 二重に記録しない
    f = server.follow_ups()
    met = server.events.met()
    assert all(frozenset(r["pair"]) in met for r in f["met_good_pairs"])
    assert all(frozenset(r["pair"]) not in met for r in f["missed_good_pairs"])
    kudo_rows = [r for r in f["met_good_pairs"] + f["missed_good_pairs"] if "工藤 翼" in r["pair"]]
    assert kudo_rows and not any(r["can_introduce"] for r in kudo_rows)


def test_record_extra_pairs_validation():
    with pytest.raises(ValueError, match="参加者2人"):
        server.record_meetings(use_plan=False, extra_pairs=[["青木 さくら", "知らない人"]])
    assert server.record_meetings(use_plan=False, extra_pairs=[["青木 さくら", "斉藤 光"]])["recorded_pairs"] == 1


def test_pair_detail_for_introduction():
    d = server.pair_detail("青木", "斉藤")
    assert d["can_introduce"] and d["reasons"] and not d["met_before"]
    assert server.pair_detail("工藤", "杉山")["can_introduce"] is False


# --- 取り込みと保存 ---------------------------------------------------------------------

def test_import_cp932_aliases_and_missing_consent(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "events", server.Events(tmp_path / "events.json"))
    csv_path = tmp_path / "form.csv"
    csv_path.write_bytes("名前,屋号,できること,困りごと\nA,a,ホームページ制作,集客\nB,b,チラシ,ホームページ\n,c,x,y\n"
                         .encode("cp932"))
    res = server.import_participants(str(csv_path), "11月会", "2026-11-20")
    assert res["participants"] == 2 and res["no_consent_column"] and "4行目" in res["problems"][0]
    assert all(not p["consent"] for p in server.participants("11月会")["people"])   # 同意の列が無ければ紹介しない
    saved = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    assert saved["events"]["11月会"]["participants"][0]["gives"] == ["ホームページ制作"]
    assert server.Events(tmp_path / "events.json").people("11月会")[1].name == "B"


def test_import_requires_columns(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("名前,メール\nA,a@example.com\n", encoding="utf-8")
    with pytest.raises(ValueError, match="列が必要"):
        server.import_participants(str(csv_path), "x", "2026-11-01")
