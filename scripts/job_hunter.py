"""案件チェックの自動実行：巡回 → 判定 → 台帳に追記 → Slack 通知。

処理の流れ:
  1. Chrome で各サイトの一覧ページを開き、案件リンクを集める（job_crawl.py）
  2. 台帳（data/jobs.csv）にあるキーは飛ばす（重複防止）
  3. 新しい案件だけ詳細ページを開き、本文を取る
  4. NGキーワードに当たれば即 ×、それ以外は Claude がプロフィールDBと照合して 〇/△/× を判定
  5. 台帳（CSV + 任意でスプレッドシート）に1案件1行で追記
  6. Slack に通知（〇△があればメンション付き）

使い方:
  python scripts/job_hunter.py                 # 全サイト
  python scripts/job_hunter.py --site lancers  # 1サイトだけ
  python scripts/job_hunter.py --dry-run       # 判定まで行い、台帳・Slack には書かない
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_common import JobRow, Ledger, load_profile, load_sites, save_body  # noqa: E402
from job_crawl import collect_links, fetch_body, open_browser  # noqa: E402
from job_judge import judge, ng_hit  # noqa: E402
from job_notify import notify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="案件の巡回・判定・台帳・通知")
    ap.add_argument("--site", help="このサイトだけ巡回する（config/job_sites.yml の name）")
    ap.add_argument("--max", type=int, help="1サイトあたりの詳細取得の上限（設定ファイルより優先）")
    ap.add_argument("--dry-run", action="store_true", help="台帳・Slack に書き込まない")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    cfg = load_sites()
    settings = cfg.get("settings", {})
    max_details = args.max or int(settings.get("max_details", 15))
    sites = [
        s for s in cfg.get("sites", [])
        if s.get("enabled") and (not args.site or s["name"] == args.site)
    ]
    if not sites:
        print("巡回対象のサイトがありません（enabled / --site を確認）")
        return 1

    profile = load_profile()
    ledger = Ledger()
    known = ledger.keys()
    client = anthropic.Anthropic()
    new_rows: list[JobRow] = []

    with sync_playwright() as p:
        ctx = open_browser(p, headless=bool(settings.get("headless", False)))
        page = ctx.new_page()
        for site in sites:
            label = site.get("label", site["name"])
            try:
                found = collect_links(page, site, settings)
            except Exception:
                print(f"[{label}] 一覧の取得に失敗しました")
                traceback.print_exc()
                continue
            fresh = [j for j in found if j.key not in known][:max_details]
            print(f"[{label}] 一覧 {len(found)} 件 / 新着 {len(fresh)} 件を確認します")

            for job in fresh:
                try:
                    body = fetch_body(page, job.url, settings)
                    row = JobRow(key=job.key, site=label, url=job.url)
                    if word := ng_hit(body, profile):
                        row.verdict, row.score = "×", "0"
                        row.title = page.title()
                        row.reasons = f"NGキーワード「{word}」を含むため自動で見送り"
                    else:
                        j = judge(client, body, profile)
                        row.title, row.client = j.title, j.client
                        row.budget, row.deadline = j.budget, j.deadline
                        row.summary, row.reasons = j.summary, j.reasons
                        row.verdict, row.score = j.verdict, str(j.score)
                        row.missing = " / ".join(j.missing_requirements)
                    if row.verdict == "×":
                        row.status = "見送り"
                except Exception:
                    print(f"  ! {job.url} の処理に失敗（次回また試します）")
                    traceback.print_exc()
                    continue

                print(f"  {row.verdict} {row.title[:40]}  {job.url}")
                known.add(job.key)
                if args.dry_run:
                    new_rows.append(row)
                    continue
                if ledger.append(row):
                    save_body(row.id, body)
                    new_rows.append(row)
        ctx.close()

    if args.dry_run:
        print(f"[dry-run] {len(new_rows)} 件を判定しました（台帳・Slack には書き込みません）")
        return 0
    notify(new_rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
