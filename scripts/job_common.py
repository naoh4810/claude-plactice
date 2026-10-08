"""案件チェック自動化の共通部品（設定・台帳）。

台帳は CSV（data/jobs.csv）が正本。Google スプレッドシートは任意の写しで、
同じ行を追記・更新する。どちらも「key（サイト名:案件番号）」で重複を防ぐ。

環境変数（スプレッドシート連携は任意）:
  GOOGLE_SERVICE_ACCOUNT_FILE  サービスアカウントの JSON 鍵ファイルのパス
  JOB_SHEET_ID                 スプレッドシートのID（URL の /d/ と /edit の間）
  JOB_SHEET_TAB                シート（タブ）名。デフォルト「案件台帳」
"""

from __future__ import annotations

import csv
import datetime as dt
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LEDGER_CSV = DATA_DIR / "jobs.csv"
BODY_DIR = DATA_DIR / "bodies"        # 案件本文のキャッシュ（応募文生成で再利用）
PROPOSAL_DIR = DATA_DIR / "proposals"  # 生成した応募文
SITES_FILE = ROOT / "config" / "job_sites.yml"
PROFILE_FILE = ROOT / "config" / "profile.yml"

DEFAULT_MODEL = "claude-opus-5-5"

VERDICT_LABEL = {"〇": "推奨", "△": "挑戦", "×": "見送り"}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_sites() -> dict:
    return load_yaml(SITES_FILE)


def load_profile() -> dict:
    return load_yaml(PROFILE_FILE)


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
# 台帳の1行
# --------------------------------------------------------------------------- #
@dataclass
class JobRow:
    id: str = ""            # 000313 のような6桁の通し番号（応募時にこれを伝える）
    key: str = ""           # crowdworks:12345678（重複防止キー）
    site: str = ""
    status: str = "未対応"   # 未対応 / 応募文作成済 / 応募済 / 見送り
    verdict: str = ""       # 〇 / △ / ×
    score: str = ""
    title: str = ""
    client: str = ""
    budget: str = ""
    deadline: str = ""
    url: str = ""
    summary: str = ""
    reasons: str = ""
    missing: str = ""
    found_at: str = ""
    updated_at: str = ""


COLUMNS = [f.name for f in fields(JobRow)]
# スプレッドシートの見出し（日本語）。CSV は英語列名のまま（スクリプトで扱いやすいように）
SHEET_HEADERS = [
    "ID", "キー", "サイト", "状態", "判定", "スコア", "案件名", "発注者",
    "報酬", "締切", "URL", "要点", "判定理由", "不足要件", "発見日時", "更新日時",
]


# --------------------------------------------------------------------------- #
# 台帳
# --------------------------------------------------------------------------- #
class Ledger:
    def __init__(self) -> None:
        self.rows: list[JobRow] = []
        if LEDGER_CSV.exists():
            with LEDGER_CSV.open(encoding="utf-8-sig", newline="") as f:
                for rec in csv.DictReader(f):
                    self.rows.append(JobRow(**{c: rec.get(c, "") for c in COLUMNS}))
        self.sheet = _open_sheet()

    # ---- 参照 ----
    def keys(self) -> set[str]:
        return {r.key for r in self.rows}

    def get(self, job_id: str) -> JobRow | None:
        job_id = job_id.strip().zfill(6)
        return next((r for r in self.rows if r.id == job_id), None)

    def next_id(self) -> str:
        nums = [int(r.id) for r in self.rows if r.id.isdigit()]
        return str((max(nums) if nums else 0) + 1).zfill(6)

    # ---- 追記・更新 ----
    def append(self, row: JobRow) -> bool:
        """重複していなければ追記して True。重複なら何もせず False。"""
        if row.key in self.keys():
            return False
        row.id = row.id or self.next_id()
        row.found_at = row.found_at or now_str()
        row.updated_at = now_str()
        self.rows.append(row)
        self._save_csv()
        if self.sheet is not None:
            existing = set(self.sheet.col_values(2)[1:])  # B列 = キー
            if row.key not in existing:
                self.sheet.append_row(_to_sheet_values(row), value_input_option="RAW")
        return True

    def update_status(self, job_id: str, status: str) -> JobRow:
        row = self.get(job_id)
        if row is None:
            raise KeyError(f"ID {job_id} は台帳にありません")
        row.status = status
        row.updated_at = now_str()
        self._save_csv()
        if self.sheet is not None:
            cell = self.sheet.find(row.id, in_column=1)
            if cell is not None:
                self.sheet.update_cell(cell.row, COLUMNS.index("status") + 1, status)
                self.sheet.update_cell(cell.row, COLUMNS.index("updated_at") + 1, row.updated_at)
        return row

    def _save_csv(self) -> None:
        LEDGER_CSV.parent.mkdir(parents=True, exist_ok=True)
        tmp = LEDGER_CSV.with_suffix(".tmp")
        # utf-8-sig: Excel で開いても文字化けしないように BOM 付きで保存
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            for r in self.rows:
                writer.writerow(asdict(r))
        tmp.replace(LEDGER_CSV)


def _to_sheet_values(row: JobRow) -> list[str]:
    # ID の先頭ゼロが数値として消えないよう、文字列として書く（RAW 入力）
    return [str(getattr(row, c)) for c in COLUMNS]


def _open_sheet():
    """スプレッドシート連携が設定されていればワークシートを返す。未設定なら None。"""
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    sheet_id = os.environ.get("JOB_SHEET_ID")
    if not key_file or not sheet_id:
        return None
    import gspread  # 連携するときだけ必要

    gc = gspread.service_account(filename=key_file)
    book = gc.open_by_key(sheet_id)
    tab = os.environ.get("JOB_SHEET_TAB") or "案件台帳"
    try:
        ws = book.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=tab, rows=1000, cols=len(SHEET_HEADERS))
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADERS, value_input_option="RAW")
    return ws


# --------------------------------------------------------------------------- #
# 案件本文のキャッシュ
# --------------------------------------------------------------------------- #
def body_path(job_id: str) -> Path:
    return BODY_DIR / f"{job_id}.txt"


def save_body(job_id: str, text: str) -> None:
    BODY_DIR.mkdir(parents=True, exist_ok=True)
    body_path(job_id).write_text(text, encoding="utf-8")
