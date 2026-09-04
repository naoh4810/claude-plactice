# 毎日1記事：AI記事の自動生成環境

自分の**学習ログ（実体験）を素材に、Claude が毎日1本ブログ記事の下書きを生成**する仕組みです。
GitHub Actions が毎朝走り、生成した記事を `articles/` にコミットします。

「一次情報（自分が実際に手を動かした記録）を核にする」ことを最優先の設計にしています。
一般論の寄せ集めではなく、あなたにしか書けない記事の下書きを量産するのが狙いです。

---

## 全体像

```
学習ログ（Notion） ──┐
                     ├─→ generate_article.py ─→ Claude ─→ articles/YYYY-MM-DD-xxx.md
config/topics.yml ───┘        （毎朝 GitHub Actions が実行）
```

- **素材の優先順位**: Notion「デイリー学習ログ」の未使用の学び → 無ければ `config/topics.yml` のキュー
- **重複防止**: 一度記事にしたトピックは `state/used.json` に記録して二度使わない
- **出力**: `articles/` に frontmatter 付き Markdown（`status: draft`）。公開前に自分で目を通す前提

---

## セットアップ

### 1. APIキーを用意
- **Anthropic APIキー**（必須）: <https://console.anthropic.com>
- **Notion連携**（任意）: <https://www.notion.so/my-integrations> でインテグレーションを作成し、
  トークンを取得。さらに「デイリー学習ログ」DBを開き `···` →「コネクトを追加」で接続する。

### 2. ローカルで試す

```bash
pip install -r requirements.txt
cp .env.example .env        # .env を編集してキーを記入
set -a; source .env; set +a # 環境変数として読み込み
python scripts/generate_article.py
```

`articles/` に記事ができれば成功です。

### 3. 毎日自動化（GitHub Actions）
リポジトリの **Settings → Secrets and variables → Actions** で登録します。

| 種別 | 名前 | 内容 |
|------|------|------|
| Secret | `ANTHROPIC_API_KEY` | Anthropic APIキー（必須） |
| Secret | `NOTION_API_KEY` | Notionトークン（任意） |
| Secret | `NOTION_DAILY_LOG_DATABASE_ID` | 学習ログDBのID（任意） |
| Variable | `ARTICLE_MODEL` | 例: `claude-opus-5`（任意） |
| Variable | `ENABLE_WEB_SEARCH` | `true`/`false`（任意） |

登録後、Actionsタブの **Daily AI Article → Run workflow** で即テストできます。
以降は毎朝 07:00 JST（cron: `0 22 * * *` UTC）に自動実行されます。

---

## カスタマイズ

- **文体・読者・構成** → `prompts/system_prompt.md` と `config/topics.yml` の `settings:`
- **Notionを使わない** → `NOTION_*` を未設定にすれば `config/topics.yml` の `queue:` を消化
- **モデル変更** → `ARTICLE_MODEL`（デフォルト `claude-opus-5`）
- **最新情報で補強** → `ENABLE_WEB_SEARCH=true`（Web検索ツールを最大3回まで使用）

---

## ディレクトリ

```
.
├─ .github/workflows/daily-article.yml  日次実行のワークフロー
├─ scripts/
│   ├─ generate_article.py              メイン：トピック選択→生成→保存
│   └─ notion_source.py                 Notion学習ログ取得（任意）
├─ prompts/system_prompt.md             記事のペルソナ・構成・文体
├─ config/topics.yml                    生成設定＋フォールバックのトピック
├─ articles/                            生成された記事（下書き）
├─ state/used.json                      記事化済みトピックの記録
└─ docs/学習ロードマップ.md             記事化しやすい今後の学習テーマ
```

## 注意
- 生成物は**下書き（`status: draft`）**です。公開前に事実確認と推敲を。
- 素材に無い数字・固有名詞は書かないようプロンプトで制約していますが、最終確認は人間が行ってください。
