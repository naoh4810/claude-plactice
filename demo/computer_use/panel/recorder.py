"""デモ実行中の画面を録画する（ffmpeg x11grab）。

終了時に、等速版と早送り版（交流会で見せる1〜2分用）の2本を recordings/ に保存する。
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

DISPLAY = os.environ.get("DISPLAY", ":1")
OUT_DIR = Path(os.environ.get("DEMO_RECORD_DIR", "/app/recordings"))
SPEED = float(os.environ.get("DEMO_RECORD_SPEED", "4"))


class Recorder:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.path: Path | None = None

    def start(self, name: str) -> None:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        self.path = OUT_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{name}.mp4"
        self.proc = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error", "-y",
                "-f", "x11grab", "-framerate", "10", "-video_size", "1280x800", "-i", DISPLAY,
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                str(self.path),
            ],
            stdin=subprocess.PIPE,
        )

    def stop(self) -> list[Path]:
        """録画を止め、保存したファイルのパスを返す。"""
        if not self.proc or not self.path:
            return []
        self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc = None

        fast = self.path.with_name(f"{self.path.stem}_x{SPEED:g}.mp4")
        subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-y", "-i", str(self.path),
                "-filter:v", f"setpts=PTS/{SPEED}", "-an", str(fast),
            ],
            timeout=600,
        )
        return [p for p in (self.path, fast) if p.exists()]
