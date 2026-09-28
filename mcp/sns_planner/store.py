"""ネタ帳・投稿（下書き / 予約 / 投稿済み）・反応の数字を1つの JSON に保存する。

  - SNS_DATA_PATH を指定 : そのファイルに保存（無ければ新規作成）。複数の端末で使うなら共有フォルダに置く
  - 指定なし             : 架空の観光案内アカウントのサンプル（sample_planner.json）をメモリ上で使う
"""

from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime
from pathlib import Path

SAMPLE_PATH = Path(__file__).with_name("sample_planner.json")
EMPTY = {"account": "", "weekly_targets": {}, "ideas": [], "posts": []}
METRICS = ("impressions", "likes", "comments", "shares", "saves", "clicks")


class Planner:
    def __init__(self, path: Path | None):
        self.path = path
        if path is None:
            self.data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        elif path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = copy.deepcopy(EMPTY)
        self.data.pop("_note", None)

    @property
    def is_sample(self) -> bool:
        return self.path is None

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)          # 書き込み途中で落ちてもファイルを壊さない

    def today(self) -> date:
        """サンプルは過去のデータなので、最後に投稿した日を「今日」とみなす。"""
        if not self.is_sample:
            return date.today()
        posted = [p["posted_at"] for p in self.data["posts"] if p.get("posted_at")]
        return datetime.fromisoformat(max(posted)).date() if posted else date.today()

    def _next_id(self, kind: str, prefix: str) -> str:
        numbers = [int(x["id"].split("-")[1]) for x in self.data[kind]]
        return f"{prefix}-{max(numbers, default=0) + 1}"

    def add_idea(self, title: str, notes: str, tags: list[str]) -> dict:
        idea = {"id": self._next_id("ideas", "idea"), "title": title, "notes": notes, "tags": tags,
                "created": self.today().isoformat()}
        self.data["ideas"].append(idea)
        self.save()
        return idea

    def idea(self, idea_id: str) -> dict:
        found = next((i for i in self.data["ideas"] if i["id"] == idea_id), None)
        if found is None:
            raise ValueError(f"ネタ {idea_id} はありません。list_ideas で確認してください")
        return found

    def post(self, post_id: str) -> dict:
        found = next((p for p in self.data["posts"] if p["id"] == post_id), None)
        if found is None:
            raise ValueError(f"投稿 {post_id} はありません。calendar で確認してください")
        return found

    def add_post(self, idea_id: str, platform: str, text: str, media: str) -> dict:
        self.idea(idea_id)
        post = {"id": self._next_id("posts", "post"), "idea_id": idea_id, "platform": platform, "text": text,
                "media": media, "status": "draft", "scheduled_at": None, "posted_at": None, "url": None,
                "metrics": {}}
        self.data["posts"].append(post)
        self.save()
        return post

    def update_post(self, post_id: str, **fields) -> dict:
        post = self.post(post_id)
        post.update(fields)
        self.save()
        return post


def open_planner() -> Planner:
    path = os.environ.get("SNS_DATA_PATH")
    return Planner(Path(path) if path else None)
