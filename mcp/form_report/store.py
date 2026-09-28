"""Google フォームの回答（回答スプレッドシート）の読み込み。

フォームの回答は「回答をスプレッドシートにリンク」すると1行1回答で溜まる。
1行目が質問文、1列目が「タイムスタンプ」という Google フォームの標準形式を前提にする。

バックエンドは2種類:
  - GoogleSheetStore : 本番。FORM_SHEET_ID と サービスアカウントの JSON で接続
  - SampleStore      : 認証なしのデモ用。sample_responses.json（架空の福祉事業所の日次記録）
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SAMPLE_PATH = Path(__file__).with_name("sample_responses.json")
TIMESTAMP_COL = "タイムスタンプ"
DATE_FORMATS = ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class Response:
    row: int                  # スプレッドシート上の行番号（ヘッダーが1行目）
    answers: dict[str, str]

    @property
    def date(self) -> date | None:
        return parse_date(self.answers.get(TIMESTAMP_COL, ""))


class BaseStore:
    title: str = ""

    def table(self) -> list[list[str]]:
        """ヘッダー行を含む全セル。"""
        raise NotImplementedError

    def questions(self) -> list[str]:
        return [(h or "").strip() for h in self.table()[0]]

    def responses(self) -> list[Response]:
        header = self.questions()
        result = []
        for i, raw in enumerate(self.table()[1:], start=2):
            cells = list(raw) + [""] * (len(header) - len(raw))
            answers = {h: (c or "").strip() for h, c in zip(header, cells) if h}
            if any(answers.values()):
                result.append(Response(i, answers))
        return result


class SampleStore(BaseStore):
    def __init__(self, path: Path = SAMPLE_PATH):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.title = data["title"]
        self._table = data["table"]

    def table(self) -> list[list[str]]:
        return self._table


class GoogleSheetStore(BaseStore):
    """回答スプレッドシートを毎回読み直す（フォームには随時回答が増えるため）。"""

    def __init__(self, sheet_id: str, credentials_path: str, worksheet: str | None = None):
        import gspread  # 本番モードでだけ必要

        book = gspread.service_account(filename=credentials_path).open_by_key(sheet_id)
        self._ws = book.worksheet(worksheet) if worksheet else book.sheet1
        self.title = f"{book.title} / {self._ws.title}"

    def table(self) -> list[list[str]]:
        return self._ws.get_all_values()


def open_store() -> BaseStore:
    """FORM_SHEET_ID と GOOGLE_APPLICATION_CREDENTIALS があれば本番、無ければサンプル。"""
    sheet_id = os.environ.get("FORM_SHEET_ID")
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if sheet_id and credentials:
        return GoogleSheetStore(sheet_id, credentials, os.environ.get("FORM_WORKSHEET"))
    return SampleStore()
