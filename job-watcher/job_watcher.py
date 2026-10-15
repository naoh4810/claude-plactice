"""
案件ウォッチャー：ランサーズ / クラウドワークスの新着案件を集めて
Google スプレッドシートに貯め、新しい案件だけ Slack に通知する。

PC 上の実ブラウザ（Chrome）で検索ページを開いて読むので、
PC が起動していれば タスクスケジューラ / cron から定期実行できる。

使い方:
    python job_watcher.py            # 収集 → スプシ追記 → Slack 通知
    python job_watcher.py --dry-run  # 収集結果を表示するだけ（送信しない）
"""

import argparse
import html
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

import requests
import yaml
from playwright.sync_api import Page, sync_playwright

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / ".browser-profile"


@dataclass
class Job:
    site: str
    keyword: str
    title: str
    price: str
    deadline: str
    url: str


# ---------------------------------------------------------------- 設定


def load_env(path: Path) -> None:
    """.env を読んで環境変数に入れる（既に設定済みのものは上書きしない）"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config() -> dict:
    path = BASE_DIR / "config.yml"
    if not path.exists():
        path = BASE_DIR / "config.example.yml"
        print(f"config.yml が無いので {path.name} を使います", file=sys.stderr)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- クラウドワークス


def yen(n) -> str:
    return f"{int(n):,}円" if n else ""


def format_cw_payment(payment: dict) -> str:
    if "hourly_payment" in payment:
        p = payment["hourly_payment"]
        lo, hi = yen(p.get("min_hourly_wage")), yen(p.get("max_hourly_wage"))
        return f"時給 {lo}〜{hi}" if lo or hi else "時給 相談"
    if "fixed_price_payment" in payment:
        p = payment["fixed_price_payment"]
        lo, hi = yen(p.get("min_budget")), yen(p.get("max_budget"))
        return f"固定 {lo}〜{hi}" if lo or hi else "固定 相談"
    # タスク・コンペなどは形式がまちまちなので数値だけ拾う
    for kind, p in payment.items():
        nums = [v for v in (p or {}).values() if isinstance(v, (int, float)) and v]
        if nums:
            return f"{kind.replace('_payment', '')} {yen(max(nums))}"
    return ""


def fetch_crowdworks(page: Page, keyword: str, page_no: int) -> list[Job]:
    url = (
        "https://crowdworks.jp/public/jobs/search?keep_search_criteria=false&order=new"
        f"&search%5Bkeywords%5D={quote(keyword)}&page={page_no}"
    )
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    # 検索結果は <div id="vue-container" data="{JSON}"> に埋め込まれている
    raw = page.get_attribute("#vue-container", "data", timeout=15_000)
    if not raw:
        print(f"[crowdworks] {keyword}: 検索結果データが見つかりません", file=sys.stderr)
        return []
    data = json.loads(html.unescape(raw))

    jobs = []
    for item in data.get("searchResult", {}).get("job_offers", []):
        offer = item.get("job_offer", {})
        if not offer.get("id"):
            continue
        jobs.append(
            Job(
                site="クラウドワークス",
                keyword=keyword,
                title=offer.get("title", "").strip(),
                price=format_cw_payment(item.get("payment", {})),
                deadline=offer.get("expired_on") or "",
                url=f"https://crowdworks.jp/public/jobs/{offer['id']}",
            )
        )
    return jobs


# ---------------------------------------------------------------- ランサーズ

# ページの HTML 構造に依存しすぎないよう、案件詳細へのリンクを起点に
# 「そのリンクを含むカード」のテキストから報酬・締切を拾う。
LANCERS_EXTRACT_JS = r"""
() => {
  const out = new Map();
  for (const a of document.querySelectorAll('a[href*="/work/detail/"]')) {
    const m = a.href.match(/\/work\/detail\/(\d+)/);
    if (!m || out.has(m[1])) continue;
    const title = (a.innerText || a.textContent || '').trim();
    if (title.length < 4) continue;  // 「詳細を見る」等の短いリンクは飛ばす
    let card = a;
    for (let i = 0; i < 8 && card.parentElement; i++) {
      card = card.parentElement;
      if (card.querySelectorAll('a[href*="/work/detail/"]').length > 3) { card = a.parentElement; break; }
      if (/円/.test(card.innerText || '')) break;
    }
    out.set(m[1], { id: m[1], title, text: (card.innerText || '').replace(/\s+/g, ' ') });
  }
  return [...out.values()];
}
"""


class LancersBlocked(Exception):
    """人間確認を通過できなかった（この回はランサーズをこれ以上見ない）"""


def fetch_lancers(page: Page, keyword: str, page_no: int) -> list[Job]:
    url = f"https://www.lancers.jp/work/search?keyword={quote(keyword)}&open=1&sort=started&page={page_no}"
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    # bot 対策の「人間確認」ページが出たら、通過するまで最大 3 分待つ
    # （headless: false なら画面に出るので手で通せる。Cookie は次回以降も残る）
    if "Human Verification" in page.title():
        print("[lancers] 人間確認ページが出ました。ブラウザで確認を通してください…", file=sys.stderr)
        try:
            page.wait_for_function("!document.title.includes('Human Verification')", timeout=180_000)
            page.wait_for_load_state("domcontentloaded")
        except Exception:
            raise LancersBlocked()

    try:
        page.wait_for_selector('a[href*="/work/detail/"]', timeout=15_000)
    except Exception:
        print(f"[lancers] {keyword}: 案件が見つかりません（0件 or ページ構造の変更）", file=sys.stderr)
        return []

    jobs = []
    for item in page.evaluate(LANCERS_EXTRACT_JS):
        text = item["text"]
        price = re.search(r"[\d,]+\s*円(?:\s*[〜~]\s*[\d,]+\s*円)?", text)
        deadline = re.search(r"(?:あと|残り)\s*\d+\s*(?:日|時間|分)", text)
        jobs.append(
            Job(
                site="ランサーズ",
                keyword=keyword,
                title=item["title"],
                price=price.group(0) if price else "",
                deadline=deadline.group(0) if deadline else "",
                url=f"https://www.lancers.jp/work/detail/{item['id']}",
            )
        )
    return jobs


# ---------------------------------------------------------------- 収集


def collect(config: dict) -> list[Job]:
    fetchers = []
    if config.get("sites", {}).get("crowdworks", True):
        fetchers.append(("crowdworks", fetch_crowdworks))
    if config.get("sites", {}).get("lancers", True):
        fetchers.append(("lancers", fetch_lancers))

    exclude = config.get("exclude_words") or []
    seen: set[str] = set()
    jobs: list[Job] = []

    with sync_playwright() as p:
        launch = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=bool(config.get("headless", False)),
            locale="ja-JP",
            viewport={"width": 1280, "height": 900},
        )
        if os.environ.get("CHROME_PATH"):
            launch["executable_path"] = os.environ["CHROME_PATH"]
        else:
            launch["channel"] = "chrome"  # PC にインストール済みの Chrome を使う
        try:
            ctx = p.chromium.launch_persistent_context(**launch)
        except Exception:
            # Chrome が無ければ Playwright 同梱の Chromium で動かす
            launch.pop("channel", None)
            ctx = p.chromium.launch_persistent_context(**launch)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        blocked: set[str] = set()
        for keyword in config.get("keywords", []):
            for name, fetch in fetchers:
                if name in blocked:
                    continue
                for page_no in range(1, int(config.get("max_pages", 1)) + 1):
                    try:
                        found = fetch(page, keyword, page_no)
                    except LancersBlocked:
                        print("[lancers] 人間確認を通過できなかったので今回はスキップします", file=sys.stderr)
                        blocked.add(name)
                        break
                    except Exception as e:
                        print(f"[{name}] {keyword} p{page_no}: 取得失敗 {e}", file=sys.stderr)
                        break
                    for job in found:
                        if job.url in seen or any(w in job.title for w in exclude):
                            continue
                        seen.add(job.url)
                        jobs.append(job)
                    print(f"[{name}] {keyword} p{page_no}: {len(found)}件", file=sys.stderr)
                    page.wait_for_timeout(2000)  # サイトに負荷をかけないよう間隔をあける
        ctx.close()
    return jobs


# ---------------------------------------------------------------- 送信


def save_to_sheet(jobs: list[Job]) -> tuple[list[Job], bool]:
    """スプシに追記し、(新規に追加された案件, シートが空だったか) を返す"""
    res = requests.post(
        os.environ["GAS_WEBAPP_URL"],
        json={"token": os.environ["GAS_TOKEN"], "jobs": [asdict(j) for j in jobs]},
        timeout=60,
    )
    res.raise_for_status()
    body = res.json()
    if not body.get("ok"):
        raise RuntimeError(f"スプレッドシートへの保存に失敗: {body}")
    added = set(body.get("added", []))
    return [j for j in jobs if j.url in added], bool(body.get("was_empty"))


def notify_slack(jobs: list[Job], max_items: int) -> None:
    def esc(s: str) -> str:  # Slack の mrkdwn で特別な意味を持つ文字をエスケープ
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    lines = [f"• <{j.url}|{esc(j.title)}>\n    {j.site}｜{j.price or '報酬未記載'}｜{j.keyword}" for j in jobs[:max_items]]
    if len(jobs) > max_items:
        lines.append(f"…他 {len(jobs) - max_items} 件（スプレッドシートを見てください）")
    text = f"*新着案件 {len(jobs)}件*\n" + "\n".join(lines)
    res = requests.post(os.environ["SLACK_WEBHOOK_URL"], json={"text": text}, timeout=30)
    res.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="収集結果を表示するだけで送信しない")
    args = parser.parse_args()

    load_env(BASE_DIR / ".env")
    config = load_config()
    jobs = collect(config)
    print(f"合計 {len(jobs)} 件を取得", file=sys.stderr)

    if args.dry_run:
        for j in jobs:
            print(f"{j.site}\t{j.price}\t{j.title}\t{j.url}")
        return
    if not jobs:
        return

    new_jobs, was_empty = save_to_sheet(jobs)
    print(f"スプレッドシートに新規 {len(new_jobs)} 件を追加", file=sys.stderr)
    if was_empty:
        # 初回は既存の案件が全部「新着」扱いになるので通知しない
        print("初回実行のため Slack 通知はスキップしました", file=sys.stderr)
        return
    if new_jobs:
        notify_slack(new_jobs, int(config.get("slack_max_items", 20)))


if __name__ == "__main__":
    main()
