"""Slack に新着案件を通知する（Incoming Webhook）。

環境変数:
  SLACK_WEBHOOK_URL    必須（未設定なら通知せず内容を表示するだけ）
  SLACK_MENTION_USER   任意。〇△があるときにメンションする Slack のメンバーID（例: U0123ABCD）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_common import VERDICT_LABEL, JobRow  # noqa: E402


def build_message(rows: list[JobRow]) -> str | None:
    picks = sorted(
        (r for r in rows if r.verdict in ("〇", "△")),
        key=lambda r: (r.verdict != "〇", -int(r.score or 0)),
    )
    skipped = sum(1 for r in rows if r.verdict == "×")
    if not picks and not skipped:
        return None

    counts = f"〇{sum(r.verdict == '〇' for r in picks)}件 △{sum(r.verdict == '△' for r in picks)}件"
    head = f"新着案件 {counts}（×{skipped}件は台帳のみ）"
    mention = os.environ.get("SLACK_MENTION_USER")
    if picks and mention:
        head = f"<@{mention}> {head}"

    lines = [head]
    for r in picks:
        meta = "｜".join(x for x in (r.site, r.budget and f"報酬 {r.budget}", r.deadline and f"締切 {r.deadline}") if x)
        lines += [
            "",
            f"*{r.verdict}{VERDICT_LABEL[r.verdict]} [{r.id}] {r.title}*",
            meta,
            f"> {r.summary.replace(chr(10), chr(10) + '> ')}",
            f"理由: {r.reasons}",
        ]
        if r.missing:
            lines.append(f"不足: {r.missing}")
        lines.append(r.url)
    if picks:
        lines += ["", "応募するときは「000313 に応募したい」のように ID を伝えてください。"]
    return "\n".join(lines)


def notify(rows: list[JobRow]) -> None:
    text = build_message(rows)
    if text is None:
        print("[notify] 新着なし。通知しません。")
        return
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        print("[notify] SLACK_WEBHOOK_URL 未設定のため表示のみ:\n" + text)
        return
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()
    print("[notify] Slack に通知しました。")
