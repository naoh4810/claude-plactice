# 案件ウォッチャー（ランサーズ / クラウドワークス → スプシ → Slack）

ランサーズとクラウドワークスで指定キーワードの**新着案件を検索**し、
**Google スプレッドシートに蓄積**、**新しく見つかった案件だけ Slack に通知**します。

PC の Chrome を自動で動かして検索ページを読むので、**PC が起動していれば**
タスクスケジューラ（Windows）や cron（Mac）で定期実行できます。

```
 あなたのPC（Chrome を自動操作）
   ├─ クラウドワークス 検索（新着順）─┐
   └─ ランサーズ 検索（新着順）──────┤
                                      ▼
                          job_watcher.py ──POST──▶ Apps Script ──▶ スプレッドシート
                                      │                  │ （URLで重複チェック）
                                      ◀── 新規分のURL ───┘
                                      ▼
                                    Slack
```

## なぜ「PC の Chrome」で動かすのか

ランサーズは bot 対策（AWS WAF の「Human Verification」）が入っていて、
サーバーや Google Apps Script から直接アクセスすると弾かれます。
普段使っている PC の実ブラウザで開けば通るので、Playwright で Chrome を操作する方式にしています。
（人間確認が出た場合は画面に表示されるので、一度手で通せば Cookie が `.browser-profile/` に残ります）

> Claude の「computer use」（画面を見てクリックする機能）でも同じことはできますが、
> 毎回 AI が画面を読むので遅く・API 料金もかかります。定期実行にはこのスクリプト方式が向いています。

## セットアップ

### 1. Python 環境
```bash
cd job-watcher
python -m venv .venv
# Windows: .venv\Scripts\activate   /  Mac: source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # Chrome が入っていれば不要だが念のため
```

### 2. スプレッドシート（Apps Script）
1. 新しい Google スプレッドシートを作る
2. **拡張機能 → Apps Script** を開き、`gas/Code.gs` の中身を貼り付けて保存
3. **プロジェクトの設定（歯車）→ スクリプト プロパティ** に `TOKEN` = 任意のランダム文字列 を追加
4. **デプロイ → 新しいデプロイ → 種類: ウェブアプリ**
   - 実行ユーザー: 自分 / アクセスできるユーザー: 全員
   - 承認画面が出たら許可
5. 表示された URL（`https://script.google.com/macros/s/.../exec`）を控える

シートは初回実行時に「案件」タブが自動で作られます。列は
`取得日時 / サイト / キーワード / タイトル / 報酬 / 締切 / URL / ステータス / メモ`。
ステータスはプルダウン（未確認・検討中・応募済・見送り）なので、そのまま応募管理に使えます。

### 3. Slack
1. <https://api.slack.com/apps> → Create New App → From scratch
2. **Incoming Webhooks** を ON → **Add New Webhook to Workspace** → 通知したいチャンネルを選ぶ
3. 発行された `https://hooks.slack.com/services/...` を控える

### 4. 設定ファイル
```bash
cp .env.example .env               # Slack / GAS の URL と TOKEN を記入
cp config.example.yml config.yml   # 検索キーワードなどを編集
```

### 5. 動作確認
```bash
python job_watcher.py --dry-run   # 取得結果を表示するだけ（送信しない）
python job_watcher.py             # 本番：スプシに追記 → Slack 通知
```
**初回実行はスプシに貯めるだけで Slack には通知しません**（既存の案件が全部「新着」扱いになるため）。
2回目以降、新しく出た案件だけが通知されます。

## 定期実行

### Windows（タスクスケジューラ）
1. 「タスク スケジューラ」→ **基本タスクの作成**
2. トリガー: 毎日 → 作成後にプロパティの「トリガー」で **繰り返し間隔: 1時間**（継続時間: 無期限）
3. 操作: プログラムの開始 → `job-watcher\run.bat` を指定
4. 「ユーザーがログオンしているときのみ実行する」にしておく（ブラウザを画面に出すため）

### Mac（cron）
```bash
crontab -e
# 毎時 7 分に実行
7 * * * * /path/to/claude-plactice/job-watcher/run.sh
```

ログは `run.log` に出ます。

## カスタマイズ
- `config.yml` の `keywords` / `exclude_words` / `max_pages` で検索条件を調整
- 安定して人間確認が出なくなったら `headless: true` にするとブラウザが画面に出なくなる
- 実行間隔は 1 時間程度を推奨（短すぎるとサイトに負荷をかけ、ブロックされやすくなる）

## 注意
- 各サイトの利用規約を確認のうえ、**個人利用の範囲で低頻度**に使ってください
- サイトのデザイン変更で取得できなくなることがあります
  - クラウドワークス: ページ内の `#vue-container` の JSON を読んでいる
  - ランサーズ: `/work/detail/数字` へのリンクを起点にカードの文字を読んでいる（報酬・締切は正規表現で抽出）
