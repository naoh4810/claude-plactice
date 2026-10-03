"""Slack への報告（Incoming Webhook）。

SLACK_WEBHOOK_URL が未設定のときは送信せず、ログに出すだけ（ドライラン）。
"""

from __future__ import annotations

import json
import os
import urllib.request


def post(text: str) -> str:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        return "OK（ドライラン：SLACK_WEBHOOK_URL 未設定のため送信していません）"
    req = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        body = res.read().decode(errors="ignore")
    if body.strip() != "ok":
        raise RuntimeError(f"Slack webhook returned: {body}")
    return "OK（Slackに投稿しました）"


TOOL = {
    "name": "post_to_slack",
    "description": (
        "チームのSlackチャンネルに報告メッセージを投稿する。"
        "作業がすべて終わったら、最後に1回だけ使う。Slackの画面は操作しないこと。"
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "投稿する本文（Slackのmrkdwn形式。*太字*、箇条書きは「• 」）",
            }
        },
        "required": ["text"],
        "additionalProperties": False,
    },
}
