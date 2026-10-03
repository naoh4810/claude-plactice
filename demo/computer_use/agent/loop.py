"""Computer Use のエージェントループ。

Claude に画面を見せる → Claude が操作を返す → 実行して結果を返す、を
タスク完了まで繰り返す。途中経過は on_event コールバックで通知する
（コントロールパネルの実況ログ、CLI の標準出力の両方で使う）。
"""

from __future__ import annotations

import os
import threading
from typing import Callable

import anthropic

from . import computer, slack

MODEL = os.environ.get("DEMO_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("DEMO_EFFORT", "medium")
MAX_TURNS = int(os.environ.get("DEMO_MAX_TURNS", "80"))

# display:"updates" … 操作の合間に Claude が書く「今なにをしているか」の短いメモを受け取る
# server-side fallback … 安全判定で断られた場合に、別モデルで自動的にやり直す
BETAS = ["thinking-display-updates-2026-08-18", "server-side-fallback-2026-07-01"]

SYSTEM_PROMPT = """\
あなたは中小企業の事務作業を代行するAIアシスタントです。
いま、お客さまの目の前でパソコン（Linuxデスクトップ, 1280x800）を操作して実演しています。

進め方:
- 最初にスクリーンショットで画面を確認してから操作を始める。
- 各操作の前に「いま何をしているか」を日本語で1文だけ、お客さまにわかる言葉で書く（専門用語は使わない）。
- ブラウザは Firefox、表計算は LibreOffice Calc を使う。画面下のパネルの左端のボタンから起動できる。
- 日本語の入力は type ツールでそのまま入力できる（IMEの切り替えは不要）。
- 表への入力は、ブラウザの表をコピーして貼り付けたり、タブ区切りでまとめて type したりして手早く進める。
- 入力や計算のあとは、必ずスクリーンショットで結果を目で確認する。間違いがあれば直す。
- 保存の確認ダイアログで形式を聞かれたら、指定された形式（.xlsx）を選ぶ。
- 作業がすべて終わったら post_to_slack で報告を1回だけ送り、最後に完了したことを短く伝えて終了する。
- 画面に表示されるデータはデモ用の架空データです。
"""


Event = Callable[[dict], None]


def _emit(on_event: Event | None, kind: str, text: str, **extra) -> None:
    if on_event:
        on_event({"type": kind, "text": text, **extra})


def _describe(name: str, inp: dict) -> str:
    """操作ログ用の短い説明。"""
    if name == "type":
        text = inp.get("text", "")
        return f"入力: {text[:40]}{'…' if len(text) > 40 else ''}"
    if name == "key":
        return f"キー: {inp.get('text')}"
    if "coordinate" in inp:
        return f"{name} {inp['coordinate']}"
    return name


def _run_tools(response, on_event: Event | None) -> list[dict]:
    """返ってきた tool_use を順番に実行する。失敗したらそれ以降は実行しない。"""
    results: list[dict] = []
    failed = False
    for block in response.content:
        if block.type != "tool_use":
            continue
        is_computer = getattr(block, "toolset_name", None) == "computer"
        result: dict = {"type": "tool_result", "tool_use_id": block.id}
        if is_computer:
            result["toolset_name"] = "computer"

        if failed:
            result["content"] = "Not executed: an earlier computer action in this turn failed."
            result["is_error"] = True
        elif is_computer:
            try:
                if block.name != "screenshot":
                    _emit(on_event, "action", _describe(block.name, block.input))
                result["content"] = computer.run(block.name, block.input)
            except Exception as err:  # noqa: BLE001 - 失敗内容は Claude に返して立て直させる
                result["content"] = f"Error: {err}"
                result["is_error"] = True
                failed = True
                _emit(on_event, "error", f"操作に失敗: {err}")
        elif block.name == slack.TOOL["name"]:
            try:
                result["content"] = slack.post(block.input["text"])
                _emit(on_event, "slack", block.input["text"])
            except Exception as err:  # noqa: BLE001
                result["content"] = f"Error: {err}"
                result["is_error"] = True
                _emit(on_event, "error", f"Slack送信に失敗: {err}")
        else:
            result["content"] = f"Error: unknown tool {block.name}"
            result["is_error"] = True
        results.append(result)
    return results


def run(task: str, on_event: Event | None = None, stop: threading.Event | None = None) -> None:
    """task（業種別シナリオの指示文）を最後まで実行する。"""
    client = anthropic.Anthropic()
    messages: list = [{"role": "user", "content": task}]
    tools = [{"type": "computer_toolset_20260801"}, slack.TOOL]

    _emit(on_event, "status", f"開始（{MODEL} / effort={EFFORT}）")
    for turn in range(1, MAX_TURNS + 1):
        if stop and stop.is_set():
            _emit(on_event, "status", "停止しました")
            return

        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=BETAS,
            fallbacks="default",
            thinking={"type": "adaptive", "display": "updates"},
            output_config={"effort": EFFORT},
            cache_control={"type": "ephemeral"},
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        # thinking / fallback ブロックも含め、返ってきた内容はそのまま履歴に戻す
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "thinking" and block.thinking.strip():
                _emit(on_event, "note", block.thinking.strip())
            elif block.type == "text" and block.text.strip():
                _emit(on_event, "note", block.text.strip())

        if response.stop_reason == "refusal":
            _emit(on_event, "error", "この依頼は実行できませんでした（refusal）")
            return
        if response.stop_reason == "max_tokens":
            _emit(on_event, "error", "応答が長すぎて途中で止まりました")
            return

        results = _run_tools(response, on_event)
        if not results:
            _emit(on_event, "done", f"完了（{turn}ターン）")
            return
        messages.append({"role": "user", "content": results})

    _emit(on_event, "error", f"{MAX_TURNS}ターンで終わらなかったため停止しました")
