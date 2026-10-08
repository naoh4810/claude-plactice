"""ID を指定して応募文を生成する。送信はしない（最後は自分で確認して送信ボタンを押す）。

使い方:
  python scripts/job_apply.py 000313          # 応募文を生成して表示・保存（data/proposals/000313.md）
  python scripts/job_apply.py 000313 --open   # 生成後、案件ページを専用 Chrome で開く（貼り付けて自分で送信）
  python scripts/job_apply.py 000313 --sent   # 送信したら台帳を「応募済」にする
  python scripts/job_apply.py 000313 --skip   # やめたら台帳を「見送り」にする
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_common import PROPOSAL_DIR, Ledger, body_path, load_profile  # noqa: E402


def copy_to_clipboard(text: str) -> bool:
    for cmd in (["pbcopy"], ["clip"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text.encode("utf-16" if cmd[0] == "clip" else "utf-8"), check=False)
            return True
    return False


def extract_body_text(proposal_md: str) -> str:
    """「## 応募文」節だけ取り出す（クリップボード用）。"""
    lines, inside = [], False
    for line in proposal_md.splitlines():
        if line.startswith("## "):
            inside = line.strip() == "## 応募文"
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines).strip() or proposal_md


def open_job_page(url: str) -> None:
    from playwright.sync_api import sync_playwright

    from job_crawl import open_browser

    with sync_playwright() as p:
        ctx = open_browser(p, headless=False)
        ctx.new_page().goto(url)
        input("ブラウザで内容を確認し、自分で送信してください。閉じるときは Enter > ")
        ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="ID から応募文を生成する")
    ap.add_argument("job_id", help="台帳の ID（例: 000313）")
    ap.add_argument("--open", action="store_true", help="生成後に案件ページを開く")
    ap.add_argument("--sent", action="store_true", help="応募済にする")
    ap.add_argument("--skip", action="store_true", help="見送りにする")
    args = ap.parse_args()

    ledger = Ledger()
    row = ledger.get(args.job_id)
    if row is None:
        print(f"ID {args.job_id} は台帳（data/jobs.csv）にありません")
        return 1

    if args.sent or args.skip:
        status = "応募済" if args.sent else "見送り"
        ledger.update_status(row.id, status)
        print(f"[{row.id}] {row.title} を「{status}」にしました")
        return 0

    bp = body_path(row.id)
    if not bp.exists():
        print(f"案件本文のキャッシュ {bp} がありません。job_hunter.py で取得した案件か確認してください。")
        return 1

    from job_judge import write_proposal

    note = f"判定: {row.verdict}（スコア {row.score}）\n理由: {row.reasons}\n不足要件: {row.missing or 'なし'}"
    print(f"[{row.id}] {row.title} の応募文を生成中...")
    proposal = write_proposal(bp.read_text(encoding="utf-8"), load_profile(), note)

    PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
    out = PROPOSAL_DIR / f"{row.id}.md"
    out.write_text(f"# [{row.id}] {row.title}\n\n{row.url}\n\n{proposal}\n", encoding="utf-8")
    ledger.update_status(row.id, "応募文作成済")

    print("\n" + proposal + "\n")
    print(f"保存しました: {out}")
    if copy_to_clipboard(extract_body_text(proposal)):
        print("応募文をクリップボードにコピーしました。")
    print(f"案件ページ: {row.url}")
    print(f"送信したら: python scripts/job_apply.py {row.id} --sent")

    if args.open:
        open_job_page(row.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
