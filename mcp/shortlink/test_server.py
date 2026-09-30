"""サンプルモードのツールと、手元で動かした Worker（worker/dev/server.mjs）を相手にした結合テスト。

実行: python -m pytest mcp/shortlink/test_server.py
（結合テストは Node.js 22 以上が必要。無ければ飛ばす）
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

os.environ.pop("SHORTLINK_BASE_URL", None)
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402
from prefectures import prefecture_label  # noqa: E402
from store import SampleBackend, ShortlinkError, WorkerBackend  # noqa: E402

SAMPLE = json.loads((HERE / "sample_shortlink.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def sample_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "backend", SampleBackend())
    monkeypatch.setenv("SHORTLINK_EXPORT_DIR", str(tmp_path))


# --- 都道府県 -------------------------------------------------------------------

def test_prefecture_label():
    assert prefecture_label("JP", "13", "Tokyo") == "東京都"
    assert prefecture_label("JP", "1", None) == "北海道"
    assert prefecture_label("JP", None, "Okayama") == "岡山県"
    assert prefecture_label("JP", None, "Kyoto Prefecture") == "京都府"
    assert prefecture_label("JP", None, None) == "日本（都道府県不明）"
    assert prefecture_label("US", "CA", "California") == "海外（US）"
    assert prefecture_label(None, None, None) == "不明"


# --- サンプルモードの集計 ---------------------------------------------------------

def test_click_report_excludes_bots_and_matches_raw_data():
    report = server.click_report(date_from="2026-09-14", date_to="2026-09-29")
    humans = [c for c in SAMPLE["clicks"] if not c["is_bot"]]
    assert report["clicks"] == len(humans)
    assert report["bots_excluded"] == len(SAMPLE["clicks"]) - len(humans)
    assert sum(report["daily"].values()) == report["clicks"]
    assert sum(p["clicks"] for p in report["by_prefecture"]) == report["clicks"]


def test_each_channel_reaches_its_area():
    by_link = {row["code"]: row for row in server.click_report(campaign="autumn", date_from="2026-09-14")["by_link"]}
    assert by_link["autumn-ff"]["top_prefectures"][0]["name"] == "東京都"     # 港区のフリーペーパー
    assert by_link["autumn-qr"]["top_prefectures"][0]["name"] == "岡山県"     # 地元の駅ポスター


def test_daily_uses_japan_time():
    # UTC 9/28 15:00 = JST 9/29 0:00 のクリックは 9/29 に数える
    backend = SampleBackend()
    backend._clicks = [c for c in backend._clicks if c.code != "autumn-x"]
    from store import _click
    backend._clicks.append(_click({"code": "autumn-x", "ts": "2026-09-28T15:00:00.000Z", "country": "JP", "region_code": "13"}))
    server.backend = backend
    report = server.click_report(code="autumn-x", date_from="2026-09-28", date_to="2026-09-29")
    assert report["daily"] == {"2026-09-28": 0, "2026-09-29": 1}


def test_create_campaign_links_validates_codes():
    res = server.create_campaign_links("https://example.com/app", "winter", ["flyer", "instagram"], "冬キャンペーン")
    assert [l["short_url"] for l in res["links"]] == ["https://s.example.jp/winter-flyer", "https://s.example.jp/winter-instagram"]
    with pytest.raises(ValueError, match="使えない文字"):
        server.create_campaign_links("https://example.com/app", "冬", ["チラシ"])
    with pytest.raises(ValueError, match="すでにある"):
        server.create_campaign_links("https://example.com/app", "winter", ["flyer"])


def test_list_links_counts_recent_clicks():
    rows = {r["code"]: r for r in server.list_links(campaign="autumn", days=30)}
    assert set(rows) == {"autumn-ff", "autumn-ig", "autumn-x", "autumn-tt", "autumn-qr"}
    assert rows["autumn-qr"]["clicks_last_30_days"] == 25


def test_make_qr_writes_svg():
    pytest.importorskip("qrcode")
    res = server.make_qr("autumn-qr")
    assert Path(res["path"]).read_text(encoding="utf-8").lstrip().startswith("<?xml")
    with pytest.raises(ValueError, match="list_links"):
        server.make_qr("nothing")


# --- 手元で動かした Worker との結合テスト ------------------------------------------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def local_worker():
    if not shutil.which("node"):
        pytest.skip("Node.js がありません")
    port = _free_port()
    proc = subprocess.Popen(
        ["node", "--no-warnings", str(HERE / "worker" / "dev" / "server.mjs"), str(port)],
        env={**os.environ, "ADMIN_TOKEN": "e2e-token"}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base}/api/links", timeout=1)
        except urllib.error.HTTPError:
            break                       # 401 が返れば起動している
        except OSError:
            time.sleep(0.1)
    yield base
    proc.terminate()
    proc.wait(timeout=5)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _visit(url: str, region_code: str, ua: str) -> urllib.error.HTTPError:
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": ua, "X-Dev-Country": "JP", "X-Dev-Region-Code": region_code})
    with pytest.raises(urllib.error.HTTPError) as e:     # 302 は転送せずに例外として受け取る
        opener.open(req, timeout=5)
    return e.value


def test_end_to_end_with_local_worker(local_worker, monkeypatch):
    monkeypatch.setattr(server, "backend", WorkerBackend(local_worker, "e2e-token"))
    links = server.create_campaign_links("https://example.com/app", "e2e", ["flyer", "poster"])["links"]
    flyer, poster = (l["short_url"] for l in links)

    iphone_instagram = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Instagram 300.0"
    response = _visit(flyer, "13", iphone_instagram)
    assert response.code == 302 and response.headers["Location"] == "https://example.com/app"
    _visit(flyer, "13", "Mozilla/5.0 (Linux; Android 15)")
    _visit(poster, "33", "Mozilla/5.0 (Windows NT 10.0)")
    _visit(poster, "33", "facebookexternalhit/1.1")          # ボット

    report = server.click_report(campaign="e2e")
    assert report["clicks"] == 3 and report["bots_excluded"] == 1
    by_link = {row["code"]: row for row in report["by_link"]}
    assert by_link["e2e-flyer"]["top_prefectures"][0] == {"name": "東京都", "clicks": 2, "share": "100%"}
    assert by_link["e2e-poster"]["top_prefectures"][0]["name"] == "岡山県"
    assert {a["name"] for a in report["by_app"]} == {"Instagram", "アプリ外（通常のブラウザ等）"}
    assert [l["code"] for l in server.list_links(campaign="e2e")] == ["e2e-flyer", "e2e-poster"]


def test_worker_errors_are_reported(local_worker):
    bad_token = WorkerBackend(local_worker, "wrong")
    with pytest.raises(ShortlinkError, match="401"):
        bad_token.links()
    good = WorkerBackend(local_worker, "e2e-token")
    with pytest.raises(ShortlinkError, match="400"):
        good.create_link("javascript:alert(1)", None, "", "")
