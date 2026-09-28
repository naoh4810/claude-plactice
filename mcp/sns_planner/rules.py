"""SNS ごとの文字数・ハッシュタグなどのルールと、下書きのチェック。

数値は 2026 年時点の目安。各サービスの仕様は変わるので、合わなくなったらここを直す。
迷う値は厳しい側に寄せている（チェックが厳しすぎても投稿は失敗しないが、緩いと投稿時に弾かれる）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

URL = re.compile(r"https?://\S+")
HASHTAG = re.compile(r"[#＃][^\s#＃]+")


@dataclass(frozen=True)
class Rule:
    label: str
    max_chars: int
    max_hashtags: int | None = None
    recommended_hashtags: int | None = None
    needs_media: bool = False
    links_clickable: bool = True
    weighted: bool = False            # X は日本語などを1文字=2で数える
    tip: str = ""


RULES: dict[str, Rule] = {
    "x": Rule("X（旧Twitter）", 280, recommended_hashtags=2, weighted=True,
              tip="日本語は1文字を2として数え、合計280まで（全角なら約140字）。URL は長さに関係なく23として数える"),
    "instagram": Rule("Instagram", 2200, max_hashtags=30, recommended_hashtags=5, needs_media=True, links_clickable=False,
                      tip="画像か動画が必須。本文のURLはタップできないので「プロフィールのリンクから」と書く"),
    "threads": Rule("Threads", 500, max_hashtags=1,
                    tip="ハッシュタグ（トピック）は1投稿に1つまで"),
    "tiktok": Rule("TikTok", 2200, recommended_hashtags=5, needs_media=True, links_clickable=False,
                   tip="動画が必須。説明文は冒頭の1〜2行しか表示されないので、最初に要点を書く"),
    "youtube_shorts": Rule("YouTube ショート（タイトル）", 100, recommended_hashtags=3, needs_media=True,
                           tip="タイトルは100字まで。#shorts を付けると分類されやすい"),
}


def _x_weight(ch: str) -> int:
    """X（twitter-text）の重み: ラテン文字や一部の記号は1、それ以外（日本語・絵文字など）は2。"""
    cp = ord(ch)
    if cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F or 0x2032 <= cp <= 0x2037:
        return 1
    return 2


def count_chars(platform: str, text: str) -> int:
    rule = RULES[platform]
    if not rule.weighted:
        return len(text)
    without_urls = URL.sub("", text)
    return sum(_x_weight(c) for c in without_urls) + 23 * len(URL.findall(text))


def check(platform: str, text: str, has_media: bool = False) -> dict:
    """errors はそのままだと投稿できない問題、warnings は投稿はできるが直したほうがよい点。"""
    if platform not in RULES:
        raise ValueError(f"未対応の SNS です: {platform}（対応: {', '.join(RULES)}）")
    rule = RULES[platform]
    errors, warnings = [], []
    length = count_chars(platform, text)
    hashtags = HASHTAG.findall(text)

    if not text.strip():
        errors.append("本文が空です")
    if length > rule.max_chars:
        errors.append(f"長すぎます（{length} / 上限 {rule.max_chars}）")
    if rule.max_hashtags is not None and len(hashtags) > rule.max_hashtags:
        errors.append(f"ハッシュタグが多すぎます（{len(hashtags)}個 / 上限 {rule.max_hashtags}個）")
    elif rule.recommended_hashtags is not None and len(hashtags) > rule.recommended_hashtags:
        warnings.append(f"ハッシュタグは {rule.recommended_hashtags}個程度までがおすすめです（今 {len(hashtags)}個）")
    if rule.needs_media and not has_media:
        warnings.append(f"{rule.label} は画像か動画が必要です。素材を用意してください")
    if not rule.links_clickable and URL.search(text):
        warnings.append(f"{rule.label} の本文のURLはタップできません。「プロフィールのリンクから」などに書き換えてください")

    return {"platform": platform, "length": length, "max": rule.max_chars, "hashtags": len(hashtags),
            "ok": not errors, "errors": errors, "warnings": warnings}
