"""営業シート（Google スプレッドシート）の読み書き。

シートはタブごとに列構成が違う（営業 / 交流会 / マッチングアプリ）ため、
1行目のヘッダー名をキーにした dict として扱う。ヘッダーが空の列は「メモ」とみなす。

バックエンドは2種類:
  - GoogleSheetStore : 本番。SALES_SHEET_ID と サービスアカウントの JSON で接続
  - SampleStore      : 認証なしのデモ用。sample_data.json を読み込み、書き込みはメモリ上のみ
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

NAME_COL = "お客さま"
MEMO_COL = "メモ"
SAMPLE_PATH = Path(__file__).with_name("sample_data.json")


def normalize_header(header: list[str]) -> list[str]:
    return [(h or "").strip() or MEMO_COL for h in header]


@dataclass
class Contact:
    sheet: str
    row: int                      # スプレッドシート上の行番号（1始まり、ヘッダーが1行目）
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.fields.get(NAME_COL, "")

    def as_dict(self) -> dict:
        return {"sheet": self.sheet, "row": self.row, **{k: v for k, v in self.fields.items() if v}}


class BaseStore:
    def refresh(self) -> None:
        """手動でシートが編集されている前提で、ツール呼び出しごとに読み直す。"""

    def sheet_names(self) -> list[str]:
        raise NotImplementedError

    def header(self, sheet: str) -> list[str]:
        raise NotImplementedError

    def _raw_rows(self, sheet: str) -> list[list[str]]:
        """ヘッダーを除いた全行（2行目以降）。"""
        raise NotImplementedError

    def _write_cells(self, sheet: str, row: int, values: dict[str, str]) -> None:
        raise NotImplementedError

    def contacts(self, sheet: str | None = None) -> list[Contact]:
        """お客さま名が入っている行だけを返す（番号だけ振られた空行は除く）。"""
        result = []
        for name in [sheet] if sheet else self.sheet_names():
            header = self.header(name)
            for i, raw in enumerate(self._raw_rows(name), start=2):
                cells = list(raw) + [""] * (len(header) - len(raw))
                fields = {h: (c or "").strip() for h, c in zip(header, cells)}
                if fields.get(NAME_COL):
                    result.append(Contact(name, i, fields))
        return result

    def append_contact(self, sheet: str, values: dict[str, str]) -> Contact:
        """お客さま欄が空いている最初の行に書き込む。無ければ末尾に追加。"""
        header = self.header(sheet)
        unknown = [k for k in values if k not in header]
        if unknown:
            raise ValueError(f"シート「{sheet}」に無い列です: {unknown}（列: {header}）")
        name_idx = header.index(NAME_COL)
        rows = self._raw_rows(sheet)
        target = None
        for i, raw in enumerate(rows, start=2):
            if len(raw) <= name_idx or not (raw[name_idx] or "").strip():
                target = i
                break
        if target is None:
            target = len(rows) + 2
        self._write_cells(sheet, target, values)
        return next(c for c in self.contacts(sheet) if c.row == target)

    def update_contact(self, sheet: str, row: int, values: dict[str, str]) -> Contact:
        header = self.header(sheet)
        unknown = [k for k in values if k not in header]
        if unknown:
            raise ValueError(f"シート「{sheet}」に無い列です: {unknown}（列: {header}）")
        self._write_cells(sheet, row, values)
        return next(c for c in self.contacts(sheet) if c.row == row)


class SampleStore(BaseStore):
    """sample_data.json を使うデモ用ストア。書き込みはプロセス終了で消える。"""

    def __init__(self, path: Path = SAMPLE_PATH):
        data = json.loads(path.read_text(encoding="utf-8"))
        self._sheets: dict[str, list[list[str]]] = copy.deepcopy(data["sheets"])

    def sheet_names(self) -> list[str]:
        return list(self._sheets)

    def header(self, sheet: str) -> list[str]:
        return normalize_header(self._table(sheet)[0])

    def _raw_rows(self, sheet: str) -> list[list[str]]:
        return self._table(sheet)[1:]

    def _table(self, sheet: str) -> list[list[str]]:
        if sheet not in self._sheets:
            raise ValueError(f"シート「{sheet}」はありません（{self.sheet_names()}）")
        return self._sheets[sheet]

    def _write_cells(self, sheet: str, row: int, values: dict[str, str]) -> None:
        table = self._table(sheet)
        header = self.header(sheet)
        while len(table) < row:
            table.append([""] * len(header))
        line = table[row - 1]
        line.extend([""] * (len(header) - len(line)))
        for key, value in values.items():
            line[header.index(key)] = value


class GoogleSheetStore(BaseStore):
    """gspread + サービスアカウントで本物のスプレッドシートを読み書きする。"""

    def __init__(self, sheet_id: str, credentials_path: str):
        import gspread  # 本番モードでだけ必要

        client = gspread.service_account(filename=credentials_path)
        self._book = client.open_by_key(sheet_id)
        self._cache: dict[str, list[list[str]]] = {}

    def refresh(self) -> None:
        self._cache.clear()

    def sheet_names(self) -> list[str]:
        return [ws.title for ws in self._book.worksheets()]

    def header(self, sheet: str) -> list[str]:
        return normalize_header(self._values(sheet)[0])

    def _raw_rows(self, sheet: str) -> list[list[str]]:
        return self._values(sheet)[1:]

    def _values(self, sheet: str) -> list[list[str]]:
        if sheet not in self._cache:
            self._cache[sheet] = self._book.worksheet(sheet).get_all_values()
        return self._cache[sheet]

    def _write_cells(self, sheet: str, row: int, values: dict[str, str]) -> None:
        import gspread

        header = self.header(sheet)
        ws = self._book.worksheet(sheet)
        if row > ws.row_count:
            ws.add_rows(row - ws.row_count)
        cells = [gspread.Cell(row, header.index(k) + 1, v) for k, v in values.items()]
        ws.update_cells(cells)
        self._cache.pop(sheet, None)


def open_store() -> BaseStore:
    """SALES_SHEET_ID と GOOGLE_APPLICATION_CREDENTIALS があれば本番、無ければサンプル。"""
    sheet_id = os.environ.get("SALES_SHEET_ID")
    credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if sheet_id and credentials:
        return GoogleSheetStore(sheet_id, credentials)
    return SampleStore()
