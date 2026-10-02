"""業種別トークMCP：業種から、よくある悩み・刺さる提案（見せるデモ）・30秒トーク・聞くべき質問を返す。

交流会の前に「今日会いそうな業種」の準備をしたり、名刺を見た直後に一言を考えたりするためのもの。
現場で分かったこと（何が刺さったか・外したか）は業種ごとの現場メモに残し、次に同じ業種の手引きを
引いたときに一緒に出す。営業シートMCP（sales-sheet）と組み合わせると、ログの業種から準備できる。

できること（ツール）:
  - list_industries : 手引きがある業種の一覧
  - match_industry  : 名刺の肩書きや事業名などの自由な文から、近い業種を探す
  - get_playbook    : 業種の手引き（悩み・提案とデモの状態・30秒トーク・質問・注意点・現場メモ）
  - prep_for_event  : 参加者（事業名）の一覧から、一人ずつの一言トークと見せるデモをまとめる
  - log_insight     : 現場で分かったことを業種ごとに残す（個人名は書かない）

起動:
  python mcp/talk_playbook/server.py
  （TALK_NOTES_PATH を指定すると現場メモをファイルに保存。指定なしはメモリ上だけ）
"""

from __future__ import annotations

import json
import os
import unicodedata
from datetime import date
from pathlib import Path

from mcp.server.mcpserver import MCPServer

HERE = Path(__file__).parent
PLAYBOOKS = {p["id"]: p for p in json.loads((HERE / "playbooks.json").read_text(encoding="utf-8"))["playbooks"]}
FALLBACK = "general"

# mcp/ のデモ。ready = 実演できる、idea = まだ構想（「作れます」とは言えるが「お見せできます」とは言わない）
DEMOS = {
    "A1": ("営業シートMCP（名刺→記録、フォローが止まっている人の抽出）", "ready"),
    "B1": ("フォーム回答→帳票MCP（フォームの回答から記録・帳票を自動作成）", "ready"),
    "B2": ("ECストア運用MCP（売上レポート・在庫切れ予測・商品説明）", "ready"),
    "B3": ("社内ナレッジ検索MCP（出典付き・資料を外に出さない）", "ready"),
    "B4": ("問題バンクMCP（問題を溜めて先生が確認、生徒の苦手と復習に合わせたプリント）", "ready"),
    "B5": ("SNS一括投稿MCP（SNS ごとの書き分け・週の目標・反応率）", "ready"),
    "B6": ("短縮リンク＋地域分析MCP（媒体別リンク・QR・都道府県別の集計）", "ready"),
    "B7": ("LINE予約・配信MCP（来院ペースから途切れた人を抽出・案内文・効果測定）", "ready"),
    "B8": ("士業の期限・書類MCP（顧問先ごとの期限の自動計算・書類の回収状況・催促の下書き）", "ready"),
    "B9": ("写真→見積MCP（現場写真とメモから見積書のたたき台・抜けのチェック）", "ready"),
    "B10": ("交流会マッチングMCP（相性の良い組み合わせと紹介文）", "idea"),
    "B11": ("家計見直しMCP（明細の分類・固定費・見直し候補・削減シミュレーション）", "ready"),
}


class Notes:
    """業種ごとの現場メモ。TALK_NOTES_PATH があればファイルに保存する。"""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, list[dict]] = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}

    def add(self, industry: str, entry: dict) -> None:
        self.data.setdefault(industry, []).append(entry)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, industry: str) -> list[dict]:
        return self.data.get(industry, [])


mcp = MCPServer("talk-playbook")
notes = Notes(Path(os.environ["TALK_NOTES_PATH"]) if os.environ.get("TALK_NOTES_PATH") else None)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").lower()


def _match(text: str) -> list[tuple[str, int, list[str]]]:
    """キーワードが含まれる業種を、当たったキーワードの文字数の合計が大きい順に返す。"""
    norm = _normalize(text)
    scored = []
    for pid, p in PLAYBOOKS.items():
        hits = [k for k in p["keywords"] if _normalize(k) in norm]
        if hits:
            scored.append((pid, sum(len(k) for k in hits), hits))
    return sorted(scored, key=lambda x: x[1], reverse=True)


def _demo(ref: str | None) -> dict | None:
    if not ref:
        return None
    name, status = DEMOS[ref]
    return {"id": ref, "name": name, "status": "実演できる" if status == "ready" else "構想（まだ見せられない）"}


def _playbook(pid: str) -> dict:
    p = PLAYBOOKS[pid]
    ideas = [{"title": i["title"], "demo": _demo(i.get("demo"))} for i in p["ideas"]]
    show = next((i["demo"] for i in ideas if i["demo"] and i["demo"]["status"] == "実演できる"), None)
    return {
        "id": pid, "name": p["name"], "pains": p["pains"], "ideas": ideas,
        "demo_to_show": show,
        "opener": p["opener"], "questions": p["questions"], "cautions": p["cautions"],
        "field_notes": notes.get(pid),
    }


@mcp.tool()
def list_industries() -> list[dict]:
    """手引きがある業種の一覧（id・名前・判定に使うキーワード・現場メモの件数）。"""
    return [{"id": pid, "name": p["name"], "keywords": p["keywords"], "field_notes": len(notes.get(pid))}
            for pid, p in PLAYBOOKS.items()]


@mcp.tool()
def match_industry(text: str) -> dict:
    """名刺の肩書き・事業名・自己紹介などの自由な文から、近い業種を探す。

    Args:
        text: 例「まちのわ司法書士事務所」「外壁洗浄専門店」「Lステップ代理店」
    """
    ranked = _match(text)
    if not ranked:
        return {"best": FALLBACK, "name": PLAYBOOKS[FALLBACK]["name"], "candidates": [],
                "note": "当てはまる業種が見つからないので、業種不明のときの手引きを使います。相手の事業を一言で聞いてください"}
    best = ranked[0][0]
    return {"best": best, "name": PLAYBOOKS[best]["name"],
            "candidates": [{"id": pid, "name": PLAYBOOKS[pid]["name"], "matched": hits} for pid, _, hits in ranked[:3]]}


@mcp.tool()
def get_playbook(industry: str) -> dict:
    """業種の手引き。industry には id（例: tax_accountant）か、業種を表す自由な文を渡せる。

    ideas の demo.status が「構想」のものは、まだ見せられない。「作れます」とは言えるが
    「お見せできます」とは言わないこと。demo_to_show はその場で実演できるデモ。
    """
    pid = industry if industry in PLAYBOOKS else match_industry(industry)["best"]
    return _playbook(pid)


@mcp.tool()
def prep_for_event(attendees: list[str], event: str = "") -> dict:
    """交流会の参加者（事業名・肩書き）の一覧から、一人ずつの一言トークと見せるデモをまとめる。

    同じ業種の人が複数いれば、業種ごとの準備も1つにまとめる。個人名は渡さなくてよい。

    Args:
        attendees: 例 ["税理士事務所", "整体院", "ECショップ運営", "Instagram 運用代行"]
        event: 交流会の名前（任意）
    """
    rows, industries = [], {}
    for text in attendees:
        pid = match_industry(text)["best"]
        pb = _playbook(pid)
        industries.setdefault(pid, pb)
        rows.append({"attendee": text, "industry": pb["name"], "opener": pb["opener"],
                     "first_question": pb["questions"][0],
                     "demo_to_show": pb["demo_to_show"]["name"] if pb["demo_to_show"] else "（実演できるデモなし。話を聞くことに集中）"})
    demos = sorted({r["demo_to_show"] for r in rows if not r["demo_to_show"].startswith("（")})
    return {
        "event": event,
        "attendees": rows,
        "demos_to_prepare": demos,
        "cautions_by_industry": {pb["name"]: pb["cautions"] for pb in industries.values()},
        "field_notes_by_industry": {pb["name"]: pb["field_notes"] for pb in industries.values() if pb["field_notes"]},
    }


@mcp.tool()
def log_insight(industry: str, insight: str, outcome: str = "") -> dict:
    """現場で分かったことを、業種ごとの現場メモに残す。次に同じ業種の手引きを引くと一緒に出る。

    個人名・会社名・連絡先は書かないこと。「税理士には守秘義務の話を先にすると聞いてもらえた」のように、
    業種に一般化した学びだけを書く。

    Args:
        industry: 業種の id か、業種を表す文
        insight: 分かったこと（何が刺さった・何を外した）
        outcome: 結果（例: 面談につながった / 名刺交換だけ / 刺さらなかった）
    """
    pid = industry if industry in PLAYBOOKS else match_industry(industry)["best"]
    entry = {"date": date.today().isoformat(), "insight": insight.strip(), "outcome": outcome.strip()}
    if not entry["insight"]:
        raise ValueError("insight が空です")
    notes.add(pid, entry)
    return {"industry": PLAYBOOKS[pid]["name"], "saved": entry, "total_notes": len(notes.get(pid)),
            "persisted": notes.path is not None}


@mcp.prompt()
def event_prep(event: str) -> str:
    """交流会の前日の準備。"""
    return (
        f"明日の交流会「{event}」の準備をします。\n"
        "1. 参加者の事業名や業種が分かっていれば教えてもらい、prep_for_event で一人ずつの一言と見せるデモをまとめてください。"
        "分からなければ、よく会う業種（営業シートMCP があれば pipeline_summary や search_contacts から）で準備してください。\n"
        "2. 当日スマホで見られるよう、1人1行（業種／最初の一言／最初の質問／見せるデモ）の表にしてください。\n"
        "3. 実演できるデモのうち、当日すぐ開けるように準備しておくものを挙げてください。"
        "「構想」のデモは見せられないので、話すだけにとどめてください。"
    )


@mcp.prompt()
def after_event(event: str) -> str:
    """交流会の後の振り返り。"""
    return (
        f"交流会「{event}」の振り返りをします。\n"
        "1. 会った人ごとに、業種・話した内容・反応をユーザーに聞いてください（営業シートMCP があればそこから読んでください）。\n"
        "2. 業種ごとに一般化できる学びがあれば、log_insight で現場メモに残してください。個人名・会社名は書かないでください。\n"
        "3. フォローすべき人と、それぞれに送る一言（get_playbook の手引きと現場メモを踏まえて）を挙げてください。"
    )


if __name__ == "__main__":
    mcp.run()
