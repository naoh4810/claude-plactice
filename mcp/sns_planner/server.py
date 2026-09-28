"""SNS一括投稿MCP：1つのネタから各 SNS 向けの文面を作り分け、投稿カレンダーと週次レポートで回す。

「週に何本かを定期的に出したいが、SNS ごとに文字数も書き方も違って手が回らない」を解決するデモ。
文面は Claude が書き、このサーバーは各 SNS のルールのチェック・予定の管理・数字の集計を受け持つ。
実際の投稿は各 SNS のアプリや予約投稿ツールで行う（投稿 API は使わない）。

できること（ツール）:
  - platform_rules    : SNS ごとの文字数・ハッシュタグ・画像の要否などのルール
  - add_idea / list_ideas : ネタ帳
  - check_text        : 文面がルールに収まっているかのチェック
  - save_draft / edit_draft : ネタから SNS ごとの下書きを保存（ルール違反があれば保存しない）
  - schedule_post     : 下書きに投稿日時を入れる（同じ SNS に近い時間の予定があれば警告）
  - calendar          : 期間の予定と、週ごとの投稿目標に対する過不足
  - mark_posted / record_metrics : 投稿したことと、表示回数・いいね などの数字を記録
  - performance_report: SNS ごとの反応率、反応が良かった投稿、目標の達成状況
  - export_calendar   : 予定を CSV に書き出す（チームで共有・予約投稿ツールへの転記用）

起動:
  python mcp/sns_planner/server.py
  （SNS_DATA_PATH が無ければ架空の観光案内アカウントのサンプルをメモリ上で使う）
"""

from __future__ import annotations

import csv
import io
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from rules import RULES, check
from store import METRICS, Planner, open_planner

mcp = MCPServer("sns-planner")
planner: Planner = open_planner()

CONFLICT_HOURS = 12
ENGAGEMENT = ("likes", "comments", "shares", "saves")


def _parse_day(value: str | None, label: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"{label}「{value}」を日付として読めません（例: 2026-10-01）") from None


def _parse_when(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f"日時「{value}」を読めません（例: 2026-10-01T19:00）") from None


def _when(post: dict) -> datetime | None:
    value = post.get("posted_at") or post.get("scheduled_at")
    return datetime.fromisoformat(value) if value else None


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _engagement(post: dict) -> int:
    return sum(post["metrics"].get(k, 0) for k in ENGAGEMENT)


def _summary(post: dict) -> dict:
    when = _when(post)
    return {"id": post["id"], "platform": post["platform"], "status": post["status"],
            "when": when.isoformat(timespec="minutes") if when else None,
            "idea": planner.idea(post["idea_id"])["title"], "text": post["text"], "media": post["media"]}


def _check_post(post: dict) -> dict:
    return check(post["platform"], post["text"], has_media=bool(post["media"]))


@mcp.tool()
def platform_rules() -> dict:
    """SNS ごとのルール（文字数の上限、ハッシュタグ、画像・動画の要否、URL がタップできるか）と書き方のコツ。"""
    return {key: {"label": r.label, "max_chars": r.max_chars, "max_hashtags": r.max_hashtags,
                  "recommended_hashtags": r.recommended_hashtags, "needs_media": r.needs_media,
                  "links_clickable": r.links_clickable, "tip": r.tip}
            for key, r in RULES.items()}


@mcp.tool()
def set_weekly_targets(targets: dict[str, int], account: str | None = None) -> dict:
    """SNS ごとの「週に何本出すか」の目標を設定する（calendar・performance_report の過不足の基準）。

    Args:
        targets: 例 {"instagram": 2, "x": 3}。0 を渡すとその SNS の目標を外す
        account: アカウント名（任意）
    """
    unknown = [k for k in targets if k not in RULES]
    if unknown:
        raise ValueError(f"未対応の SNS です: {unknown}（対応: {', '.join(RULES)}）")
    if any(v < 0 for v in targets.values()):
        raise ValueError("目標はマイナスにできません")
    merged = {**planner.data.get("weekly_targets", {}), **targets}
    planner.data["weekly_targets"] = {k: v for k, v in merged.items() if v > 0}
    if account:
        planner.data["account"] = account
    planner.save()
    return {"account": planner.data["account"], "weekly_targets": planner.data["weekly_targets"]}


@mcp.tool()
def add_idea(title: str, notes: str = "", tags: list[str] | None = None) -> dict:
    """ネタ帳にネタを追加する。notes には日時・価格・場所など、文面に使う事実を書いておく。"""
    return planner.add_idea(title, notes, tags or [])


@mcp.tool()
def list_ideas(unused_only: bool = False) -> list[dict]:
    """ネタ帳の一覧と、それぞれがどの SNS で下書き・予定・投稿済みになっているか。

    Args:
        unused_only: true なら、まだどの SNS にも下書きが無いネタだけ
    """
    by_idea: dict[str, dict[str, str]] = defaultdict(dict)
    for p in planner.data["posts"]:
        by_idea[p["idea_id"]][p["platform"]] = p["status"]
    rows = [{**idea, "posts": by_idea.get(idea["id"], {})} for idea in planner.data["ideas"]]
    return [r for r in rows if not r["posts"]] if unused_only else rows


@mcp.tool()
def check_text(platform: str, text: str, has_media: bool = False) -> dict:
    """文面が SNS のルールに収まっているかを確認する。errors はそのままでは投稿できない問題、warnings は改善点。

    Args:
        platform: x / instagram / threads / tiktok / youtube_shorts
        text: 本文（ハッシュタグも含める）
        has_media: 画像・動画を付ける予定なら true
    """
    return check(platform, text, has_media)


@mcp.tool()
def save_draft(idea_id: str, platform: str, text: str, media: str = "") -> dict:
    """ネタから SNS 用の下書きを保存する。ルール違反（errors）があれば保存せず、直すべき点を返す。

    文面には、ネタの notes とユーザーから聞いた事実だけを使う。日時・価格・場所を推測で書かない。

    Args:
        idea_id: list_ideas の id
        platform: x / instagram / threads / tiktok / youtube_shorts
        text: 本文（ハッシュタグも含める）
        media: 付ける画像・動画の説明（例: 「店先の写真3枚」）。未定なら空
    """
    result = check(platform, text, has_media=bool(media))
    if not result["ok"]:
        return {"saved": False, "check": result}
    post = planner.add_post(idea_id, platform, text, media)
    return {"saved": True, "post": _summary(post), "check": result}


@mcp.tool()
def edit_draft(post_id: str, text: str | None = None, media: str | None = None) -> dict:
    """下書き・予定の文面や素材を直す。投稿済みは直せない。ルール違反があれば保存しない。"""
    post = planner.post(post_id)
    if post["status"] == "posted":
        raise ValueError(f"{post_id} は投稿済みなので直せません")
    candidate = {**post, "text": post["text"] if text is None else text, "media": post["media"] if media is None else media}
    result = _check_post(candidate)
    if not result["ok"]:
        return {"saved": False, "check": result}
    planner.update_post(post_id, text=candidate["text"], media=candidate["media"])
    return {"saved": True, "post": _summary(planner.post(post_id)), "check": result}


@mcp.tool()
def schedule_post(post_id: str, when: str) -> dict:
    """下書きに投稿日時を入れて「予定」にする。同じ SNS で前後12時間以内に別の予定・投稿があれば警告する。

    Args:
        post_id: save_draft / calendar の id
        when: 投稿日時（例: 2026-10-01T19:00）
    """
    post = planner.post(post_id)
    if post["status"] == "posted":
        raise ValueError(f"{post_id} は投稿済みです")
    result = _check_post(post)
    if not result["ok"]:
        return {"scheduled": False, "check": result}
    at = _parse_when(when)
    warnings = list(result["warnings"])
    if at.date() < planner.today():
        warnings.append("過去の日時です")
    for other in planner.data["posts"]:
        other_at = _when(other)
        if other["id"] != post_id and other["platform"] == post["platform"] and other_at \
                and abs(other_at - at) < timedelta(hours=CONFLICT_HOURS):
            warnings.append(f"{other['id']}（{other_at:%m/%d %H:%M}）と近すぎます。同じ SNS は{CONFLICT_HOURS}時間以上あけるのがおすすめです")
    planner.update_post(post_id, status="scheduled", scheduled_at=at.isoformat(timespec="minutes"))
    return {"scheduled": True, "post": _summary(planner.post(post_id)), "warnings": warnings}


@mcp.tool()
def calendar(date_from: str | None = None, date_to: str | None = None) -> dict:
    """期間の予定・投稿済みの一覧と、週（月曜始まり）ごとの投稿目標に対する過不足。日時が未定の下書きも返す。

    Args:
        date_from: 開始日（例: 2026-09-28）。省略で今日
        date_to: 終了日。省略で開始日から13日後（2週間分）
    """
    start = _parse_day(date_from, "date_from") or planner.today()
    end = _parse_day(date_to, "date_to") or start + timedelta(days=13)
    posts = [p for p in planner.data["posts"] if _when(p) and start <= _when(p).date() <= end]
    posts.sort(key=_when)

    targets = planner.data.get("weekly_targets", {})
    weeks = []
    week = _week_start(start)
    while week <= end:
        counts: dict[str, int] = defaultdict(int)
        for p in planner.data["posts"]:
            if _when(p) and _week_start(_when(p).date()) == week:
                counts[p["platform"]] += 1
        weeks.append({
            "week_of": week.isoformat(),
            "planned_or_posted": dict(counts),
            "short": {k: t - counts[k] for k, t in targets.items() if counts[k] < t},
        })
        week += timedelta(days=7)

    return {
        "period": [start.isoformat(), end.isoformat()],
        "weekly_targets": targets,
        "posts": [_summary(p) for p in posts],
        "weeks": weeks,
        "unscheduled_drafts": [_summary(p) for p in planner.data["posts"] if p["status"] == "draft"],
    }


@mcp.tool()
def mark_posted(post_id: str, posted_at: str | None = None, url: str | None = None) -> dict:
    """投稿したことを記録する。posted_at を省略すると予定日時（無ければ今）を使う。"""
    post = planner.post(post_id)
    at = _parse_when(posted_at) if posted_at else (_when(post) or datetime.now())
    planner.update_post(post_id, status="posted", posted_at=at.isoformat(timespec="minutes"), url=url)
    return _summary(planner.post(post_id))


@mcp.tool()
def record_metrics(
    post_id: str,
    impressions: int | None = None,
    likes: int | None = None,
    comments: int | None = None,
    shares: int | None = None,
    saves: int | None = None,
    clicks: int | None = None,
) -> dict:
    """投稿の数字（各 SNS のインサイト画面の値）を記録する。渡した項目だけ上書きする。

    Args:
        post_id: 投稿の id
        impressions: 表示回数（Instagram のリーチ、X のインプレッション、TikTok の再生数など）
        likes: いいね
        comments: コメント・返信
        shares: シェア・リポスト
        saves: 保存
        clicks: リンクのクリック
    """
    post = planner.post(post_id)
    if post["status"] != "posted":
        raise ValueError(f"{post_id} はまだ投稿済みになっていません。先に mark_posted を呼んでください")
    values = dict(zip(METRICS, (impressions, likes, comments, shares, saves, clicks)))
    negative = [k for k, v in values.items() if v is not None and v < 0]
    if negative:
        raise ValueError(f"マイナスの値は記録できません: {negative}")
    metrics = {**post["metrics"], **{k: v for k, v in values.items() if v is not None}}
    planner.update_post(post_id, metrics=metrics)
    return {"id": post_id, "metrics": metrics}


@mcp.tool()
def performance_report(date_from: str | None = None, date_to: str | None = None, top: int = 3) -> dict:
    """期間に投稿したものの反応をまとめる。反応率 =（いいね+コメント+シェア+保存）÷ 表示回数。

    Args:
        date_from: 開始日。省略で終了日の13日前（直近2週間）
        date_to: 終了日。省略で今日
        top: 反応率の高い投稿をいくつ返すか
    """
    end = _parse_day(date_to, "date_to") or planner.today()
    start = _parse_day(date_from, "date_from") or end - timedelta(days=13)
    posted = [p for p in planner.data["posts"]
              if p["status"] == "posted" and start <= _when(p).date() <= end]
    measured = [p for p in posted if p["metrics"].get("impressions")]

    by_platform: dict[str, dict] = {}
    for p in posted:
        row = by_platform.setdefault(p["platform"], {"posts": 0, "impressions": 0, "engagement": 0, "clicks": 0})
        row["posts"] += 1
        row["impressions"] += p["metrics"].get("impressions", 0)
        row["engagement"] += _engagement(p)
        row["clicks"] += p["metrics"].get("clicks", 0)
    for row in by_platform.values():
        row["engagement_rate"] = f"{row['engagement'] / row['impressions'] * 100:.1f}%" if row["impressions"] else None

    ranked = sorted(measured, key=lambda p: _engagement(p) / p["metrics"]["impressions"], reverse=True)
    weeks = calendar(start.isoformat(), end.isoformat())["weeks"]
    return {
        "period": [start.isoformat(), end.isoformat()],
        "posts": len(posted),
        "without_metrics": [p["id"] for p in posted if p not in measured],
        "by_platform": by_platform,
        "top_posts": [{**_summary(p), "engagement_rate": f"{_engagement(p) / p['metrics']['impressions'] * 100:.1f}%",
                       "metrics": p["metrics"]} for p in ranked[:top]],
        "weekly_target_shortfalls": [{"week_of": w["week_of"], "short": w["short"]} for w in weeks if w["short"]],
    }


@mcp.tool()
def export_calendar(date_from: str | None = None, date_to: str | None = None) -> dict:
    """期間の予定・投稿を CSV（Excel で開ける）に書き出す。チームでの共有や予約投稿ツールへの転記に使う。"""
    info = calendar(date_from, date_to)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["日時", "SNS", "状態", "ネタ", "本文", "素材"])
    labels = {"draft": "下書き", "scheduled": "予定", "posted": "投稿済み"}
    for p in info["posts"]:
        writer.writerow([p["when"].replace("T", " "), RULES[p["platform"]].label, labels[p["status"]],
                         p["idea"], p["text"], p["media"]])
    out_dir = Path(os.environ.get("SNS_EXPORT_DIR", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"SNSカレンダー_{info['period'][0]}_{info['period'][1]}.csv"
    path.write_text(buf.getvalue(), encoding="utf-8-sig")
    return {"path": str(path.resolve()), "rows": len(info["posts"])}


@mcp.prompt()
def plan_week() -> str:
    """来週分の投稿を、ネタ → SNS ごとの下書き → 予定 まで一気に作る流れ。"""
    return (
        "来週の SNS 投稿を計画します。\n"
        "1. calendar で来週の予定と、週の目標に対する不足（short）を確認してください。\n"
        "2. list_ideas(unused_only=true) で使えるネタを見て、不足を埋めるネタを選んでください。足りなければ、"
        "ユーザーに最近の出来事や告知したいことを聞いて add_idea で追加してください。\n"
        "3. platform_rules を見て、ネタごとに SNS に合わせて書き分けてください"
        "（X は短く要点、Instagram は保存したくなる情報、TikTok は動画の冒頭で言う一言、など）。"
        "ネタの notes に無い日時・価格・場所は書かず、分からなければユーザーに聞いてください。\n"
        "4. save_draft で保存し、errors があれば直して保存し直してください。\n"
        "5. 同じ SNS は間を空け、平日の昼（12時台）や夜（19〜21時）を中心に schedule_post で日時を入れてください。\n"
        "6. 最後に calendar で1週間分を表にして見せ、直したい点がないかユーザーに確認してください。"
    )


@mcp.prompt()
def weekly_review() -> str:
    """直近2週間の振り返り。"""
    return (
        "SNS の直近2週間を振り返ります。\n"
        "1. performance_report で SNS ごとの反応率、反応が良かった投稿、目標の未達を確認してください。\n"
        "2. 数字が未記録の投稿（without_metrics）があれば、各 SNS のインサイトの数字を教えてもらい record_metrics で記録してください。\n"
        "3. 次の形でまとめてください。\n"
        "   - SNS ごとの数字（投稿数・表示回数・反応率）\n"
        "   - 反応が良かった投稿と、その理由として考えられること（数字の裏付けがあるものだけ）\n"
        "   - 来週やること（どのネタを、どの SNS で、どう変えて出すか）"
    )


if __name__ == "__main__":
    mcp.run()
