"""Notion の「デイリー学習ログ」から記事の素材を取得する（任意機能）。

NOTION_API_KEY と NOTION_DAILY_LOG_DATABASE_ID が設定されているときだけ動く。
未設定・接続失敗時は空リストを返し、呼び出し側は config/topics.yml に
フォールバックする。落ちても記事生成全体を止めない、という方針。

デイリー学習ログの想定プロパティ（日本語名）:
  - タイトル        : title
  - 学習日          : date
  - 学習内容(概要)  : rich_text
  - 学習タイプ      : select（インプット / アウトプット / 復習）
  - 実践度          : select（理解のみ / 手を動かした / 応用できた）
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class LearningEntry:
    key: str          # 重複判定用の一意キー（日付+タイトル）
    date: str
    title: str
    summary: str
    learning_type: str
    practice_level: str

    def as_source_text(self) -> str:
        """記事生成プロンプトに渡す素材テキスト。"""
        lines = [f"学習日: {self.date or '不明'}", f"テーマ: {self.title}"]
        if self.learning_type:
            lines.append(f"学習タイプ: {self.learning_type}")
        if self.practice_level:
            lines.append(f"実践度: {self.practice_level}")
        if self.summary:
            lines.append(f"学習内容:\n{self.summary}")
        return "\n".join(lines)


def _plain_text(rich: list) -> str:
    return "".join(part.get("plain_text", "") for part in (rich or []))


def _prop(props: dict, name: str) -> dict:
    return props.get(name, {}) or {}


def fetch_recent_entries(limit: int = 25) -> list[LearningEntry]:
    """学習日の新しい順に学習ログを取得する。設定が無ければ空リスト。"""
    token = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("NOTION_DAILY_LOG_DATABASE_ID")
    if not token or not database_id:
        return []

    try:
        from notion_client import Client
    except ImportError:
        print("notion-client が未インストールのため Notion 連携をスキップします。")
        return []

    try:
        notion = Client(auth=token)
        resp = notion.databases.query(
            database_id=database_id,
            sorts=[{"property": "学習日", "direction": "descending"}],
            page_size=min(limit, 100),
        )
    except Exception as exc:  # 接続・権限・スキーマ差異など何が起きても止めない
        print(f"Notion からの取得に失敗しました（フォールバックします）: {exc}")
        return []

    entries: list[LearningEntry] = []
    for page in resp.get("results", []):
        props = page.get("properties", {})
        title = _plain_text(_prop(props, "タイトル").get("title"))
        summary = _plain_text(_prop(props, "学習内容(概要)").get("rich_text"))
        date_obj = _prop(props, "学習日").get("date") or {}
        date = date_obj.get("start", "") or ""
        learning_type = (_prop(props, "学習タイプ").get("select") or {}).get("name", "")
        practice = (_prop(props, "実践度").get("select") or {}).get("name", "")

        if not title and not summary:
            continue

        entries.append(
            LearningEntry(
                key=f"{date}::{title}".strip(":"),
                date=date,
                title=title or "（無題の学び）",
                summary=summary,
                learning_type=learning_type,
                practice_level=practice,
            )
        )
    return entries
