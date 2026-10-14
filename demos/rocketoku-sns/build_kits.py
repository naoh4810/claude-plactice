"""案2：data/kits.json から加盟店ごとの投稿キット（画像＋文面）を web/kit/ に書き出す。

    python build_kits.py

加盟店ごとに次のファイルができ、web/kit/index.html で一覧できます。
  post.png（Instagram投稿 1080×1080） / story.png（ストーリーズ 1080×1920）
  caption.txt（投稿文＋ハッシュタグ） / google_post.txt（Googleマップの店舗情報用）
"""

import html
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "web" / "kit"

# 加盟店の業種ごとの配色（画像の背景）
PALETTE = {"体験": ("#1f4e5f", "#f4efe6"), "食事券": ("#6b2d4f", "#fbf1f5")}

TEMPLATE = """<!doctype html><html lang="ja"><head><meta charset="utf-8"><style>
* {{ margin: 0; box-sizing: border-box; }}
body {{ width: {w}px; height: {h}px; font-family: "IPAGothic", "Hiragino Sans", sans-serif; background: {bg}; color: {fg};
        display: flex; flex-direction: column; justify-content: space-between; padding: {pad}px; }}
.badge {{ align-self: flex-start; border: 4px solid {fg}; border-radius: 999px; padding: 14px 34px; font-size: {badge}px; font-weight: bold; letter-spacing: 2px; }}
h1 {{ font-size: {head}px; line-height: 1.35; font-weight: bold; }}
.sub {{ margin-top: 28px; font-size: {sub}px; opacity: .9; }}
.steps {{ display: flex; gap: 24px; font-size: {step}px; }}
.steps div {{ flex: 1; border-top: 4px solid {fg}; padding-top: 18px; line-height: 1.5; }}
.foot {{ font-size: {foot}px; opacity: .8; }}
</style></head><body>
<div class="badge">ロケふる 対応店</div>
<div><h1>{headline}</h1><p class="sub">{subline}</p></div>
<div class="steps"><div>① アプリで<br>QRを読む</div><div>② 支払いが<br>ふるさと納税に</div><div>③ その場で<br>楽しめる</div></div>
<p class="foot">倉敷市外にお住まいの方が対象です</p>
</body></html>"""


def render(page, kit, merchant, size, path):
    w, h = size
    tall = h > w
    bg, fg = PALETTE.get(merchant["category"], PALETTE["体験"])
    doc = TEMPLATE.format(
        w=w, h=h, bg=bg, fg=fg, pad=96 if tall else 80,
        badge=40, head=96 if tall else 84, sub=46 if tall else 42, step=38 if tall else 34, foot=30,
        headline=html.escape(kit["headline"]).replace("、", "、<br>"), subline=html.escape(kit["subline"]),
    )
    page.set_viewport_size({"width": w, "height": h})
    page.set_content(doc)
    page.screenshot(path=str(path))


def main():
    kits = json.loads((ROOT / "data" / "kits.json").read_text(encoding="utf-8"))["kits"]
    merchants = {m["id"]: m for m in json.loads((ROOT / "data" / "merchants.json").read_text(encoding="utf-8"))["merchants"]}
    cards = []
    with sync_playwright() as pw:
        launch = {"executable_path": "/opt/pw-browsers/chromium"} if os.path.exists("/opt/pw-browsers/chromium") else {}
        browser = pw.chromium.launch(**launch)
        page = browser.new_page()
        for kit in kits:
            m = merchants[kit["merchant"]]
            d = OUT / m["id"]
            d.mkdir(parents=True, exist_ok=True)
            render(page, kit, m, (1080, 1080), d / "post.png")
            render(page, kit, m, (1080, 1920), d / "story.png")
            caption = kit["caption"] + "\n\n" + " ".join(kit["hashtags"])
            (d / "caption.txt").write_text(caption, encoding="utf-8")
            (d / "google_post.txt").write_text(kit["google_post"], encoding="utf-8")
            checks = "".join(f"<li>{html.escape(c)}</li>" for c in kit["checks"]) or "<li>なし</li>"
            cards.append(f"""
<section class="card">
  <h2>{html.escape(m['name'])}<small>{html.escape(m['area'])}・{html.escape(m['category'])}</small></h2>
  <div class="imgs"><img src="{m['id']}/post.png" alt="投稿画像"><img src="{m['id']}/story.png" alt="ストーリーズ画像" class="story"></div>
  <h3>Instagram 投稿文</h3><pre>{html.escape(caption)}</pre>
  <h3>ストーリーズの一言</h3><pre>{html.escape(kit['story_text'])}</pre>
  <h3>Googleマップ（店舗情報）への投稿</h3><pre>{html.escape(kit['google_post'])}</pre>
  <h3>加盟店に渡す前の確認事項</h3><ul>{checks}</ul>
</section>""")
        browser.close()

    (OUT / "index.html").write_text(f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><title>加盟店 投稿キット</title>
<style>
body {{ margin: 0; font-family: "IPAGothic", "Hiragino Sans", sans-serif; background: #f5f5f2; color: #222; }}
header {{ background: #1f4e5f; color: #fff; padding: 18px 28px; }} header p {{ margin: 6px 0 0; font-size: 13px; opacity: .85; }}
main {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; padding: 20px 28px; }}
.card {{ background: #fff; border-radius: 10px; padding: 18px 20px; font-size: 13px; }}
.card h2 {{ margin: 0 0 12px; font-size: 18px; }} .card h2 small {{ margin-left: 10px; font-size: 12px; color: #777; font-weight: normal; }}
.card h3 {{ margin: 14px 0 6px; font-size: 13px; color: #1f4e5f; }}
.imgs {{ display: flex; gap: 10px; align-items: flex-start; }} .imgs img {{ width: 62%; border-radius: 6px; }} .imgs img.story {{ width: 26%; }}
pre {{ white-space: pre-wrap; background: #f7f7f4; padding: 10px; border-radius: 6px; margin: 0; font-family: inherit; line-height: 1.6; }}
ul {{ margin: 0; padding-left: 20px; }}
</style></head><body>
<header><h1 style="margin:0;font-size:20px">加盟店向け「ロケふる使えます」投稿キット（デモ）</h1>
<p>架空の加盟店データから自動生成。加盟店はこの画像と文面をそのまま自分のSNS・Googleマップに投稿できます。</p></header>
<main>{''.join(cards)}</main></body></html>""", encoding="utf-8")
    print(f"{len(cards)} 店舗分のキットを {OUT} に書き出しました")


if __name__ == "__main__":
    main()
