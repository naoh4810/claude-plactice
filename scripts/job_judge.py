"""案件本文をプロフィールDBと照合して判定し、応募文を生成する（Claude）。

環境変数:
  ANTHROPIC_API_KEY  必須
  JOB_MODEL          任意（デフォルト claude-opus-5-5）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

import anthropic
import yaml
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_common import DEFAULT_MODEL, ROOT  # noqa: E402

JUDGE_PROMPT_FILE = ROOT / "prompts" / "job_judge.md"
PROPOSAL_PROMPT_FILE = ROOT / "prompts" / "proposal.md"


class Judgement(BaseModel):
    title: str = Field(description="案件名")
    client: str = Field(description="発注者名（不明なら空文字）")
    budget: str = Field(description="報酬・予算（本文の表記のまま。不明なら空文字）")
    deadline: str = Field(description="応募締切（不明なら空文字）")
    summary: str = Field(description="通知用の要点（3行以内）")
    verdict: Literal["〇", "△", "×"]
    score: int = Field(description="0〜100 の適合度")
    reasons: str = Field(description="判定理由")
    missing_requirements: list[str] = Field(description="満たせていない必須要件")


def _model() -> str:
    # 空文字（未設定の変数）もデフォルトへ落とす
    return os.environ.get("JOB_MODEL") or DEFAULT_MODEL


def _profile_text(profile: dict) -> str:
    return yaml.safe_dump(profile, allow_unicode=True, sort_keys=False)


def ng_hit(body: str, profile: dict) -> str | None:
    """NGキーワードに当たればその語を返す（Claude を呼ばずに × にする）。"""
    return next((w for w in profile.get("ng_keywords", []) if w and w in body), None)


def judge(client: anthropic.Anthropic, body: str, profile: dict) -> Judgement:
    resp = client.messages.parse(
        model=_model(),
        max_tokens=4000,
        output_config={"effort": "medium"},
        # プロフィール部分は毎回同じなのでキャッシュして料金を抑える
        system=[
            {"type": "text", "text": JUDGE_PROMPT_FILE.read_text(encoding="utf-8")},
            {
                "type": "text",
                "text": f"## プロフィールDB\n```yaml\n{_profile_text(profile)}```",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": f"## 案件本文\n{body}"}],
        output_format=Judgement,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError(f"判定できませんでした（stop_reason={resp.stop_reason}）")
    return resp.parsed_output


def write_proposal(body: str, profile: dict, judgement_note: str) -> str:
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=_model(),
        max_tokens=16000,
        output_config={"effort": "high"},
        system=PROPOSAL_PROMPT_FILE.read_text(encoding="utf-8"),
        messages=[
            {
                "role": "user",
                "content": (
                    f"## プロフィールDB\n```yaml\n{_profile_text(profile)}```\n\n"
                    f"## 事前判定\n{judgement_note}\n\n"
                    f"## 案件本文\n{body}"
                ),
            }
        ],
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "refusal":
        raise RuntimeError("応募文の生成が拒否されました")
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not text:
        raise RuntimeError("応募文が空でした")
    return text
