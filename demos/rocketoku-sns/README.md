# ロケトク向け SNS運用デモ（案1〜3）

現地決済型ふるさと納税アプリ「ロケふる」の SNS 運用を、AIでどう手伝えるかを見せるデモです。
**店舗・イベント・観光サイトはすべて架空**です（`data/merchants.json`）。実在のSNSへの投稿は一切しません。

| 案 | 内容 | AIの使い方 | 人の関わり |
|---|---|---|---|
| 1 | ロケふる公式アカウントの投稿案づくり → 承認 → 予約投稿 | 加盟店の新着情報から、SNSごとの投稿案と確認事項を作る（`generate.py drafts`） | 承認画面で修正・承認・差し戻し。承認分だけCSVで予約投稿ツールへ |
| 2 | 加盟店に配る「ロケふる使えます」投稿キット | 店ごとの投稿文・ストーリーズ文・Googleマップ用の文を作り（`generate.py kits`）、画像を自動生成（`build_kits.py`） | 加盟店に渡す前に確認事項をチェック |
| 3 | 観光サイトへのイベント掲載代行 | **Computer use**：書式の違う2サイトの投稿画面をAIが見て入力（`agent.py`） | 投稿ボタンの前に毎回承認 |

SNSへの投稿そのものはComputer useで自動化しません。各SNSの規約で自動操作が制限されていて、公式の予約投稿機能やAPIの方が安定するからです。
Computer useは、APIがなく画面ごとに書式が違う「周辺業務」（案3）に使います。

## ルール（AIに守らせていること）

- 書いてよい事実は加盟店データにあることだけ。データにないことは本文に書かず、確認事項（⚠）に回す
- 控除額や「実質○円」などは断定しない
- 寄付できるのは倉敷市外に住む人だけなので、その旨を入れる
- 公開・投稿の前は必ず人が承認する

## 動かし方

```bash
cd demos/rocketoku-sns
pip install -r requirements.txt
python server.py          # http://127.0.0.1:8766/
```

- 案1：<http://127.0.0.1:8766/approve/> で投稿案を承認 →「承認済みをCSVで書き出し」
- 案2：<http://127.0.0.1:8766/kit/> でキットを一覧（画像は `web/kit/<店>/post.png`・`story.png`）
- 案3：<http://127.0.0.1:8766/?caption=1> が操作対象の画面

APIキーがあれば、生成とComputer useをAIに任せられます。

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python generate.py drafts       # 案1の投稿案を作り直す → data/drafts.json
python generate.py kits         # 案2の文面を作り直す → data/kits.json
python build_kits.py            # 案2の画像とキット一覧を作る
python agent.py --auto-answer   # 案3をComputer useで実行し、runs/ に録画を保存
```

## 同梱データについて

`data/drafts.json` と `data/kits.json` は、APIキーがない環境でデモを見せるために、Claude Code のセッション内で作成したものです（中身は `generate.py` の出力と同じ形式）。
案3の録画（別途共有）は、Claude Code のセッションがスクリーンショットを見ながら操作したもので、`agent.py` の実行結果ではありません。
