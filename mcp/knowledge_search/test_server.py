"""サンプル資料と一時フォルダでツールの動きを確かめるテスト。

実行: python -m pytest mcp/knowledge_search/test_server.py
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.pop("KNOWLEDGE_DIR", None)
sys.path.insert(0, str(Path(__file__).parent))

import server  # noqa: E402
from index import tokenize  # noqa: E402


@pytest.fixture(autouse=True)
def sample_library(monkeypatch):
    monkeypatch.setattr(server, "library", server.Library(server.SAMPLE_DIR))


def top(query, **kw):
    hits = server.search(query, **kw)
    return (hits[0]["path"], hits[0]["location"]) if hits else None


def test_tokenize_uses_bigrams_and_drops_hiragana_only():
    assert tokenize("有給休暇はいい？") == ["有給", "給休", "休暇", "暇は"]
    assert tokenize("ＣｈａｔＧＰＴ PC") == ["chatgpt", "pc"]   # 全角も正規化


@pytest.mark.parametrize("query, path, location_end", [
    ("有給休暇は何日前までに申請？", "規程/就業規則.md", "第20条 年次有給休暇"),
    ("タクシー代は精算できる？", "マニュアル/経費精算マニュアル.md", "3.2 タクシー"),
    ("PCを家に持ち帰りたい", "規程/情報セキュリティ規程.md", "第5条 パソコンの社外持ち出し"),
    ("慶弔休暇 祖父", "規程/就業規則.md", "第21条 慶弔休暇"),
])
def test_search_finds_the_right_section(query, path, location_end):
    got_path, got_location = top(query)
    assert got_path == path and got_location.endswith(location_end)


def test_faq_text_is_cited_by_line():
    assert top("引っ越ししたら") == ("FAQ/総務FAQ.txt", "3行目")


def test_path_prefix_limits_search():
    hits = server.search("申請", path_prefix="マニュアル/")
    assert hits and all(h["path"].startswith("マニュアル/") for h in hits)


def test_read_section_returns_full_text_under_heading():
    res = server.read_section("規程/就業規則.md", "就業規則（抜粋） > 第3章 休暇")
    assert "【就業規則（抜粋） > 第3章 休暇 > 第20条 年次有給休暇】" in res["text"]
    assert "半日単位の取得は年10回まで" in res["text"]
    assert "第21条 慶弔休暇" in res["text"]
    assert "第10条" not in res["text"]


def test_read_section_rejects_unknown_document():
    with pytest.raises(ValueError, match="list_documents"):
        server.read_section("../../../etc/passwd")


def test_new_and_unreadable_files_are_picked_up(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("# 在宅勤務\n\n在宅勤務は週2日まで。", encoding="utf-8")
    monkeypatch.setattr(server, "library", server.Library(tmp_path))
    assert top("在宅勤務") == ("a.md", "在宅勤務")

    (tmp_path / "b.txt").write_text("駐車場は申請制です。", encoding="utf-8")
    (tmp_path / "broken.docx").write_bytes(b"not a real docx")
    assert top("駐車場") == ("b.txt", "1行目")          # 追加した資料が次の呼び出しで検索に入る
    docs = server.list_documents()
    assert [d["path"] for d in docs["documents"]] == ["a.md", "b.txt"]
    assert "broken.docx" in docs["skipped"]
