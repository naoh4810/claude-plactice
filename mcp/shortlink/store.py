"""短縮リンクとクリック記録の取得・作成。

バックエンドは2種類:
  - WorkerBackend : 本番。Cloudflare Workers の短縮リンク（worker/）の管理 API を呼ぶ
  - SampleBackend : 認証なしのデモ用。sample_shortlink.json（架空の観光キャンペーン）。作成はメモリ上のみ
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SAMPLE_PATH = Path(__file__).with_name("sample_shortlink.json")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


@dataclass
class Link:
    code: str
    target: str
    label: str
    source: str
    created_at: str


@dataclass
class Click:
    code: str
    ts: datetime                  # UTC（タイムゾーン付き）
    country: str | None
    region_code: str | None
    region: str | None
    city: str | None
    referer_host: str | None
    device: str | None
    app: str | None
    is_bot: bool


def _click(row: dict) -> Click:
    return Click(
        row["code"], datetime.fromisoformat(row["ts"].replace("Z", "+00:00")), row.get("country"),
        row.get("region_code"), row.get("region"), row.get("city"), row.get("referer_host"),
        row.get("device"), row.get("app"), bool(row.get("is_bot")),
    )


class ShortlinkError(RuntimeError):
    pass


class BaseBackend:
    base_url: str = ""

    def links(self) -> list[Link]:
        raise NotImplementedError

    def clicks(self, code: str | None, since: datetime, until: datetime) -> list[Click]:
        """since 以上 until 未満（どちらも UTC）のクリック。"""
        raise NotImplementedError

    def create_link(self, target: str, code: str | None, label: str, source: str) -> Link:
        raise NotImplementedError

    def short_url(self, code: str) -> str:
        return f"{self.base_url.rstrip('/')}/{code}"


class SampleBackend(BaseBackend):
    def __init__(self, path: Path = SAMPLE_PATH):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.base_url = data["base_url"]
        self._links = [Link(**link) for link in data["links"]]
        self._clicks = [_click(c) for c in data["clicks"]]

    def links(self) -> list[Link]:
        return list(self._links)

    def clicks(self, code: str | None, since: datetime, until: datetime) -> list[Click]:
        return [c for c in self._clicks if (code is None or c.code == code) and since <= c.ts < until]

    def create_link(self, target: str, code: str | None, label: str, source: str) -> Link:
        if not re.match(r"^https?://", target):
            raise ShortlinkError("転送先は http / https の URL にしてください")
        code = code or f"s{len(self._links) + 1:05d}"
        if not CODE_RE.match(code):
            raise ShortlinkError("code は英数字・-・_ の3〜32文字にしてください")
        if any(link.code == code for link in self._links):
            raise ShortlinkError(f"code「{code}」は使われています")
        link = Link(code, target, label, source, datetime.now().astimezone().isoformat())
        self._links.append(link)
        return link

    def last_click_date(self) -> datetime:
        return max(c.ts for c in self._clicks)


class WorkerBackend(BaseBackend):
    """worker/ を Cloudflare にデプロイした短縮リンクの管理 API を使う。"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self._token = token

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read().decode("utf-8")).get("error", "")
            except ValueError:
                message = ""
            raise ShortlinkError(f"短縮リンクの API がエラーを返しました（{e.code}）: {message}") from None

    def links(self) -> list[Link]:
        return [Link(**link) for link in self._request("GET", "/api/links")["links"]]

    def clicks(self, code: str | None, since: datetime, until: datetime) -> list[Click]:
        query = {"from": since.isoformat(), "to": until.isoformat()}
        if code:
            query["code"] = code
        body = self._request("GET", f"/api/clicks?{urllib.parse.urlencode(query)}")
        if body.get("truncated"):
            raise ShortlinkError("クリックが多すぎて一度に取得できません。期間を短くしてください")
        return [_click(c) for c in body["clicks"]]

    def create_link(self, target: str, code: str | None, label: str, source: str) -> Link:
        body = {"target": target, "label": label, "source": source}
        if code:
            body["code"] = code
        return Link(**self._request("POST", "/api/links", body))


def open_backend() -> BaseBackend:
    """SHORTLINK_BASE_URL と SHORTLINK_ADMIN_TOKEN があれば本番、無ければサンプル。"""
    base = os.environ.get("SHORTLINK_BASE_URL")
    token = os.environ.get("SHORTLINK_ADMIN_TOKEN")
    if base and token:
        return WorkerBackend(base, token)
    return SampleBackend()
