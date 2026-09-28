# 営業シートMCP（sales-sheet）

交流会・マッチングアプリで出会った人の営業ログ（Google スプレッドシート）を、Claude から検索・追記・集計できるようにする MCP サーバーです。
自分の営業に毎日使いながら、交流会では「名刺を撮る → 10秒でシートに入る」をそのままデモとして見せる想定です。

## ツール

| ツール | 内容 |
|---|---|
| `list_sheets` | タブ名と列構成（営業 / 交流会 / マッチングアプリ） |
| `search_contacts` | 名前・事業名・メモのキーワード検索。タブや進捗でも絞り込める |
| `stalled_contacts` | 否決・NG 以外の人を段階の手前から順に並べ、次の一手を付けて返す。同じ人は1件にまとめる |
| `pipeline_summary` | 進捗の内訳と、交流会ごとの「面談以上に進んだ人数 / 会った人数」 |
| `add_contact` | 空き行に1人分を書き込む。同じ名前があれば止める。タブに無い列は `skipped` で返す |
| `update_contact` | 進捗・結果の更新と、メモへの追記（上書きしない） |

プロンプト（Claude Code では `/` から呼べます）:

- `followup_message` : 指定した人へのフォロー LINE の下書き（送信はしない）
- `business_card_intake` : 名刺の写真 → 内容確認 → 交流会タブに登録

名刺の文字は Claude が画像から読み取り、`add_contact` に渡します。OCR 用の外部サービスは使いません。

## 動かし方

### 1. サンプルモード（認証なしですぐ試す）

架空の人物データ（`sample_data.json`）で動きます。書き込みはメモリ上だけで、終了すると消えます。

```bash
pip install -r mcp/sales_sheet/requirements.txt
claude mcp add sales-sheet -- python mcp/sales_sheet/server.py
```

Claude Code で「フォローが止まっている人を出して」「交流会ごとの面談化率は？」などと聞いてみてください。

### 2. 本番モード（自分の営業シートにつなぐ）

1. Google Cloud でサービスアカウントを作り、JSON キーをダウンロードする（Google Sheets API を有効化）
2. 営業シートの「共有」で、サービスアカウントのメールアドレスを **編集者** として追加する
3. 環境変数を付けて登録する

```bash
claude mcp add sales-sheet \
  -e SALES_SHEET_ID=<スプレッドシートURLの /d/ と /edit の間> \
  -e GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json \
  -- python mcp/sales_sheet/server.py
```

JSON キーはリポジトリの外に置いてください（`.gitignore` でも除外していますが、念のため）。

## テスト

```bash
pip install pytest
python -m pytest mcp/sales_sheet/test_server.py
```

## シートの前提

- 1行目がヘッダー。列名が空の列は「メモ」として扱う（営業タブ・マッチングアプリタブのメモ欄）
- 「お客さま」が空の行は、番号だけ振られた空き行とみなして読み飛ばし、`add_contact` はそこから埋める
- 進捗の段階: 名刺配布のみ → 面談予定 / オフライン・オンラインアポ → 1回目商談・面談。否決・NG・失注は完了扱い
