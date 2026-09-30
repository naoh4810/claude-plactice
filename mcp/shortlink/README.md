# 短縮リンク＋地域分析MCP（shortlink）

チラシ・ポスター・SNS 投稿など**媒体ごとに別の短縮リンク**を配り、「どの媒体から・どの都道府県の人が来たか」を Claude に聞けるようにする MCP サーバーです。
「アプリのダウンロードが増えたが、どの施策が効いたのか分からない」「App Store の分析では国までしか分からない」という悩み向けのデモとして作りました。

```
チラシの QR / SNS のリンク ──→ 短縮リンク（Cloudflare Workers）──302──→ アプリストア・LP
                                    │ 都道府県・端末・アプリ名だけ記録
                                    ▼
                          Claude ←─ MCP（このサーバー）: 発行・集計・QR
```

- 転送役は Cloudflare Workers で動かす（`worker/`）。Cloudflare がアクセスに都道府県を付けてくれるので、IP から地域を調べる仕組みを自前で持たなくてよい
- **IP アドレスや User-Agent の全文は保存しない**。残すのは国・都道府県・市区町村、端末の種類（iPhone / Android / パソコン）、アプリ内ブラウザの名前（Instagram / LINE / TikTok / Facebook）、リファラのドメインだけ
- SNS のリンクプレビューなどのボットは記録はするが、集計からは除く

## ツール

| ツール | 内容 |
|---|---|
| `overview` | 接続先・リンク数・直近7日のクリック数 |
| `create_link` | 短縮リンクを1本作る |
| `create_campaign_links` | 1つの転送先に、媒体ごとのリンクをまとめて作る（`autumn-flyer`、`autumn-instagram` …） |
| `list_links` | リンク一覧と直近のクリック数 |
| `click_report` | 都道府県・リンク別（それぞれの上位の都道府県付き）・アプリ内ブラウザ・端末・リファラ・日別（日本時間） |
| `make_qr` | 印刷物用の QR コード（SVG、拡大してもぼやけない） |

プロンプト:

- `campaign_setup` : 媒体を確認 → 媒体ごとのリンク → 印刷物は QR → 「どのリンクをどこに載せるか」の一覧
- `campaign_review` : 媒体ごとのクリックと届いた地域 → 狙った地域に届いたか → 日別の山 → 次に増やす・減らす媒体

## デモの流れ（Claude Code で話しかける例）

架空の「秋の観光キャンペーン」（`sample_shortlink.json`、5媒体・約240クリック）で試せます。

1. 「秋キャンペーンの媒体ごとの効果は？」→ TikTok が最多で全国に分散、港区のフリーペーパーは東京の人が78%、駅ポスターの QR は地元の岡山が86%
2. 「冬キャンペーン用に、チラシ（港区・倉敷）と Instagram と X のリンクを作って。チラシは QR も」→ リンク4本と QR 2枚
3. 「どのアプリから開かれてる？」→ Instagram・TikTok のアプリ内ブラウザの割合（リファラが空でも分かる）

## 動かし方

### 1. サンプルモード（認証なしですぐ試す）

```bash
pip install -r mcp/shortlink/requirements.txt
claude mcp add shortlink -- python mcp/shortlink/server.py
```

### 2. 手元で本物の転送を試す（Cloudflare 不要）

`worker/` のコードをそのまま手元で動かす開発用サーバーがあります（Node.js 22 以上）。

```bash
ADMIN_TOKEN=dev-token node --no-warnings mcp/shortlink/worker/dev/server.mjs 8787
claude mcp add shortlink -e SHORTLINK_BASE_URL=http://localhost:8787 -e SHORTLINK_ADMIN_TOKEN=dev-token -- python mcp/shortlink/server.py
```

手元では Cloudflare の地域情報が付かないので、都道府県は「不明」になります（テストでは `X-Dev-Region-Code` ヘッダーで代わりに渡しています）。

### 3. 本番（Cloudflare Workers にデプロイ）

```bash
cd mcp/shortlink/worker
npx wrangler login
npx wrangler d1 create shortlink          # 表示された database_id を wrangler.toml に書く
npm run db:init                           # テーブルを作る
npx wrangler secret put ADMIN_TOKEN       # 管理用のトークン（長いランダムな文字列）を登録
npm run deploy                            # https://shortlink.<アカウント名>.workers.dev で公開
```

自分のドメイン（例: `s.example.jp`）で使うなら、`wrangler.toml` の `routes` を設定します。個人・小規模の利用なら Workers と D1 の無料枠に収まることが多いですが、最新の上限は Cloudflare の料金ページで確認してください。

MCP の登録:

```bash
claude mcp add shortlink \
  -e SHORTLINK_BASE_URL=https://shortlink.<アカウント名>.workers.dev \
  -e SHORTLINK_ADMIN_TOKEN=<登録したトークン> \
  -- python mcp/shortlink/server.py
```

| 環境変数 | 内容 | 省略時 |
|---|---|---|
| `SHORTLINK_BASE_URL` | 短縮リンクの URL（Worker の公開 URL） | サンプルモード |
| `SHORTLINK_ADMIN_TOKEN` | Worker に登録した `ADMIN_TOKEN` | サンプルモード |
| `SHORTLINK_TIMEZONE` | 日別集計のタイムゾーン | `Asia/Tokyo` |
| `SHORTLINK_EXPORT_DIR` | QR コードの保存先 | `reports/` |

## テスト

```bash
pip install pytest qrcode
python -m pytest mcp/shortlink/test_server.py     # MCP（手元の Worker との結合テストを含む。Node.js 22 以上）
cd mcp/shortlink/worker && npm test              # Worker 単体
```

`mcp/` 配下の各サーバーはモジュール名（`server` など）が同じなので、テストはサーバーごとに分けて実行してください。

## 注意と制限

- **クリック数はダウンロード数ではありません**（ストアのページで閉じた人も含む）。ダウンロード数は App Store Connect / Google Play Console で確認し、「媒体ごとの比率」を見るのに使ってください
- 都道府県は Cloudflare が IP アドレスから推定した値で、モバイル回線では実際と違う県になることがある
- 実際の Cloudflare へのデプロイは、まだ確認していません（Worker のコードは手元の開発用サーバーとテストで確認済み）
