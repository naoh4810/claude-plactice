"""顧問先・書類の受け取り状況・完了・催促の記録を1つの JSON に保存する。

  - OFFICE_DATA_PATH を指定 : そのファイルに保存（無ければ新規作成）
  - 指定なし                : 架空の事務所のサンプル（sample_office.json）をメモリ上で使う
"""

from __future__ import annotations

import copy
import json
import os
from datetime import date
from pathlib import Path

SAMPLE_PATH = Path(__file__).with_name("sample_office.json")
EMPTY = {"office": "", "clients": [], "received": {}, "done": {}, "reminders": {}, "custom": []}


class Office:
    def __init__(self, path: Path | None):
        self.path = path
        if path is None:
            self.data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        elif path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = copy.deepcopy(EMPTY)
            # 使い始めた日より前の期限は「期限切れ」として出さない（過去分を全部警告しないため）
            self.data["tracking_since"] = date.today().isoformat()
        self.data.pop("_note", None)
        for key, value in EMPTY.items():
            self.data.setdefault(key, copy.deepcopy(value))

    @property
    def is_sample(self) -> bool:
        return self.path is None

    def today(self) -> date:
        """サンプルは固定の日付（as_of）を「今日」とみなす。"""
        if self.is_sample and self.data.get("as_of"):
            return date.fromisoformat(self.data["as_of"])
        return date.today()

    def tracking_since(self) -> date | None:
        value = self.data.get("tracking_since")
        return date.fromisoformat(value) if value else None

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def client(self, ref: str) -> dict:
        """id（c01）か、名前の一部で顧問先を探す。候補が複数なら絞り込みを求める。"""
        clients = self.data["clients"]
        exact = [c for c in clients if c["id"] == ref or c["name"] == ref]
        found = exact or [c for c in clients if ref in c["name"]]
        if not found:
            raise ValueError(f"顧問先「{ref}」は見つかりません。list_clients で確認してください")
        if len(found) > 1:
            raise ValueError(f"「{ref}」に当てはまる顧問先が複数あります: {[c['name'] for c in found]}")
        return found[0]


def open_office() -> Office:
    path = os.environ.get("OFFICE_DATA_PATH")
    return Office(Path(path) if path else None)
