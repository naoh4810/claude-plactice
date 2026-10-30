# link-tracker：リンクを踏んだ人の都道府県を計測する

App Store の「ロケふる」のリンクを直接配る代わりに、このリンクを配ります。
クリックされると**都道府県・端末・配布元を記録**してから、App Store のページへ転送します。

```
ユーザー → https://link-tracker.xxx.workers.dev/go?src=instagram
             │ ① 日時・都道府県・端末・配布元を記録（IPアドレスそのものは保存しない）
             ▼ ② すぐに転送（ユーザーには一瞬で App Store が開くように見える）
          https://apps.apple.com/jp/app/id6503939543
```

- 動かす場所：Cloudflare Workers（**無料枠で十分**。1日10万リクエストまで）
- 開発者アカウント・サーバーは不要です

---

## セットアップ（初回だけ・約15分）

### 1. 準備
- Cloudflare の無料アカウントを作ります：<https://dash.cloudflare.com/sign-up>
- パソコンに Node.js（LTS版）を入れます：<https://nodejs.org/ja>

### 2. このフォルダでコマンドを実行

```bash
cd tools/link-tracker
npm install

# Cloudflare にログイン（ブラウザが開くので「Allow」を押す）
npx wrangler login

# データベースを作る
npx wrangler d1 create link-tracker
```

最後のコマンドの出力に `database_id = "xxxxxxxx-...."` が表示されます。
その値を `wrangler.toml` の `REPLACE_WITH_YOUR_DATABASE_ID` と置き換えてください。

```bash
# テーブルを作る
npm run db:init

# 集計画面のパスワードを決める（入力を求められるので、推測されにくい文字列を入れる）
npx wrangler secret put STATS_KEY

# 公開する
npm run deploy
```

`deploy` の出力に `https://link-tracker.<あなたのサブドメイン>.workers.dev` が表示されます。これが配るリンクの土台です。

---

## 使い方

### 配るリンク
配る場所ごとに `src=` を変えると、**どこから来たか**も分かります（半角英数字・`-`・`_`、40文字まで）。

| 配る場所 | リンク |
|---|---|
| Instagram | `https://link-tracker.xxx.workers.dev/go?src=instagram` |
| LINE | `https://link-tracker.xxx.workers.dev/go?src=line` |
| チラシのQRコード | `https://link-tracker.xxx.workers.dev/go?src=flyer` |

### 集計を見る
```
https://link-tracker.xxx.workers.dev/stats?key=（決めたパスワード）
```
- 都道府県別・配布元別・端末別・日別のクリック数と割合が見られます（期間は7日・30日・90日・365日で切り替え）
- 「CSVダウンロード」から全クリックを Excel で開けます

---

## 知っておくこと
- **計測されるのは、このリンクを踏んだ人だけ**です。App Store のリンクを直接踏んだ人や、過去のクリックは計測できません。
- 都道府県は **IPアドレスからの推定**です。スマホの回線（ドコモ・au・ソフトバンクなど）では、実際とは違う県（東京・大阪など）と出ることがあります。Wi-Fi経由のほうが正確です。
- SNS がリンクのプレビューを取得するアクセスなど、ボットによるアクセスは数えません。
- 同じ人が2回踏むと2件と数えます（クリック数であって、人数ではありません）。
- 転送先を変えたいときは、`wrangler.toml` の `TARGET_URL` を書き換えて `npm run deploy` を実行します。
- 公開するサイトやアプリにプライバシーポリシーがあれば、「リンクのクリック時に、おおよその地域と端末の種類を計測しています」と一文入れておくと安心です。
