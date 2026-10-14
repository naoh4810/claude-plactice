"""案3：加盟店のイベントを、書式の違う観光サイトA・Bに Computer use で掲載申請するエージェント。

画面操作の部分（Desktop、ツールの処理、API呼び出し）は返礼品登録デモ（../furusato-cu/agent.py）と共通です。

    export ANTHROPIC_API_KEY=...
    python agent.py                 # 確認依頼にはターミナルで回答
    python agent.py --auto-answer   # 確認依頼にすべて「はい」と答える（無人録画用）
"""

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "furusato-cu"))
import agent as base  # noqa: E402  返礼品デモの agent.py（Desktop・create・handle_tool_calls を流用）

import anthropic  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# このフォルダの server.py を、返礼品デモの server.py と名前がぶつからないように読み込む
_spec = importlib.util.spec_from_file_location("rocketoku_server", ROOT / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

SYSTEM_PROMPT = """\
あなたは現地決済型ふるさと納税アプリ「ロケふる」の加盟店サポート担当です。
画面はスクリーンショットでしか見えません。人と同じようにマウスとキーボードで操作してください。

画面の構成：
- 上部のタブで「掲載したいイベント」「サイトA イベント投稿」「サイトB イベント投稿」を切り替えられます。
- 画面最下部の黒い帯は録画用の字幕表示です。操作対象ではないので無視してください。

仕事のルール：
- 一覧のイベントを、観光サイトA・Bの両方に掲載申請します。すでに同じイベントがあれば二重に登録しません。
- 各サイトの書式（日付の形式、文字数の上限、項目の分け方）に合わせて書き直してください。
- 書いてよい事実は一覧にあることだけです。チェック項目（予約制、支払い方法など）は、一覧から読み取れるものだけ選びます。
- 料金はふるさと納税アプリ「ロケふる」で支払えること、対象は倉敷市外にお住まいの方であることを、紹介文かPR文に入れてください。
- 投稿・登録ボタンを押す前に、必ず ask_human で担当者に内容の要点を伝えて了承を取ってください。了承がなければ押しません。
- 入力欄に文字を入れる前に、その欄をクリックしてください。登録後は一覧で結果を確認してください。

字幕：作業の区切りごとに show_caption で、いま何をしているか・何を判断したかを短い日本語（40文字以内）で表示してください。
最後に、各サイトに申請したイベントと、判断に迷った点を短く報告してください。
"""

TASK = "掲載したいイベント一覧の内容を、観光サイトAとサイトBに掲載申請してください。"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--auto-answer", action="store_true", help="確認依頼にすべて「はい」と答える")
    ap.add_argument("--max-turns", type=int, default=200)
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    # 返礼品デモ用の設定を、このデモ用に差し替える
    base.SYSTEM_PROMPT = SYSTEM_PROMPT
    base.AUTO_ANSWERS = [(r".", "はい、その内容で進めてください。")]

    httpd = server.serve(args.port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    run_dir = ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()
    tools = [{"type": "computer_toolset_20260801"}, *base.CUSTOM_TOOLS]
    messages = [{"role": "user", "content": TASK}]
    final_text = ""

    with sync_playwright() as pw:
        launch = {"headless": not args.headed}
        if Path("/opt/pw-browsers/chromium").exists():
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        browser = pw.chromium.launch(**launch)
        context = browser.new_context(viewport=base.VIEWPORT, locale="ja-JP", record_video_dir=str(run_dir), record_video_size=base.VIEWPORT)
        page = context.new_page()
        page.goto(f"http://127.0.0.1:{args.port}/?caption=1")
        page.wait_for_timeout(1500)
        desktop = base.Desktop(page)
        desktop.caption("AIに指示：" + TASK[:34] + "…")

        for _ in range(args.max_turns):
            try:
                response = base.create(client, messages, tools, args.effort)
            except anthropic.AuthenticationError:
                print("⚠️ APIキーが無効です。環境変数 ANTHROPIC_API_KEY を確認してください。")
                break
            if response.stop_reason == "refusal":
                print("⚠️ モデルが処理を断りました:", getattr(response, "stop_details", None))
                break
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    final_text = block.text
                    print(f"\n🤖 {block.text}")
            if response.stop_reason != "tool_use":
                break
            messages.append({"role": "user", "content": base.handle_tool_calls(desktop, response.content, args.auto_answer)})

        page.wait_for_timeout(2000)
        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()

    httpd.shutdown()
    (run_dir / "result.json").write_text(json.dumps({"final_report": final_text, "listings": server._listings}, ensure_ascii=False, indent=2), encoding="utf-8")
    if video_path and shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(run_dir / "demo.mp4")], check=False)
    print(f"結果: {run_dir}")


if __name__ == "__main__":
    main()
