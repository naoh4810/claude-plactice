"""社内資料フォルダの読み込みと、検索用の小さな塊（チャンク）への分割。

対応形式:
  - .md / .txt : 標準ライブラリだけで読む。Markdown は見出しの階層を位置情報にする
  - .pdf       : pypdf が入っていれば読む。ページ番号を位置情報にする
  - .docx      : python-docx が入っていれば読む。見出しスタイルを位置情報にする
ライブラリが無い形式は読み飛ばし、skipped として理由を返す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS = 500
SUPPORTED = {".md", ".txt", ".pdf", ".docx"}


@dataclass
class Chunk:
    path: str          # 資料フォルダからの相対パス
    location: str      # 「見出し > 見出し」や「p.3」
    text: str


def _split_long(text: str) -> list[str]:
    """段落単位でまとめ、MAX_CHARS を超えたら次の塊にする。"""
    parts, current = [], ""
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        if current and len(current) + len(para) > MAX_CHARS:
            parts.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return parts


def _text_chunks(rel: str, text: str) -> list[Chunk]:
    """テキストは段落ごとに1チャンク。位置は段落の開始行（FAQ の1問1答がそのまま出典になる）。"""
    chunks, para, start = [], [], 0
    for number, line in enumerate(text.splitlines() + [""], start=1):
        if line.strip():
            if not para:
                start = number
            para.append(line)
        elif para:
            chunks.extend(Chunk(rel, f"{start}行目", part) for part in _split_long("\n".join(para)))
            para = []
    return chunks


def _markdown_chunks(rel: str, text: str) -> list[Chunk]:
    chunks, headings, body = [], [], []

    def flush():
        joined = "\n".join(body).strip()
        location = " > ".join(h for _, h in headings) or "（冒頭）"
        chunks.extend(Chunk(rel, location, part) for part in _split_long(joined))
        body.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            flush()
            level = len(m.group(1))
            headings[:] = [(lv, h) for lv, h in headings if lv < level] + [(level, m.group(2).strip())]
        else:
            body.append(line)
    flush()
    return chunks


def _pdf_chunks(rel: str, path: Path) -> list[Chunk]:
    from pypdf import PdfReader

    chunks = []
    for number, page in enumerate(PdfReader(path).pages, start=1):
        chunks.extend(Chunk(rel, f"p.{number}", part) for part in _split_long(page.extract_text() or ""))
    return chunks


def _docx_chunks(rel: str, path: Path) -> list[Chunk]:
    import docx

    lines = []
    for para in docx.Document(path).paragraphs:
        style = (para.style.name or "") if para.style else ""
        m = re.match(r"(?:Heading|見出し)\s*(\d)", style)
        lines.append(f"{'#' * int(m.group(1))} {para.text}" if m else para.text)
    return _markdown_chunks(rel, "\n".join(lines))


def load_folder(root: Path) -> tuple[list[Chunk], dict[str, str], dict[str, float]]:
    """(チャンク一覧, 読み飛ばした資料と理由, 資料ごとの更新時刻) を返す。"""
    chunks: list[Chunk] = []
    skipped: dict[str, str] = {}
    mtimes: dict[str, float] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if path.name.startswith(".") or path.suffix.lower() not in SUPPORTED:
            continue
        mtimes[rel] = path.stat().st_mtime
        suffix = path.suffix.lower()
        try:
            if suffix == ".md":
                chunks.extend(_markdown_chunks(rel, path.read_text(encoding="utf-8")))
            elif suffix == ".txt":
                chunks.extend(_text_chunks(rel, path.read_text(encoding="utf-8")))
            elif suffix == ".pdf":
                chunks.extend(_pdf_chunks(rel, path))
            elif suffix == ".docx":
                chunks.extend(_docx_chunks(rel, path))
        except ImportError as e:
            skipped[rel] = f"{suffix} を読むライブラリがありません（{e.name}）。requirements.txt を参照"
        except Exception as e:  # 壊れたファイル1つで全体を止めない
            skipped[rel] = f"読み込みに失敗: {e}"
    return chunks, skipped, mtimes
