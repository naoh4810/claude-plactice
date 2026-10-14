"""ロケトク SNS運用デモのローカルサーバー（標準ライブラリのみ）。

    python server.py    # http://127.0.0.1:8766/

  /approve/      案1：AIの投稿案を人が確認・修正・承認する画面（承認分はCSVで予約投稿ツールへ）
  /kit/          案2：加盟店向け投稿キット（python build_kits.py で生成）
  /              案3：イベント掲載代行デモ（掲載したいイベント一覧＋架空の観光サイトA・B）
"""

import csv
import io
import json
import sys
import threading
from copy import deepcopy
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
MERCHANTS = json.loads((ROOT / "data" / "merchants.json").read_text(encoding="utf-8"))
DRAFTS = json.loads((ROOT / "data" / "drafts.json").read_text(encoding="utf-8"))

# 案3：観光サイトに最初から載っている（他の主催者の）イベント
SEED_LISTINGS = {
    "a": [{"id": 1, "title": "倉敷春宵あかり（終了）", "organizer": "倉敷市観光協会（デモ）", "status": "掲載中", "created_at": "2026-03-01 10:00"}],
    "b": [{"id": 1, "title": "瀬戸内アートめぐり", "organizer": "瀬戸内アート実行委員会（デモ）", "status": "公開中", "created_at": "2026-09-30 09:00"}],
}
LISTING_STATUS = {"a": "審査待ち", "b": "受付済み"}

_lock = threading.Lock()
_drafts = []
_listings = {}


def reset():
    with _lock:
        _drafts[:] = deepcopy(DRAFTS["drafts"])
        _listings.clear()
        _listings.update(deepcopy(SEED_LISTINGS))


def events():
    """掲載したいイベント（加盟店データのうち event があるもの）"""
    rows = []
    for m in MERCHANTS["merchants"]:
        if m.get("event"):
            rows.append({"merchant": m["name"], "description": m["news"], **m["event"]})
    return rows


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt, *args):
        if "/api/" in self.path:
            sys.stderr.write("[server] " + (fmt % args) + "\n")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/api/merchants":
            return self._json(200, MERCHANTS)
        if url.path == "/api/drafts":
            with _lock:
                return self._json(200, {"note": DRAFTS["note"], "drafts": _drafts})
        if url.path == "/api/export.csv":
            # 承認済みの投稿を、予約投稿ツール（Meta Business Suite／Make など）に渡すCSVにする
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["予約日時", "SNS", "加盟店", "本文", "ハッシュタグ", "画像メモ"])
            names = {m["id"]: m["name"] for m in MERCHANTS["merchants"]}
            with _lock:
                for d in sorted((d for d in _drafts if d["status"] == "approved"), key=lambda d: d["scheduled"]):
                    w.writerow([d["scheduled"], d["platform"], names.get(d["merchant"], d["merchant"]), d["text"], " ".join(d["hashtags"]), d["image_idea"]])
            body = ("﻿" + buf.getvalue()).encode("utf-8")  # Excelで文字化けしないようBOM付き
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=approved_posts.csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if url.path == "/api/events":
            return self._json(200, events())
        if url.path == "/api/listings":
            site = q.get("site", [""])[0]
            if site not in ("a", "b"):
                return self._json(400, {"error": "site must be 'a' or 'b'"})
            with _lock:
                return self._json(200, _listings[site])
        if url.path == "/api/state":
            with _lock:
                return self._json(200, {"drafts": _drafts, "listings": _listings})
        return super().do_GET()

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == "/api/reset":
            reset()
            return self._json(200, {"ok": True})
        data = self._body()
        if url.path.startswith("/api/drafts/"):
            did = url.path.rsplit("/", 1)[1]
            with _lock:
                d = next((x for x in _drafts if x["id"] == did), None)
                if not d:
                    return self._json(404, {"error": "not found"})
                for k in ("status", "text", "scheduled", "reviewer_note"):
                    if k in data:
                        d[k] = data[k]
                return self._json(200, d)
        if url.path == "/api/listings":
            site, item = data.get("site"), data.get("item") or {}
            if site not in ("a", "b"):
                return self._json(400, {"error": "site must be 'a' or 'b'"})
            missing = [k for k in ("title", "organizer") if not str(item.get(k, "")).strip()]
            if missing:
                return self._json(422, {"error": "必須項目が未入力です", "fields": missing})
            with _lock:
                item["id"] = max((x["id"] for x in _listings[site]), default=0) + 1
                item["status"] = LISTING_STATUS[site]
                item["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                _listings[site].append(item)
            return self._json(201, item)
        return self._json(404, {"error": "not found"})


def serve(port=8766):
    reset()
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8766
    print(f"デモサーバー起動: http://127.0.0.1:{port}/approve/ ・ /kit/ ・ /?caption=1")
    serve(port).serve_forever()
