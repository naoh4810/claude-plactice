"""短縮リンク＋地域分析MCP：流入元ごとに短縮リンクを発行し、どこから・どの都道府県の人が来たかを集計する。

「アプリのダウンロードが増えたが、どの施策が効いたのか分からない」を解決するデモ。
チラシ・SNS 投稿・ポスターなど媒体ごとに別のリンク（印刷物には QR コード）を配り、
クリックを都道府県・日・アプリ内ブラウザ・端末・リンク別に集計する。

できること（ツール）:
  - overview              : リンク数・直近のクリック数・集計の基準日
  - create_link           : 短縮リンクを1本作る
  - create_campaign_links : 1つの転送先に、媒体ごとのリンクをまとめて作る（例: 秋-チラシ / 秋-instagram）
  - list_links            : リンク一覧と、それぞれのクリック数
  - click_report          : 期間のクリックを都道府県・日別・アプリ別・端末別・リンク別に集計（ボットは除く）
  - make_qr               : 印刷物用の QR コード（SVG）を作る

起動:
  python mcp/shortlink/server.py
  （SHORTLINK_BASE_URL と SHORTLINK_ADMIN_TOKEN が無ければ架空キャンペーンのサンプルモード）
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

from prefectures import prefecture_label
from store import CODE_RE, BaseBackend, Click, SampleBackend, ShortlinkError, open_backend

mcp = MCPServer("shortlink")
backend: BaseBackend = open_backend()

APP_LABELS = {"instagram": "Instagram", "facebook": "Facebook", "line": "LINE", "tiktok": "TikTok"}
DEVICE_LABELS = {"ios": "iPhone / iPad", "android": "Android", "desktop": "パソコン", "other": "その他"}


def _tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("SHORTLINK_TIMEZONE", "Asia/Tokyo"))


def _today() -> date:
    if isinstance(backend, SampleBackend):
        return backend.last_click_date().astimezone(_tz()).date()   # サンプルは過去のデータ
    return datetime.now(_tz()).date()


def _parse_day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{label}「{value}」を日付として読めません（例: 2026-09-01）") from None


def _range(date_from: str | None, date_to: str | None, default_days: int) -> tuple[date, date, datetime, datetime]:
    end = _parse_day(date_to, "date_to") or _today()
    start = _parse_day(date_from, "date_from") or end - timedelta(days=default_days - 1)
    if start > end:
        raise ValueError(f"期間の開始 {start} が終了 {end} より後になっています")
    since = datetime.combine(start, time(), _tz())
    until = datetime.combine(end + timedelta(days=1), time(), _tz())     # 終了日の翌日0時（含まない）
    return start, end, since, until


def _ranked(counter: Counter, top: int | None = None) -> list[dict]:
    total = sum(counter.values())
    items = counter.most_common()
    if top is not None and len(items) > top:
        rest = sum(n for _, n in items[top:])
        items = items[:top] + [("その他", rest)]
    return [{"name": k, "clicks": n, "share": f"{n / total * 100:.0f}%"} for k, n in items] if total else []


@mcp.tool()
def overview() -> dict:
    """接続先・リンク数・直近7日のクリック数（ボット除く）と、集計の基準日（今日）。"""
    _, _, since, until = _range(None, None, 7)
    clicks = [c for c in backend.clicks(None, since, until) if not c.is_bot]
    return {
        "mode": "sample" if isinstance(backend, SampleBackend) else "worker",
        "base_url": backend.base_url,
        "today": _today().isoformat(),
        "links": len(backend.links()),
        "clicks_last_7_days": len(clicks),
    }


@mcp.tool()
def create_link(target_url: str, label: str = "", source: str = "", code: str | None = None) -> dict:
    """短縮リンクを1本作る。媒体ごとに分けたいときは create_campaign_links のほうが早い。

    Args:
        target_url: 転送先（アプリストアのページ、LP など）
        label: 管理用の名前（例: 「秋キャンペーン / 駅ポスター」）
        source: 流入元の種類（例: print / instagram / x / tiktok / line）
        code: 短縮リンクの末尾（英数字・-・_、3〜32文字）。省略で自動
    """
    link = backend.create_link(target_url, code, label, source)
    return {"short_url": backend.short_url(link.code), **link.__dict__}


@mcp.tool()
def create_campaign_links(target_url: str, campaign: str, channels: list[str], label_prefix: str = "") -> dict:
    """1つの転送先に、媒体ごとの短縮リンクをまとめて作る。code は「{campaign}-{channel}」になる。

    例: campaign="autumn", channels=["flyer", "instagram", "x", "poster"]
        → autumn-flyer / autumn-instagram / autumn-x / autumn-poster

    Args:
        target_url: 転送先
        campaign: キャンペーン名（英数字・-・_）
        channels: 媒体の名前（英数字・-・_）。チラシ・ポスターなど印刷物は make_qr で QR にする
        label_prefix: 管理用の名前の頭（例: 「秋キャンペーン」）。省略で campaign
    """
    codes = [f"{campaign}-{ch}" for ch in channels]
    bad = [c for c in codes if not CODE_RE.match(c)]
    if bad:
        raise ValueError(f"code に使えない文字があります: {bad}。campaign と channels は英数字・-・_ で指定してください")
    existing = {link.code for link in backend.links()}
    taken = [c for c in codes if c in existing]
    if taken:
        raise ValueError(f"すでにある code です: {taken}。campaign 名を変えてください")
    created = []
    for ch, code in zip(channels, codes):
        link = backend.create_link(target_url, code, f"{label_prefix or campaign} / {ch}", ch)
        created.append({"channel": ch, "short_url": backend.short_url(link.code), "code": link.code})
    return {"campaign": campaign, "target_url": target_url, "links": created}


@mcp.tool()
def list_links(campaign: str | None = None, days: int = 30) -> list[dict]:
    """リンク一覧と、直近 days 日のクリック数（ボット除く）。

    Args:
        campaign: code の先頭で絞り込む（例: "autumn"）
        days: クリック数を数える直近の日数
    """
    _, _, since, until = _range(None, None, days)
    counts = Counter(c.code for c in backend.clicks(None, since, until) if not c.is_bot)
    return [
        {"code": link.code, "short_url": backend.short_url(link.code), "label": link.label, "source": link.source,
         "target": link.target, f"clicks_last_{days}_days": counts.get(link.code, 0)}
        for link in backend.links() if not campaign or link.code.startswith(campaign)
    ]


@mcp.tool()
def click_report(
    code: str | None = None,
    campaign: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    include_bots: bool = False,
    top_prefectures: int = 10,
) -> dict:
    """期間のクリックを集計する。都道府県・日別（日本時間）・アプリ内ブラウザ・端末・リンク別。

    リンク別（by_link）には、それぞれの上位の都道府県も付くので「どの媒体がどの地域に届いたか」が分かる。
    SNS のリンクプレビューなどのボットは既定で除く。

    Args:
        code: 1本のリンクに絞る
        campaign: code の先頭で絞る（例: "autumn"）
        date_from: 開始日（例: 2026-09-14）。省略で終了日の13日前（直近2週間）
        date_to: 終了日。省略で今日
        include_bots: ボットも数えるなら true
        top_prefectures: 都道府県を上位いくつまで出すか（残りは「その他」）
    """
    start, end, since, until = _range(date_from, date_to, 14)
    clicks = backend.clicks(code, since, until)
    if campaign:
        clicks = [c for c in clicks if c.code.startswith(campaign)]
    bots = sum(c.is_bot for c in clicks)
    if not include_bots:
        clicks = [c for c in clicks if not c.is_bot]

    def pref(c: Click) -> str:
        return prefecture_label(c.country, c.region_code, c.region)

    tz = _tz()
    daily = Counter(c.ts.astimezone(tz).date().isoformat() for c in clicks)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    labels = {link.code: link.label for link in backend.links()}
    by_link = []
    for link_code, n in Counter(c.code for c in clicks).most_common():
        prefs = Counter(pref(c) for c in clicks if c.code == link_code)
        by_link.append({"code": link_code, "label": labels.get(link_code, ""), "clicks": n,
                        "top_prefectures": _ranked(prefs, 3)})

    return {
        "period": [start.isoformat(), end.isoformat()],
        "clicks": len(clicks),
        "bots_excluded": 0 if include_bots else bots,
        "by_prefecture": _ranked(Counter(pref(c) for c in clicks), top_prefectures),
        "by_link": by_link,
        "by_app": _ranked(Counter(APP_LABELS.get(c.app, "アプリ外（通常のブラウザ等）") for c in clicks)),
        "by_device": _ranked(Counter(DEVICE_LABELS.get(c.device or "other", "その他") for c in clicks)),
        "by_referrer": _ranked(Counter(c.referer_host or "（なし）" for c in clicks), 5),
        "daily": {d: daily.get(d, 0) for d in days},
    }


@mcp.tool()
def make_qr(code: str) -> dict:
    """短縮リンクの QR コードを SVG で保存する（チラシ・ポスター・フリーペーパー用。拡大してもぼやけない）。"""
    if code not in {link.code for link in backend.links()}:
        raise ValueError(f"リンク「{code}」はありません。list_links で確認してください")
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        raise ShortlinkError("QR コードの作成には qrcode ライブラリが必要です（pip install qrcode）") from None
    url = backend.short_url(code)
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=20, border=4)
    out_dir = Path(os.environ.get("SHORTLINK_EXPORT_DIR", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"QR_{code}.svg"
    image.save(str(path))
    return {"path": str(path.resolve()), "url": url}


@mcp.prompt()
def campaign_setup(target_url: str, campaign: str) -> str:
    """媒体ごとにリンクを分けて、効果を比べられる状態にする流れ。"""
    return (
        f"キャンペーン「{campaign}」の転送先 {target_url} に向けて、媒体ごとの短縮リンクを用意します。\n"
        "1. このキャンペーンで使う媒体（チラシ・ポスター・Instagram・X・TikTok・LINE など）をユーザーに確認してください。"
        "同じチラシでも配る地域が違うなら分けて（例: flyer-minato / flyer-kurashiki）。\n"
        "2. create_campaign_links で媒体ごとのリンクを作ってください。\n"
        "3. 印刷物のリンクは make_qr で QR コードを作り、保存先を伝えてください。\n"
        "4. どのリンクをどこに載せるかの一覧表を作り、「SNS の投稿にはそれぞれのリンクを貼る」ことを念押ししてください。"
    )


@mcp.prompt()
def campaign_review(campaign: str) -> str:
    """キャンペーンの効果を媒体ごと・地域ごとに振り返る。"""
    return (
        f"キャンペーン「{campaign}」の効果を振り返ります。\n"
        "1. click_report(campaign=...) で、媒体（リンク）ごとのクリック数と、それぞれ上位の都道府県を確認してください。\n"
        "2. 次の形でまとめてください。\n"
        "   - 媒体ごとのクリック数（多い順）と、届いた地域\n"
        "   - 狙った地域に届いたか（例: 東京で配ったチラシから東京の人が来ているか）\n"
        "   - 日別の山と、その日に何をしたか（ユーザーに聞く）\n"
        "   - 次に増やす媒体・減らす媒体\n"
        "クリック数はアプリのダウンロード数そのものではないこと（途中でやめた人も含む）を添えてください。"
    )


if __name__ == "__main__":
    mcp.run()
