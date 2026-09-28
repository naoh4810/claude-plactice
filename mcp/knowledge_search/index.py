"""日本語向けのキーワード検索（文字バイグラム + BM25）。

日本語は単語の区切りが無いので、「就業規則」→「就業」「業規」「規則」のように
2文字ずつに区切って照合する。英数字は単語のまま扱う。形態素解析や埋め込みの
外部サービスを使わないので、資料を社外に出さずに動く。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from loader import Chunk

K1 = 1.5
B = 0.75
WORD = re.compile(r"\w+")
# 「いい」「して」「ます」のような ひらがなだけの2文字は、どの文にも出てきて順位を乱すので使わない
HIRAGANA_ONLY = re.compile(r"^[ぁ-ゖー]+$")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def tokenize(text: str) -> list[str]:
    tokens = []
    for run in WORD.findall(normalize(text)):
        if run.isascii():
            tokens.append(run)
        elif len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(g for g in (run[i:i + 2] for i in range(len(run) - 1)) if not HIRAGANA_ONLY.match(g))
    return tokens


class Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        # 見出しとファイル名も検索対象に含める（本文に言葉が無くても見出しで当たるように）
        self._tf = [Counter(tokenize(f"{c.path}\n{c.location}\n{c.text}")) for c in chunks]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df = Counter(t for tf in self._tf for t in tf)
        n = len(chunks)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, top_k: int = 5, path_prefix: str | None = None) -> list[tuple[Chunk, float]]:
        terms = set(tokenize(query))
        scored = []
        for chunk, tf, length in zip(self.chunks, self._tf, self._len):
            if path_prefix and not chunk.path.startswith(path_prefix):
                continue
            score = 0.0
            for t in terms:
                f = tf.get(t)
                if f:
                    score += self._idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * length / self._avg))
            if score > 0:
                scored.append((chunk, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def snippet(text: str, query: str, width: int = 120) -> str:
    """質問の言葉が一番多く集まっている付近を切り出す。"""
    flat = re.sub(r"\s+", " ", text)
    if len(flat) <= width:
        return flat
    terms = set(tokenize(query))
    norm = normalize(flat)
    best, best_hits = 0, -1
    for start in range(0, len(flat) - width + 1, 10):
        window = norm[start:start + width]
        hits = sum(1 for t in terms if t in window)
        if hits > best_hits:
            best, best_hits = start, hits
    prefix = "…" if best > 0 else ""
    suffix = "…" if best + width < len(flat) else ""
    return f"{prefix}{flat[best:best + width]}{suffix}"
