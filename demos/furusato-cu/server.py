"""デモ用の架空ふるさと納税ポータル（A・B）と返礼品登録シートを配信するローカルサーバー。

標準ライブラリだけで動きます。データはメモリ上に持ち、/api/reset で初期状態に戻ります。

    python server.py            # http://127.0.0.1:8765/
"""

import json
import sys
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
SHEET = json.loads((ROOT / "data" / "sheet.json").read_text(encoding="utf-8"))

# ポータルごとに最初から1件だけ登録済みの返礼品を置き、「既存データがある管理画面」に見せる
SEED = {
    "a": [{
        "id": 1, "name": "倉敷美観地区 着物レンタル（1名）", "vendor": "きもの処 倉敷",
        "category": "体験", "amount": 18000, "description": "着物を着て美観地区を散策",
        "period": "2026/04/01〜2027/03/31", "capacity": 1, "reservation": "要",
        "status": "公開中", "created_at": "2026-09-20 10:12",
    }],
    "b": [{
        "id": 1, "name": "大原美術館 ペア入館券", "vendor": "倉敷アートツアーズ",
        "category": "チケット", "amount": 8000, "description": "大原美術館の入館券2枚",
        "period": "2026/04/01〜2027/03/31", "capacity": 2, "reservation": "不要",
        "status": "掲載中", "created_at": "2026-09-18 15:40",
    }],
}
# 新規登録直後と、公開（審査申請）操作後のステータス
DRAFT_STATUS = {"a": "下書き", "b": "申請前"}
PUBLISHED_STATUS = {"a": "公開中", "b": "審査中"}

_lock = threading.Lock()
_items = {}


def reset():
    with _lock:
        _items.clear()
        _items.update({k: [dict(x) for x in v] for k, v in SEED.items()})


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):
        if "/api/" in self.path:
            sys.stderr.write("[server] " + (fmt % args) + "\n")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _portal(self, value):
        if value not in _items:
            self._json(400, {"error": "portal must be 'a' or 'b'"})
            return None
        return value

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/sheet":
            return self._json(200, SHEET)
        if url.path == "/api/items":
            portal = self._portal(parse_qs(url.query).get("portal", [""])[0])
            if portal:
                with _lock:
                    return self._json(200, _items[portal])
            return
        if url.path == "/api/state":
            with _lock:
                return self._json(200, _items)
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/reset":
            reset()
            return self._json(200, {"ok": True})
        data = self._body()
        portal = self._portal(data.get("portal"))
        if not portal:
            return
        if url.path == "/api/items":
            item = data.get("item") or {}
            missing = [k for k in ("name", "vendor", "category", "amount") if not str(item.get(k, "")).strip()]
            if missing:
                return self._json(422, {"error": "必須項目が未入力です", "fields": missing})
            with _lock:
                item["id"] = max((x["id"] for x in _items[portal]), default=0) + 1
                item["status"] = DRAFT_STATUS[portal]
                item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                _items[portal].append(item)
            return self._json(201, item)
        if url.path == "/api/publish":
            ids = set(data.get("ids") or [])
            with _lock:
                for item in _items[portal]:
                    if item["id"] in ids and item["status"] == DRAFT_STATUS[portal]:
                        item["status"] = PUBLISHED_STATUS[portal]
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})


def serve(port=8765):
    reset()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return httpd


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"デモサーバー起動: http://127.0.0.1:{port}/")
    serve(port).serve_forever()
