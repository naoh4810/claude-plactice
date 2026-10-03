"""Computer use デモ：返礼品登録シートの内容を、架空のふるさと納税ポータル2つにAIが登録する。

Claude には画面のスクリーンショットだけを渡し、クリック・入力・スクロールで操作させます。
画面のHTML構造やAPIはClaudeに一切教えていないので、人と同じように「見て判断して」操作します。

    export ANTHROPIC_API_KEY=...
    python agent.py                      # 標準のデモ（確認依頼にはターミナルで回答）
    python agent.py --layout v2          # ポータルAの画面レイアウトを変えた版で実行
    python agent.py --auto-answer        # 確認依頼に自動回答（無人で録画したいとき）
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import anthropic
from playwright.sync_api import sync_playwright

import server

ROOT = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"
VIEWPORT = {"width": 1280, "height": 800}

SYSTEM_PROMPT = """\
あなたは、ふるさと納税の返礼品を各ポータルサイトの事業者管理画面に登録する事務スタッフです。
画面はスクリーンショットでしか見えません。人と同じようにマウスとキーボードで操作してください。

画面の構成：
- 上部のタブで「返礼品登録シート」「ポータルA 事業者管理画面」「ポータルB 事業者管理画面」を切り替えられます。
- 画面最下部の黒い帯は録画用の字幕表示です。操作対象ではないので無視してください。

仕事のルール：
- シートの「掲載先」列に従って登録します（「A・B」は両方のポータル）。
- 各ポータルにすでに同じ名前の返礼品がある場合は二重登録しないでください。
- 総務省の基準により、調達価格は寄付金額の3割以下でなければなりません。
  寄付金額が空欄、または調達価格が寄付金額の3割を超える行は、自分で判断せず、ask_human で担当者に確認してください。
  確認が必要な行は後回しにして、問題のない行から先に登録して構いません。
- 登録すると、ポータルAでは「下書き」、ポータルBでは「申請前」になります。
  最後に公開（ポータルBでは審査申請）する前に、必ず ask_human で担当者の了承を取ってください。了承がなければ公開しません。
- 入力欄に文字を入れる前に、その欄をクリックしてフォーカスしてください。保存後は一覧画面で登録結果を確認してください。

字幕：
- 作業の区切りごと（シートを読む、どのポータルで何を登録する、確認結果など）に show_caption で
  「いま何をしているか」を短い日本語（40文字以内）で表示してください。見ている人向けの実況です。
- show_caption は画面操作と同じターンにまとめて呼んで構いません。

最後に、各ポータルに登録・公開した返礼品と、見送った返礼品とその理由を短く報告してください。
"""

TASK = "返礼品登録シートの内容を、ポータルAとポータルBに登録して、担当者の了承を得てから公開まで進めてください。"

CUSTOM_TOOLS = [
    {
        "name": "show_caption",
        "description": "録画を見ている人向けに、画面下部の字幕に現在の作業内容を表示する。画面操作には影響しない。",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "40文字以内の日本語の実況"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "ask_human",
        "description": (
            "担当者（人間）に確認や了承を求める。データの不備、基準違反の疑い、公開などの取り返しのつかない操作の前に使う。"
            "担当者の回答がテキストで返る。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "担当者への質問。対象の行・返礼品名と、何を判断してほしいかを具体的に書く"}},
            "required": ["question"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

# --auto-answer 用の想定回答（無人録画用）。質問文に含まれる語で選ぶ
AUTO_ANSWERS = [
    (r"公開|審査申請", "はい、公開（審査申請）して大丈夫です。"),
    (r"トートバッグ|帆布|縁", "寄付金額は17,000円で登録してください。"),
    (r"備前|ろくろ|陽炎", "今回は登録を見送ってください。事業者と寄付金額を見直します。"),
]

# 実況・確認依頼以外で Claude が使う xdotool 形式のキー名を Playwright のキー名へ
KEY_MAP = {
    "return": "Enter", "enter": "Enter", "kp_enter": "Enter", "tab": "Tab", "escape": "Escape", "esc": "Escape",
    "backspace": "Backspace", "delete": "Delete", "space": " ", "home": "Home", "end": "End",
    "page_up": "PageUp", "pageup": "PageUp", "page_down": "PageDown", "pagedown": "PageDown", "prior": "PageUp", "next": "PageDown",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "ctrl": "Control", "control": "Control", "alt": "Alt", "shift": "Shift", "super": "Meta", "cmd": "Meta", "meta": "Meta",
}


def to_pw_key(combo: str) -> str:
    parts = []
    for p in combo.split("+"):
        k = KEY_MAP.get(p.strip().lower(), p.strip())
        if re.fullmatch(r"[fF]\d{1,2}", k):
            k = k.upper()
        parts.append(k)
    return "+".join(parts)


class Desktop:
    """Playwright のブラウザ1枚を「画面」とみなして、computer toolset の各操作を実行する。"""

    def __init__(self, page):
        self.page = page
        self.cursor = (VIEWPORT["width"] // 2, VIEWPORT["height"] // 2)
        self.dialogs = []
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog):
        self.dialogs.append(dialog.message)
        dialog.accept()

    def _fx(self, js, *args):
        try:
            self.page.evaluate(js, list(args))
        except Exception:
            pass

    def caption(self, text):
        self._fx("([t]) => window.demo && window.demo.caption(t)", text)

    def screenshot_block(self, clip=None):
        png = self.page.screenshot(type="png", clip=clip)
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()}}

    def _modifiers(self, text):
        return [to_pw_key(m) for m in text.split("+")] if text else []

    def _click(self, inp, button="left", count=1):
        if inp.get("coordinate"):
            x, y = inp["coordinate"]
            self.page.mouse.move(x, y, steps=8)
            self.cursor = (x, y)
        x, y = self.cursor
        self._fx("([x, y]) => window.demo && window.demo.click(x, y)", x, y)
        mods = self._modifiers(inp.get("text"))
        for m in mods:
            self.page.keyboard.down(m)
        self.page.mouse.click(x, y, button=button, click_count=count)
        for m in reversed(mods):
            self.page.keyboard.up(m)

    def run(self, name, inp):
        """1操作を実行し、tool_result の content（文字列または content block のリスト）を返す。"""
        page = self.page
        if name == "screenshot":
            page.wait_for_timeout(300)
            return [self.screenshot_block()]
        if name == "zoom":
            x0, y0, x1, y1 = inp["region"]
            return [self.screenshot_block(clip={"x": x0, "y": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)})]
        if name == "left_click":
            self._click(inp)
        elif name == "right_click":
            self._click(inp, button="right")
        elif name == "middle_click":
            self._click(inp, button="middle")
        elif name == "double_click":
            self._click(inp, count=2)
        elif name == "triple_click":
            self._click(inp, count=3)
        elif name == "mouse_move":
            x, y = inp["coordinate"]
            page.mouse.move(x, y, steps=8)
            self.cursor = (x, y)
        elif name == "left_click_drag":
            sx, sy = inp["start_coordinate"]
            x, y = inp["coordinate"]
            page.mouse.move(sx, sy)
            page.mouse.down()
            page.mouse.move(x, y, steps=12)
            page.mouse.up()
            self.cursor = (x, y)
        elif name == "left_mouse_down":
            page.mouse.down()
        elif name == "left_mouse_up":
            page.mouse.up()
        elif name == "cursor_position":
            return f"X={self.cursor[0]}, Y={self.cursor[1]}"
        elif name == "scroll":
            if inp.get("coordinate"):
                x, y = inp["coordinate"]
                page.mouse.move(x, y)
                self.cursor = (x, y)
            amount = int(inp.get("scroll_amount", 3)) * 100
            dx, dy = {"up": (0, -amount), "down": (0, amount), "left": (-amount, 0), "right": (amount, 0)}[inp["scroll_direction"]]
            page.mouse.wheel(dx, dy)
        elif name == "type":
            page.keyboard.type(inp["text"], delay=25)
        elif name == "key":
            for _ in range(int(inp.get("repeat", 1))):
                page.keyboard.press(to_pw_key(inp["text"]))
        elif name == "hold_key":
            key = to_pw_key(inp["text"])
            page.keyboard.down(key)
            page.wait_for_timeout(int(float(inp["duration"]) * 1000))
            page.keyboard.up(key)
        elif name == "wait":
            page.wait_for_timeout(int(min(float(inp.get("duration", 1)), 10) * 1000))
        else:
            raise ValueError(f"未対応の操作です: {name}")
        page.wait_for_timeout(250)
        if self.dialogs:
            msg = "OK（画面にダイアログが表示され、閉じました：" + " / ".join(self.dialogs) + "）"
            self.dialogs.clear()
            return msg
        return "OK"


def ask_human(desktop, question, auto):
    desktop._fx("([q]) => window.demo && window.demo.ask(q)", question)
    print("\n🙋 AIからの確認依頼:\n" + question)
    if auto:
        answer = next((a for pat, a in AUTO_ANSWERS if re.search(pat, question)), "その判断で進めてください。")
        time.sleep(2.5)
        print("（自動回答）" + answer)
    else:
        answer = input("回答を入力してください > ").strip() or "その判断で進めてください。"
    desktop._fx("([a]) => window.demo && window.demo.answer(a)", answer)
    desktop.page.wait_for_timeout(2000)
    desktop._fx("() => window.demo && window.demo.closeAsk()")
    return answer


def handle_tool_calls(desktop, content, auto):
    """1ターン分の tool_use を順に実行する。computer の操作が失敗したら、同じターンの残りの操作は実行しない。"""
    results = []
    failed = False
    for block in content:
        if block.type != "tool_use":
            continue
        toolset = getattr(block, "toolset_name", None)
        result = {"type": "tool_result", "tool_use_id": block.id}
        if toolset == "computer":
            result["toolset_name"] = "computer"
            if failed:
                result.update(is_error=True, content="Not executed: an earlier computer action in this turn failed.")
            else:
                try:
                    out = desktop.run(block.name, block.input)
                    result["content"] = out
                    print(f"  🖱  {block.name} {json.dumps(block.input, ensure_ascii=False) if block.input else ''}")
                except Exception as err:
                    result.update(is_error=True, content=f"Error: {err}")
                    failed = True
                    print(f"  ⚠️  {block.name} 失敗: {err}")
        elif block.name == "show_caption":
            desktop.caption(block.input["text"])
            print(f"💬 {block.input['text']}")
            result["content"] = "OK"
        elif block.name == "ask_human":
            result["content"] = ask_human(desktop, block.input["question"], auto)
        else:
            result.update(is_error=True, content=f"Unknown tool: {block.name}")
        results.append(result)
    return results

def create(client, messages, tools, effort):
    return client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=tools,
        messages=messages,
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        cache_control={"type": "ephemeral"},
        # 古いスクリーンショットを自動で間引き、長い作業でも文脈があふれないようにする
        context_management={"edits": [{
            "type": "clear_tool_uses_20250919",
            "trigger": {"type": "input_tokens", "value": 80000},
            "keep": {"type": "tool_uses", "value": 12},
            "clear_at_least": {"type": "input_tokens", "value": 20000},
            "exclude_tools": ["ask_human"],
        }]},
        # 安全分類器による拒否時は、サーバー側で代替モデルに自動で切り替える
        fallbacks="default",
        betas=["context-management-2025-06-27", "server-side-fallback-2026-07-01"],
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--layout", choices=["v1", "v2"], default="v1", help="ポータルAの画面レイアウト（v2 = リニューアル後）")
    ap.add_argument("--auto-answer", action="store_true", help="確認依頼に想定回答で自動返答する")
    ap.add_argument("--max-turns", type=int, default=150)
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--headed", action="store_true", help="ブラウザ画面を表示して実行する（手元のPC向け）")
    args = ap.parse_args()

    httpd = server.serve(args.port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{args.port}"

    run_dir = ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()
    tools = [{"type": "computer_toolset_20260801"}, *CUSTOM_TOOLS]
    messages = [{"role": "user", "content": TASK}]
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    with sync_playwright() as pw:
        launch = {"headless": not args.headed}
        if os.path.exists("/opt/pw-browsers/chromium"):
            launch["executable_path"] = "/opt/pw-browsers/chromium"
        browser = pw.chromium.launch(**launch)
        context = browser.new_context(viewport=VIEWPORT, locale="ja-JP", record_video_dir=str(run_dir), record_video_size=VIEWPORT)
        page = context.new_page()
        page.goto(f"{base}/?caption=1&layout={args.layout}")
        page.wait_for_timeout(1500)
        desktop = Desktop(page)
        desktop.caption("AIに指示：" + TASK[:34] + "…")
        page.wait_for_timeout(2000)

        final_text = ""
        for turn in range(1, args.max_turns + 1):
            try:
                response = create(client, messages, tools, args.effort)
            except anthropic.AuthenticationError:
                print("⚠️ APIキーが無効です。環境変数 ANTHROPIC_API_KEY を確認してください。")
                break
            u = response.usage
            usage["input"] += u.input_tokens or 0
            usage["output"] += u.output_tokens or 0
            usage["cache_read"] += u.cache_read_input_tokens or 0
            usage["cache_write"] += u.cache_creation_input_tokens or 0

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
            messages.append({"role": "user", "content": handle_tool_calls(desktop, response.content, args.auto_answer)})
        else:
            print(f"⚠️ {args.max_turns} ターンで打ち切りました")

        desktop.caption("完了：" + (final_text.splitlines()[0][:40] if final_text else "作業が終わりました"))
        page.wait_for_timeout(3000)
        video_path = page.video.path() if page.video else None
        context.close()
        browser.close()

    httpd.shutdown()
    (run_dir / "result.json").write_text(json.dumps({"final_report": final_text, "usage": usage, "portals": server._items}, ensure_ascii=False, indent=2), encoding="utf-8")

    # 料金の目安（Claude Opus 5.5：入力 $4 / 出力 $20 / キャッシュ書き込み $5 / キャッシュ読み $0.20 per 1M tokens）
    cost = (usage["input"] * 4 + usage["output"] * 20 + usage["cache_write"] * 5 + usage["cache_read"] * 0.2) / 1_000_000
    print(f"\nトークン: 入力 {usage['input']:,} / 出力 {usage['output']:,} / キャッシュ書込 {usage['cache_write']:,} / キャッシュ読み {usage['cache_read']:,}  （概算 ${cost:.2f}）")
    if video_path:
        mp4 = run_dir / "demo.mp4"
        if shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_path), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(mp4)], check=False)
        print(f"録画: {mp4 if mp4.exists() else video_path}")
    print(f"結果: {run_dir / 'result.json'}")


if __name__ == "__main__":
    sys.exit(main())
