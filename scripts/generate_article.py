"""毎日1記事を Claude で生成して articles/ に保存する。

処理の流れ:
  1. トピックを選ぶ（Notion の学習ログ優先 → なければ config/topics.yml）
  2. すでに記事化したトピックは state/used.json で除外
  3. Claude で記事を生成
  4. articles/YYYY-MM-DD-slug.md に frontmatter 付きで保存
  5. used.json を更新

環境変数:
  ANTHROPIC_API_KEY            必須
  NOTION_API_KEY               任意（学習ログ連携）
  NOTION_DAILY_LOG_DATABASE_ID 任意
  ARTICLE_MODEL                任意（デフォルト claude-opus-5）
  ENABLE_WEB_SEARCH            任意（true で最新情報をWeb検索で補強）
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import yaml

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from notion_source import LearningEntry, fetch_recent_entries  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "articles"
STATE_FILE = ROOT / "state" / "used.json"
TOPICS_FILE = ROOT / "config" / "topics.yml"
SYSTEM_PROMPT_FILE = ROOT / "prompts" / "system_prompt.md"

DEFAULT_MODEL = "claude-opus-5"


# --------------------------------------------------------------------------- #
# 状態（使用済みトピック）
# --------------------------------------------------------------------------- #
def load_used_keys() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return set()
    return set()


def save_used_keys(keys: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(sorted(keys), ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# トピック選択
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    return yaml.safe_load(TOPICS_FILE.read_text(encoding="utf-8")) or {}


def pick_topic(config: dict, used: set[str]) -> tuple[str, str, str] | None:
    """(key, title, source_text) を返す。使えるトピックが無ければ None。"""
    # 1) Notion 学習ログ（新しい順で未使用の最初の1件）
    for entry in fetch_recent_entries():
        if entry.key not in used:
            return entry.key, entry.title, entry.as_source_text()

    # 2) フォールバック: topics.yml のキュー
    for item in config.get("queue", []):
        title = item.get("title", "").strip()
        if not title:
            continue
        key = f"queue::{title}"
        if key not in used:
            hint = item.get("hint", "")
            source = f"テーマ: {title}\n補足: {hint}" if hint else f"テーマ: {title}"
            return key, title, source
    return None


# --------------------------------------------------------------------------- #
# Claude で記事生成
# --------------------------------------------------------------------------- #
def build_user_prompt(config: dict, source_text: str) -> str:
    s = config.get("settings", {})
    must = "\n".join(f"- {m}" for m in s.get("must_include", []))
    return f"""以下は書き手の実際の学習ログ（体験）です。これを一次情報の素材として、
日本語のブログ記事を1本書いてください。

## 想定読者
{s.get("audience", "AIを業務に取り入れたい人")}

## 文体・トーン
{s.get("tone", "実務家として、実体験ベースで具体的に。")}

## 目安の文字数
本文でおよそ {s.get("target_chars", 2500)} 文字

## 必ず盛り込む要素
{must}

## 素材（学習ログ）
{source_text}
"""


def _create_message(client: anthropic.Anthropic, **kwargs):
    """server tool の pause_turn を吸収しつつ最終メッセージを返す。"""
    messages = list(kwargs.pop("messages"))
    while True:
        with client.messages.stream(messages=messages, **kwargs) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        return resp


def generate_article(source_text: str, config: dict) -> str:
    client = anthropic.Anthropic()
    # 空文字（GitHub Actions で未設定の Variable は "" になる）もデフォルトへ落とす
    model = os.environ.get("ARTICLE_MODEL") or DEFAULT_MODEL

    kwargs = dict(
        model=model,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=SYSTEM_PROMPT_FILE.read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": build_user_prompt(config, source_text)}],
    )

    if os.environ.get("ENABLE_WEB_SEARCH", "false").lower() == "true":
        # 最新情報の補強が必要なときだけ。素材（実体験）が主役なので既定はオフ。
        kwargs["tools"] = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
        ]

    resp = _create_message(client, **kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"生成が拒否されました: {resp.stop_details}")

    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("記事本文が空でした。")
    return text


# --------------------------------------------------------------------------- #
# 保存
# --------------------------------------------------------------------------- #
def slugify(title: str) -> str:
    slug = re.sub(r"[^\wぁ-んァ-ン一-龥ー]+", "-", title.strip()).strip("-")
    return (slug[:40] or "article").lower()


def extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def save_article(markdown: str, topic_title: str, source_key: str) -> Path:
    today = dt.date.today().isoformat()
    title = extract_title(markdown, topic_title)
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTICLES_DIR / f"{today}-{slugify(title)}.md"

    frontmatter = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(39))}"\n'
        f"date: {today}\n"
        "status: draft\n"
        f'source: "{source_key}"\n'
        "---\n\n"
    )
    path.write_text(frontmatter + markdown + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY が未設定です。", file=sys.stderr)
        return 1

    config = load_config()
    used = load_used_keys()

    picked = pick_topic(config, used)
    if picked is None:
        print("記事化できる未使用トピックがありません。"
              "config/topics.yml に追加するか、Notionに学びを追記してください。")
        return 0

    key, title, source_text = picked
    print(f"今日のトピック: {title}")

    markdown = generate_article(source_text, config)
    path = save_article(markdown, title, key)
    print(f"記事を保存しました: {path.relative_to(ROOT)}")

    used.add(key)
    save_used_keys(used)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
