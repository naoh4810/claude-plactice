"""社内ナレッジ検索MCP：社内資料フォルダを検索し、出典付きで答えるためのツール群。

規程集・マニュアル・FAQ などを1つのフォルダに入れておくと、Claude が該当箇所を探し、
「どの資料のどこに書いてあるか」を添えて回答する。資料に無いことは無いと答えさせる。

できること（ツール）:
  - list_documents : 検索対象の資料一覧（読めなかった資料と理由も）
  - search         : キーワード・質問文で該当箇所を探す（出典の位置と抜粋付き）
  - read_section   : 資料の該当箇所の全文を読む（回答前の裏取り用）

起動:
  python mcp/knowledge_search/server.py
  （KNOWLEDGE_DIR が無ければ架空の会社の規程集 sample_docs/ を使うサンプルモード）
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from index import Index, snippet
from loader import load_folder

SAMPLE_DIR = Path(__file__).with_name("sample_docs")

mcp = MCPServer("knowledge-search")


class Library:
    """資料フォルダと検索インデックス。資料が追加・更新されたら次の呼び出しで作り直す。"""

    def __init__(self, root: Path):
        if not root.is_dir():
            raise ValueError(f"資料フォルダがありません: {root}")
        self.root = root.resolve()
        self._mtimes: dict[str, float] | None = None
        self.refresh()

    def refresh(self) -> None:
        current = {
            p.relative_to(self.root).as_posix(): p.stat().st_mtime
            for p in self.root.rglob("*") if p.is_file()
        }
        if current == self._mtimes:
            return
        self.chunks, self.skipped, _ = load_folder(self.root)
        self.index = Index(self.chunks)
        self._mtimes = current


def _library() -> Library:
    library.refresh()
    return library


library = Library(Path(os.environ.get("KNOWLEDGE_DIR") or SAMPLE_DIR))


@mcp.tool()
def list_documents() -> dict:
    """検索対象の資料一覧と、それぞれの検索単位（チャンク）の数。読めなかった資料は理由付きで返す。"""
    lib = _library()
    counts = Counter(c.path for c in lib.chunks)
    return {
        "mode": "sample" if lib.root == SAMPLE_DIR.resolve() else "folder",
        "documents": [{"path": path, "chunks": n} for path, n in sorted(counts.items())],
        "skipped": lib.skipped,
    }


@mcp.tool()
def search(query: str, top_k: int = 5, path_prefix: str | None = None) -> list[dict]:
    """資料を検索し、関連度の高い箇所を出典（資料のパスと位置）・抜粋付きで返す。

    言い回しを変えて何度か検索すると取りこぼしが減る（例:「有給」「年次有給休暇」「休暇 申請」）。
    回答に使う前に read_section で該当箇所の全文を確認すること。

    Args:
        query: 探したい言葉や質問文
        top_k: 返す件数（最大20）
        path_prefix: 資料のパスの先頭で絞り込む（例: "規程/"）
    """
    lib = _library()
    hits = lib.index.search(query, top_k=max(1, min(top_k, 20)), path_prefix=path_prefix)
    return [
        {"path": c.path, "location": c.location, "score": round(score, 2), "snippet": snippet(c.text, query)}
        for c, score in hits
    ]


@mcp.tool()
def read_section(path: str, location: str | None = None) -> dict:
    """資料の該当箇所の全文を返す。location を省略すると資料全体（長い場合は先頭から）。

    Args:
        path: search が返した資料のパス
        location: search が返した位置（見出しやページ）。前方一致で、その配下の見出しもまとめて返す
    """
    lib = _library()
    chunks = [c for c in lib.chunks if c.path == path]
    if not chunks:
        raise ValueError(f"資料「{path}」はありません。list_documents で一覧を確認してください")
    if location:
        chunks = [c for c in chunks if c.location.startswith(location)]
        if not chunks:
            raise ValueError(f"「{path}」に位置「{location}」はありません")
    text, truncated = "", False
    for c in chunks:
        part = f"【{c.location}】\n{c.text}\n\n"
        if len(text) + len(part) > 8000:
            truncated = True
            break
        text += part
    return {"path": path, "text": text.strip(), "truncated": truncated}


@mcp.prompt()
def answer_with_citations(question: str) -> str:
    """社内資料だけを根拠に、出典付きで質問に答える流れ。"""
    return (
        f"社内資料をもとに次の質問に答えてください: {question}\n\n"
        "1. search で関連箇所を探してください。言い回しを変えて2〜3回検索し、取りこぼしを減らしてください。\n"
        "2. 使えそうな箇所は read_section で全文を読み、条件や例外を確認してください。\n"
        "3. 回答は結論を先に書き、各文の根拠に［資料のパス／位置］の形で出典を付けてください。\n"
        "4. 資料に書かれていないことは推測で補わず、「資料には記載がありません」と明記し、"
        "誰に確認すればよいか資料に書かれていればそれも添えてください。"
    )


if __name__ == "__main__":
    mcp.run()
