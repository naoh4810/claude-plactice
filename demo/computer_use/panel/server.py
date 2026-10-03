"""デモのコントロールパネル（http://localhost:8080）。

- 左：AIが操作している仮想デスクトップ（noVNC）
- 右：シナリオ選択ボタンと、AIの実況ログ
- /site/ 以下：デモ用の架空データ（フォーム回答一覧、請求書PDF）。仮想デスクトップ内のブラウザから開く
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import loop, scenarios  # noqa: E402
from panel.recorder import Recorder  # noqa: E402

PORT = 8080
RECORD = os.environ.get("DEMO_RECORD", "1") == "1"


class Hub:
    """実行中のデモ1本と、そのイベントログを保持する。"""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.cond = threading.Condition()
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def emit(self, event: dict) -> None:
        with self.cond:
            self.events.append({**event, "id": len(self.events), "ts": time.strftime("%H:%M:%S")})
            self.cond.notify_all()

    def start(self, scenario_id: str) -> None:
        scenario = scenarios.load(scenario_id)
        self.stop = threading.Event()
        self.emit({"type": "scenario", "text": scenario["title"]})

        def target() -> None:
            recorder = Recorder()
            if RECORD:
                try:
                    recorder.start(scenario_id)
                except OSError as err:
                    self.emit({"type": "error", "text": f"録画を開始できませんでした: {err}"})
            try:
                loop.run(scenario["task"], on_event=self.emit, stop=self.stop)
            except Exception as err:  # noqa: BLE001 - パネルに表示して終わる
                self.emit({"type": "error", "text": f"{type(err).__name__}: {err}"})
            finally:
                files = recorder.stop()
                if files:
                    names = "、".join(p.name for p in files)
                    self.emit({"type": "status", "text": f"録画を保存しました: {names}"})
                self.emit({"type": "idle", "text": ""})

        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()


HUB = Hub()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # アクセスログは出さない
        pass

    def _json(self, status: int, body: object) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.path = "/panel/index.html"
        if self.path == "/api/scenarios":
            items = [
                {k: s[k] for k in ("id", "title", "audience", "pitch", "minutes")}
                for s in scenarios.load_all().values()
            ]
            return self._json(200, {"scenarios": items, "running": HUB.running})
        if self.path.startswith("/api/events"):
            return self._sse()
        if not (self.path.startswith("/panel/") or self.path.startswith("/site/")):
            return self._json(404, {"error": "not found"})
        return super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/run":
            if HUB.running:
                return self._json(409, {"error": "別のデモを実行中です"})
            HUB.start(body.get("id", ""))
            return self._json(200, {"ok": True})
        if self.path == "/api/stop":
            HUB.stop.set()
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        sent = 0
        try:
            while True:
                with HUB.cond:
                    HUB.cond.wait_for(lambda: len(HUB.events) > sent, timeout=15)
                    pending = HUB.events[sent:]
                if not pending:
                    self.wfile.write(b": keepalive\n\n")
                for ev in pending:
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                sent += len(pending)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> None:
    handler = partial(Handler, directory=str(ROOT))
    server = ThreadingHTTPServer(("0.0.0.0", PORT), handler)
    print(f"コントロールパネル: http://localhost:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
