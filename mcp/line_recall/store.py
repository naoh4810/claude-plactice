"""予約台帳の読み込みと、配信の記録。

予約台帳は予約システムから書き出した CSV（Airリザーブ、LINE 予約ツール、手入力の表など）を読む。
列名は店ごとに違うので、よくある呼び方を自動で対応づける（例:「来院日」「予約日」→ 日付）。

  - RESERVATIONS_CSV を指定 : その CSV を読む（UTF-8 / BOM 付き UTF-8 / Shift_JIS）
  - LINE_STATE_PATH を指定  : 配信の記録をそのファイルに保存
  - どちらも無し            : 架空の整体院（sample_reservations.json）をメモリ上で使う
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SAMPLE_PATH = Path(__file__).with_name("sample_reservations.json")

COLUMN_ALIASES = {
    "date": ["日付", "来院日", "予約日", "利用日", "日時", "予約日時", "来店日", "date"],
    "customer_id": ["顧客ID", "会員番号", "お客様番号", "患者番号", "カルテ番号", "顧客番号", "ID", "customer_id"],
    "name": ["名前", "氏名", "お客様名", "顧客名", "患者名", "お名前", "name"],
    "menu": ["メニュー", "コース", "施術内容", "サービス", "menu"],
    "staff": ["担当", "担当者", "スタッフ", "施術者", "staff"],
    "status": ["状態", "ステータス", "予約状況", "来院状況", "status"],
}
STATUS_MAP = {
    "visited": ["来院", "来店", "完了", "済", "施術済", "利用済", "visited"],
    "booked": ["予約", "予約中", "予約済", "確定", "booked"],
    "cancelled": ["キャンセル", "取消", "cancelled"],
    "no_show": ["無断キャンセル", "無断", "ノーショー", "no_show"],
}
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S")


@dataclass
class Reservation:
    date: date
    customer_id: str
    name: str
    menu: str
    staff: str
    status: str            # visited / booked / cancelled / no_show


def _status(value: str) -> str:
    value = (value or "").strip()
    # 「無断キャンセル」を「キャンセル」より先に見る
    for key in ("no_show", "cancelled", "booked", "visited"):
        if any(word in value for word in STATUS_MAP[key]):
            return key
    return "visited" if not value else "unknown"


def _date(value: str) -> date:
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日付「{value}」を読めません")


def map_columns(header: list[str]) -> dict[str, str]:
    """CSV の列名 → 内部の項目名。日付と顧客ID（無ければ名前）は必須。"""
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for col in header:
            if col.strip() in aliases:
                mapping[field] = col
                break
    if "date" not in mapping or not ({"customer_id", "name"} & set(mapping)):
        raise ValueError(f"日付と、顧客ID か名前の列が必要です。CSV の列: {header}")
    return mapping


def parse_csv(text: str) -> tuple[list[Reservation], list[str]]:
    """(予約の一覧, 読めなかった行の説明) を返す。"""
    reader = csv.DictReader(io.StringIO(text))
    mapping = map_columns(reader.fieldnames or [])
    rows, problems = [], []
    for line_no, raw in enumerate(reader, start=2):
        get = lambda f: (raw.get(mapping[f]) or "").strip() if f in mapping else ""  # noqa: E731
        try:
            d = _date(get("date"))
        except ValueError as e:
            problems.append(f"{line_no}行目: {e}")
            continue
        cid = get("customer_id") or get("name")
        if not cid:
            problems.append(f"{line_no}行目: 顧客ID も名前も空です")
            continue
        status = _status(get("status"))
        if status == "unknown":
            problems.append(f"{line_no}行目: 状態「{get('status')}」が分からないので来院として扱いました")
            status = "visited"
        rows.append(Reservation(d, cid, get("name"), get("menu"), get("staff"), status))
    return rows, problems


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path} の文字コードを判定できません（UTF-8 か Shift_JIS で保存してください）")


class Ledger:
    def __init__(self, csv_path: Path | None, state_path: Path | None):
        self.state_path = state_path
        self.problems: list[str] = []
        if csv_path is None:
            data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
            self.shop = data["shop"]
            self.as_of = date.fromisoformat(data["as_of"])
            self.reservations = [Reservation(date.fromisoformat(r["date"]), r["customer_id"], r["name"], r["menu"],
                                             r["staff"], _status(r["status"])) for r in data["reservations"]]
            self.messages = data["messages"]
        else:
            self.shop = csv_path.stem
            self.as_of = None
            self.reservations, self.problems = parse_csv(read_text(csv_path))
            self.messages = []
        if state_path and state_path.exists():
            self.messages = json.loads(state_path.read_text(encoding="utf-8")).get("messages", [])

    @property
    def is_sample(self) -> bool:
        return self.as_of is not None

    def today(self) -> date:
        return self.as_of or date.today()

    def save_messages(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"messages": self.messages}, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)


def open_ledger() -> Ledger:
    csv_path = os.environ.get("RESERVATIONS_CSV")
    state = os.environ.get("LINE_STATE_PATH")
    return Ledger(Path(csv_path) if csv_path else None, Path(state) if state else None)
