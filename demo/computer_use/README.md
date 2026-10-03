# Computer Use 営業デモ

お客さまの目の前で **AIがパソコンを操作して事務作業を片付ける** 様子を見せるデモ環境です。
業種に合わせたシナリオを選んで「▶ 実行」を押すと、Claude が仮想デスクトップ上で
ブラウザと表計算ソフトを操作し、最後に Slack へ報告します。

```
ブラウザで「コントロールパネル」を開く（http://localhost:8080）
 ├─ 左：AIが操作している画面（マウスが勝手に動く）
 └─ 右：シナリオ選択 ／ AIの実況（「いま〇〇しています」）／ Slackへの報告内容
```

| シナリオ | 見せる相手 | 内容 |
|---|---|---|
| `form_survey` アンケート回答の集計と報告 | 福祉・障害者支援、スクール、サロンなど | フォーム回答一覧 → Calcに転記 → 事業所別に平均を数式で集計 → .xlsx保存 → Slack報告 |
| `invoice_ledger` 請求書PDFの台帳転記とインボイスチェック | 税理士・司法書士・会計事務所、経理のある中小企業 | 請求書PDFを1枚ずつ開く → 台帳に転記 → 登録番号なし・計算違いを検出 → .xlsx保存 → Slack報告 |

画面に出るデータはすべて**デモ用の架空データ**です（`site/` 以下）。請求書には、登録番号の記載漏れと金額の不一致をわざと1件ずつ入れてあります。

---

## 準備（初回だけ）

1. **Docker Desktop** をインストールする。
2. リポジトリ直下の `.env` に次を記入する（`.env.example` を参照）。
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   # 任意：未設定なら Slack には送らず、パネルに内容を表示するだけ
   SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
   ```
   Slack の Webhook URL は、Slack の「アプリ」→「Incoming Webhooks」で、デモ用チャンネルを選ぶと発行できます。
   お客さまに見せるので、**デモ専用のワークスペースかチャンネル**を使ってください。

## 起動

```bash
cd demo/computer_use
docker compose up --build
```

初回のビルドは10分ほどかかります。`コントロールパネル: http://localhost:8080` と表示されたら、ブラウザで開きます。

- 実行中に「停止」を押すと、次の操作の前に止まります。
- 録画は `demo/computer_use/recordings/` に保存されます（等速版と4倍速版）。交流会では4倍速版を見せると1〜2分に収まります。
- ターミナルから実行することもできます：`docker compose exec demo python3 -m agent.cli form_survey`

## 設定（`.env` で上書き）

| 変数 | 既定値 | 内容 |
|---|---|---|
| `DEMO_MODEL` | `claude-opus-5-5` | 使うモデル。テンポを上げたいときは `claude-sonnet-5-5` も試す価値あり |
| `DEMO_EFFORT` | `medium` | 考える深さ（`low` / `medium` / `high`）。`low` は速いが取りこぼしが増える |
| `DEMO_MAX_TURNS` | `80` | 何往復で打ち切るか（暴走防止） |
| `DEMO_RECORD` | `1` | `0` で録画しない |
| `DEMO_RECORD_SPEED` | `4` | 早送り版の倍率 |

## シナリオを追加する

`scenarios/` に YAML を1つ置くだけで、パネルにボタンが増えます。

```yaml
title: 競合店の口コミ調査
audience: 美容サロン、整体院、歯科医院
pitch: 近隣の競合店の口コミをAIが集めて、改善ポイントをまとめます。
minutes: 5
task: |
  （Claude への指示。開くURL、表の作り方、保存名、Slackに報告する内容を具体的に書く）
```

架空データのページが必要なら `site/<名前>/index.html` に置くと、
仮想デスクトップ内のブラウザから `http://localhost:8080/site/<名前>/` で開けます。

## 仕組み

```
panel/server.py   コントロールパネル（シナリオ実行・実況ログ・録画）
agent/loop.py     Claude とのやり取り（computer_toolset_20260801 + post_to_slack ツール）
agent/computer.py 画面操作の実行（xdotool で操作、ImageMagick でスクリーンショット）
agent/slack.py    Slack Incoming Webhook への投稿
```

- 画面は 1280×800 の仮想ディスプレイ（Xvfb）です。noVNC でブラウザに映しています。
- 日本語は `xdotool type` だと文字を取りこぼすため、クリップボード経由で貼り付けています。タブと改行はキー操作として送るので、表計算に「タブ区切り＋改行」でまとめて入力できます。
- 費用の目安は1回あたり1ドル前後です（スクリーンショットの枚数によって変わります）。本番前に一度実行して、Anthropic Console で実際の金額を確認してください。

## 注意

- 実際のお客さま情報（営業シートなど）は、この環境では開かないでください。画面がそのまま相手に見えます。
- AIの操作は毎回少しずつ違います。大事な商談の前には一度通しで実行し、録画をバックアップとして用意しておくと安心です。
