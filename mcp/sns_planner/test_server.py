"""サンプルデータと一時ファイルでツールの動きを確かめるテスト。

実行: python -m pytest mcp/sns_planner/test_server.py
"""

import csv
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.pop("SNS_DATA_PATH", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
from rules import check, count_chars  # noqa: E402
from store import Planner  # noqa: E402


@pytest.fixture(autouse=True)
def sample_planner(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "planner", Planner(None))
    monkeypatch.setenv("SNS_EXPORT_DIR", str(tmp_path))


# --- ルール -----------------------------------------------------------------

def test_x_counts_japanese_as_two_and_urls_as_23():
    assert count_chars("x", "あいう abc") == 6 + 4
    assert count_chars("x", "詳細 https://example.com/a/very/long/path") == 4 + 1 + 23
    assert check("x", "あ" * 140)["ok"]
    assert not check("x", "あ" * 141)["ok"]
    assert check("threads", "あ" * 141)["ok"]          # Threads は重み付けしない


def test_instagram_rules():
    too_many = " ".join(f"#tag{i}" for i in range(31))
    assert "ハッシュタグが多すぎます" in check("instagram", too_many, has_media=True)["errors"][0]
    warnings = check("instagram", "詳しくは https://example.com", has_media=False)["warnings"]
    assert any("画像か動画" in w for w in warnings) and any("タップできません" in w for w in warnings)


def test_threads_allows_one_topic_tag_and_fullwidth_hash_counts():
    assert check("threads", "秋の和菓子 #和菓子")["ok"]
    assert not check("threads", "秋の和菓子 #和菓子 ＃秋")["ok"]


def test_unknown_platform():
    with pytest.raises(ValueError, match="未対応"):
        check("facebook", "hi")


# --- 下書き → 予定 → 投稿 → 数字 ----------------------------------------------

def test_full_flow_from_idea_to_report():
    idea = server.add_idea("新しいカフェ", notes="10/10オープン", tags=["お店紹介"])
    assert idea["id"] == "idea-6"
    assert server.list_ideas(unused_only=True)[-1]["id"] == "idea-6"

    bad = server.save_draft(idea["id"], "x", "あ" * 200)
    assert not bad["saved"] and bad["check"]["errors"]

    saved = server.save_draft(idea["id"], "x", "10/10に新しいカフェがオープン。 #サンプル町")
    post_id = saved["post"]["id"]
    assert post_id == "post-11" and saved["post"]["status"] == "draft"

    scheduled = server.schedule_post(post_id, "2026-09-29T20:00")
    assert scheduled["scheduled"]
    assert any("post-10" in w for w in scheduled["warnings"])      # 同じ日の12時に X の予定がある

    server.mark_posted(post_id)
    server.record_metrics(post_id, impressions=1000, likes=50, comments=5, shares=5, saves=0, clicks=12)
    report = server.performance_report(date_from="2026-09-28", date_to="2026-09-30")
    assert report["posts"] == 1
    assert report["by_platform"]["x"]["engagement_rate"] == "6.0%"
    assert report["top_posts"][0]["id"] == post_id


def test_edit_draft_revalidates_and_blocks_posted():
    res = server.edit_draft("post-9", text="#a " * 40)
    assert not res["saved"]
    assert server.edit_draft("post-9", media="朝市のチラシ画像")["saved"]
    with pytest.raises(ValueError, match="投稿済み"):
        server.edit_draft("post-1", text="直したい")


def test_record_metrics_requires_posted_and_merges():
    with pytest.raises(ValueError, match="mark_posted"):
        server.record_metrics("post-9", likes=1)
    merged = server.record_metrics("post-1", likes=70)
    assert merged["metrics"]["likes"] == 70 and merged["metrics"]["impressions"] == 820
    with pytest.raises(ValueError, match="マイナス"):
        server.record_metrics("post-1", likes=-1)


# --- カレンダーとレポート ------------------------------------------------------

def test_calendar_shows_weekly_shortfalls_and_unscheduled_drafts():
    info = server.calendar(date_from="2026-09-28", date_to="2026-10-04")
    assert [p["id"] for p in info["posts"]] == ["post-10"]
    assert info["weeks"] == [{"week_of": "2026-09-28", "planned_or_posted": {"x": 1},
                              "short": {"instagram": 2, "x": 2, "threads": 1, "tiktok": 1}}]
    assert [d["id"] for d in info["unscheduled_drafts"]] == ["post-9"]


def test_performance_report_ranks_by_engagement_rate():
    report = server.performance_report()
    assert report["period"] == ["2026-09-14", "2026-09-27"]
    assert report["posts"] == 8 and report["without_metrics"] == []
    assert [p["id"] for p in report["top_posts"]] == ["post-6", "post-1", "post-3"]
    assert report["by_platform"]["instagram"]["engagement_rate"] == "12.2%"


def test_export_calendar_writes_excel_csv():
    res = server.export_calendar(date_from="2026-09-14", date_to="2026-09-30")
    raw = Path(res["path"]).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines()))
    assert rows[0] == ["日時", "SNS", "状態", "ネタ", "本文", "素材"]
    assert rows[1][:3] == ["2026-09-15 12:10", "X（旧Twitter）", "投稿済み"]
    assert res["rows"] == 9


# --- ファイル保存 ---------------------------------------------------------------

def test_file_mode_persists_between_restarts(tmp_path, monkeypatch):
    path = tmp_path / "sns.json"
    monkeypatch.setattr(server, "planner", Planner(path))
    assert server.set_weekly_targets({"threads": 1, "x": 2}, account="テスト店")["weekly_targets"] == {"threads": 1, "x": 2}
    assert server.set_weekly_targets({"x": 0})["weekly_targets"] == {"threads": 1}
    idea = server.add_idea("はじめてのネタ")
    server.save_draft(idea["id"], "threads", "はじめての投稿です")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert [p["text"] for p in data["posts"]] == ["はじめての投稿です"]
    reloaded = Planner(path)                                           # 読み直しても同じ内容
    assert reloaded.post("post-1")["idea_id"] == "idea-1"
    assert reloaded.data["weekly_targets"] == {"threads": 1} and reloaded.data["account"] == "テスト店"
