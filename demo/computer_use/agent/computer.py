"""仮想デスクトップ（Xvfb）を xdotool / ImageMagick で操作する実行部。

Claude の computer_toolset_20260801 が返すメンバーツール
（screenshot, left_click, type, key, scroll ...）を1つずつ実行する。
画面は 1280x800 固定なので、座標のスケーリングは不要。
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import time

DISPLAY = os.environ.get("DISPLAY", ":1")
# 操作後、画面描画が落ち着くまでの待ち時間（秒）
SETTLE_SECONDS = float(os.environ.get("DEMO_SETTLE_SECONDS", "0.6"))
# デモで見せるために、入力を少しゆっくりにする（ミリ秒/文字）
TYPE_DELAY_MS = os.environ.get("DEMO_TYPE_DELAY_MS", "12")

_BUTTONS = {"left_click": 1, "middle_click": 2, "right_click": 3}
_CLICK_REPEAT = {"double_click": 2, "triple_click": 3}
_SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}


class ComputerError(Exception):
    """操作に失敗したとき。メッセージは Claude にそのまま返す。"""


def _xdotool(*args: str) -> str:
    proc = subprocess.run(
        ["xdotool", *args],
        env={**os.environ, "DISPLAY": DISPLAY},
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise ComputerError(f"xdotool {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _png(crop: tuple[int, int, int, int] | None = None) -> str:
    cmd = ["import", "-window", "root"]
    if crop:
        x0, y0, x1, y1 = crop
        if x1 <= x0 or y1 <= y0:
            raise ComputerError(f"invalid zoom region: {list(crop)}")
        cmd += ["-crop", f"{x1 - x0}x{y1 - y0}+{x0}+{y0}", "+repage"]
    cmd.append("png:-")
    proc = subprocess.run(
        cmd, env={**os.environ, "DISPLAY": DISPLAY}, capture_output=True, timeout=30
    )
    if proc.returncode != 0:
        raise ComputerError(f"screenshot failed: {proc.stderr.decode(errors='ignore')}")
    return base64.standard_b64encode(proc.stdout).decode()


def _image(data: str) -> list[dict]:
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}]


def _with_modifiers(modifiers: str | None, action: list[str]) -> list[str]:
    """`text` に修飾キー（例: "shift", "ctrl"）があれば押しながら action を実行する。"""
    if not modifiers:
        return action
    keys = modifiers.replace(" ", "").split("+")
    args: list[str] = []
    for k in keys:
        args += ["keydown", k]
    args += action
    for k in reversed(keys):
        args += ["keyup", k]
    return args


def _paste(text: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode(),
        env={**os.environ, "DISPLAY": DISPLAY},
        check=True,
        timeout=10,
    )
    _xdotool("key", "ctrl+v")
    time.sleep(0.15)


def _type(text: str) -> None:
    """文字入力。xdotool type は日本語の文字や改行を取りこぼすことがあるため、
    タブ・改行はキー操作、日本語を含む部分はクリップボード経由の貼り付けで入力する。
    （表計算で「タブ区切り＋改行」でまとめて入力しても1セルずつ正しく入るようにする）"""
    for token in re.split(r"(\t|\n)", text):
        if token == "\t":
            _xdotool("key", "Tab")
        elif token == "\n":
            _xdotool("key", "Return")
        elif token.isascii():
            if token:
                _xdotool("type", "--delay", TYPE_DELAY_MS, "--", token)
        else:
            _paste(token)


def _move_args(inp: dict) -> list[str]:
    coord = inp.get("coordinate")
    if coord is None:
        return []
    x, y = coord
    return ["mousemove", "--sync", str(int(x)), str(int(y))]


def screenshot() -> str:
    """base64 PNG。パネルのプレビューやログ用にも使う。"""
    return _png()


def run(name: str, inp: dict) -> list[dict] | str:
    """メンバーツールを1つ実行し、tool_result の content を返す。"""
    if name == "screenshot":
        return _image(_png())

    if name == "zoom":
        return _image(_png(tuple(int(v) for v in inp["region"])))

    if name in _BUTTONS or name in _CLICK_REPEAT:
        button = _BUTTONS.get(name, 1)
        repeat = _CLICK_REPEAT.get(name, 1)
        click = ["click", "--repeat", str(repeat), "--delay", "80", str(button)]
        _xdotool(*_move_args(inp), *_with_modifiers(inp.get("text"), click))
    elif name == "left_click_drag":
        sx, sy = inp["start_coordinate"]
        ex, ey = inp["coordinate"]
        drag = [
            "mousemove", "--sync", str(int(sx)), str(int(sy)),
            "mousedown", "1",
            "mousemove", "--sync", str(int(ex)), str(int(ey)),
            "mouseup", "1",
        ]
        _xdotool(*_with_modifiers(inp.get("text"), drag))
    elif name == "mouse_move":
        _xdotool(*_move_args(inp))
    elif name == "left_mouse_down":
        _xdotool("mousedown", "1")
    elif name == "left_mouse_up":
        _xdotool("mouseup", "1")
    elif name == "cursor_position":
        out = _xdotool("getmouselocation", "--shell")
        pos = dict(line.split("=", 1) for line in out.split())
        return f"X={pos['X']},Y={pos['Y']}"
    elif name == "scroll":
        button = _SCROLL_BUTTONS[inp["scroll_direction"]]
        amount = int(inp.get("scroll_amount", 3))
        scroll = ["click", "--repeat", str(amount), "--delay", "30", str(button)]
        _xdotool(*_move_args(inp), *_with_modifiers(inp.get("text"), scroll))
    elif name == "type":
        _type(inp["text"])
    elif name == "key":
        repeat = int(inp.get("repeat", 1))
        _xdotool("key", "--repeat", str(repeat), "--delay", "60", "--", inp["text"])
    elif name == "hold_key":
        keys = inp["text"].replace(" ", "").split("+")
        _xdotool(*[a for k in keys for a in ("keydown", k)])
        time.sleep(min(float(inp["duration"]), 300))
        _xdotool(*[a for k in reversed(keys) for a in ("keyup", k)])
    elif name == "wait":
        time.sleep(min(float(inp.get("duration", 1)), 300))
    else:
        raise ComputerError(f"unsupported action: {name}")

    time.sleep(SETTLE_SECONDS)
    return "OK"
