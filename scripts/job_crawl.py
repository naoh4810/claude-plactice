"""Chrome で案件サイトを巡回して、案件リンクと本文を集める。

Playwright で「この仕組み専用の Chrome プロフィール」（.chrome-profile/）を使うので、
一度ログインしておけば次回以降もログイン状態のまま巡回できる。普段使いの Chrome とは別物。

使い方:
  python scripts/job_crawl.py login              # 各サイトのログイン画面を開く（初回だけ）
  python scripts/job_crawl.py links crowdworks   # 一覧ページ上のリンクを全部表示（link_pattern 調整用）
"""

from __future__ import annotations

import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_common import ROOT, load_sites  # noqa: E402

PROFILE_DIR = ROOT / ".chrome-profile"


@dataclass
class FoundJob:
    site: str
    site_label: str
    native_id: str   # サイト内の案件番号
    url: str

    @property
    def key(self) -> str:
        return f"{self.site}:{self.native_id}"


def open_browser(playwright, headless: bool):
    """専用プロフィールで Chrome を起動する。Chrome が無ければ同梱 Chromium を使う。

    JOB_CHROME_PATH を設定すると、そのパスのブラウザを使う。
    """
    PROFILE_DIR.mkdir(exist_ok=True)
    opts = dict(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        locale="ja-JP",
        viewport={"width": 1280, "height": 900},
    )
    if path := os.environ.get("JOB_CHROME_PATH"):
        return playwright.chromium.launch_persistent_context(executable_path=path, **opts)
    try:
        return playwright.chromium.launch_persistent_context(channel="chrome", **opts)
    except Exception:
        return playwright.chromium.launch_persistent_context(**opts)


def _pause(settings: dict) -> None:
    lo, hi = settings.get("wait_sec", [3, 6])
    time.sleep(random.uniform(lo, hi))


def _all_hrefs(page) -> list[str]:
    return page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")


def collect_links(page, site: dict, settings: dict) -> list[FoundJob]:
    """一覧ページを開き、link_pattern に合うリンクを案件として返す（重複除去・出現順）。"""
    pattern = re.compile(site["link_pattern"])
    found: dict[str, FoundJob] = {}
    for list_url in site.get("list_urls", []):
        page.goto(list_url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2_000)  # JS で描画される一覧を待つ
        for href in _all_hrefs(page):
            m = pattern.match(href)
            if not m:
                continue
            native_id = m.group(1)
            if native_id not in found:
                found[native_id] = FoundJob(
                    site=site["name"],
                    site_label=site.get("label", site["name"]),
                    native_id=native_id,
                    url=m.group(0),
                )
        _pause(settings)
    return list(found.values())


def fetch_body(page, url: str, settings: dict) -> str:
    """詳細ページを開いて本文テキストを返す。"""
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1_500)
    text = ""
    for selector in ("main", "article", "#main", "body"):
        if page.locator(selector).count():
            text = page.locator(selector).first.inner_text(timeout=10_000)
            if len(text.strip()) > 200:
                break
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = page.title()
    _pause(settings)
    max_chars = int(settings.get("max_body_chars", 12000))
    return f"ページタイトル: {title}\nURL: {url}\n\n{text[:max_chars]}"


# --------------------------------------------------------------------------- #
# 補助コマンド
# --------------------------------------------------------------------------- #
def cmd_login() -> None:
    from playwright.sync_api import sync_playwright

    cfg = load_sites()
    with sync_playwright() as p:
        ctx = open_browser(p, headless=False)
        for site in cfg.get("sites", []):
            if site.get("login_url"):
                ctx.new_page().goto(site["login_url"])
        input("開いたタブで各サイトにログインしたら、ここで Enter を押してください > ")
        ctx.close()
    print(f"ログイン状態を保存しました: {PROFILE_DIR}")


def cmd_links(site_name: str) -> None:
    from playwright.sync_api import sync_playwright

    cfg = load_sites()
    site = next((s for s in cfg.get("sites", []) if s["name"] == site_name), None)
    if site is None:
        sys.exit(f"サイト {site_name} は config/job_sites.yml にありません")
    with sync_playwright() as p:
        ctx = open_browser(p, headless=False)
        page = ctx.new_page()
        for url in site.get("list_urls", []):
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(3_000)
            print(f"== {url}")
            for href in sorted(set(_all_hrefs(page))):
                hit = "✔" if re.match(site["link_pattern"], href) else " "
                print(f" {hit} {href}")
        ctx.close()
    print("\n✔ が link_pattern に一致したリンクです。案件詳細のURLに ✔ が付くよう調整してください。")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "login":
        cmd_login()
    elif len(sys.argv) >= 3 and sys.argv[1] == "links":
        cmd_links(sys.argv[2])
    else:
        print(__doc__)
