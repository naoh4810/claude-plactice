"""加盟店データから、案1の投稿案（data/drafts.json）と案2の投稿キット文面（data/kits.json）を Claude API で作る。

    export ANTHROPIC_API_KEY=...
    python generate.py drafts     # 案1：ロケふる公式アカウント用の投稿案
    python generate.py kits       # 案2：加盟店に配る投稿キットの文面

生成物は必ず人が確認してから使う前提です（案1は approve 画面で承認、案2は加盟店に渡す前に確認）。
"""

import json
import sys
from pathlib import Path
from typing import List, Literal

import anthropic
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
MODEL = "claude-opus-5-5"

RULES = """\
- 書いてよい事実は、渡した加盟店データにあることだけ。価格・日付・時間を推測で補わない。
- データにないが書きたい事実は本文に入れず、checks に「加盟店に要確認」として残す。
- ふるさと納税の控除額や「実質○円」などの断定はしない。
- 寄付できるのは倉敷市に住民票がない人だけ。地元向けに見える表現は避ける。
- 顔が写る写真・動画は肖像権の確認が要るので、なるべく手元や後ろ姿で構成する。
"""


class Draft(BaseModel):
    merchant: str
    platform: Literal["Instagram", "X", "TikTok"]
    scheduled: str  # "YYYY-MM-DD HH:MM"
    text: str
    hashtags: List[str]
    image_idea: str
    checks: List[str]


class Drafts(BaseModel):
    drafts: List[Draft]


class Kit(BaseModel):
    merchant: str
    headline: str  # 画像に載せる見出し（16文字以内）
    subline: str  # 画像に載せる補足（24文字以内）
    caption: str  # 加盟店が自分のInstagramに投稿する文
    story_text: str  # ストーリーズ用の一言
    hashtags: List[str]
    google_post: str  # Googleマップの店舗情報に載せる投稿文
    checks: List[str]


class Kits(BaseModel):
    kits: List[Kit]


def ask(prompt: str, schema):
    client = anthropic.Anthropic()
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
        )
    except anthropic.AuthenticationError:
        sys.exit("APIキーが無効です。環境変数 ANTHROPIC_API_KEY を確認してください。")
    if response.stop_reason == "refusal":
        sys.exit(f"モデルが処理を断りました: {response.stop_details}")
    return response.parsed_output


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "drafts"
    merchants = (ROOT / "data" / "merchants.json").read_text(encoding="utf-8")

    if mode == "drafts":
        result = ask(
            "あなたは現地決済型ふるさと納税アプリ「ロケふる」のSNS担当です。\n"
            "次の加盟店データから、ロケふる公式アカウント用の投稿案を作ってください。\n"
            "各加盟店につき Instagram 1本、話題性のある加盟店には X や TikTok（15秒動画の台本）も追加。\n"
            "投稿日時は今日から2週間以内で、平日昼か夜に分散させてください。\n\n"
            f"ルール:\n{RULES}\n加盟店データ:\n{merchants}",
            Drafts,
        )
        drafts = [{"id": f"d{i}", **d.model_dump(), "status": "pending", "reviewer_note": ""}
                  for i, d in enumerate(result.drafts, 1)]
        out = {"note": "generate.py が Claude API で作成。必ず承認画面で人が確認してから予約投稿してください。", "drafts": drafts}
        (ROOT / "data" / "drafts.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"投稿案 {len(drafts)} 件を data/drafts.json に保存しました")

    elif mode == "kits":
        result = ask(
            "あなたは現地決済型ふるさと納税アプリ「ロケふる」の加盟店サポート担当です。\n"
            "次の加盟店それぞれに、『この店ではロケふるが使えます』と伝える投稿キットの文面を作ってください。\n"
            "加盟店自身のアカウントで投稿する前提なので、一人称は店の立場で書いてください。\n\n"
            f"ルール:\n{RULES}\n加盟店データ:\n{merchants}",
            Kits,
        )
        out = {"note": "generate.py が Claude API で作成。加盟店に渡す前に人が確認してください。",
               "kits": [k.model_dump() for k in result.kits]}
        (ROOT / "data" / "kits.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"投稿キット {len(out['kits'])} 件を data/kits.json に保存しました。画像は python build_kits.py で作ります")

    else:
        sys.exit("使い方: python generate.py drafts|kits")


if __name__ == "__main__":
    main()
