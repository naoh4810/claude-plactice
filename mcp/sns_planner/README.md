# SNS一括投稿MCP（sns-planner）

1つのネタから X・Instagram・Threads・TikTok・YouTube ショート向けの文面を作り分け、投稿カレンダーと週次レポートで回すための MCP サーバーです。
「週に何本か定期的に出したいが、SNS ごとに文字数も書き方も違って手が回らない」という地域サービス・お店・クリエイター向けのデモとして作りました。

役割分担:

- **Claude** : ネタ出し、SNS ごとの書き分け、振り返りのまとめ
- **このサーバー** : 各 SNS のルールのチェック、予定と週の目標の管理、反応の数字の集計
- **人** : 投稿そのもの（各 SNS のアプリや予約投稿ツールで）、インサイトの数字の入力

各 SNS の投稿 API は使いません（Instagram・TikTok の投稿 API はアプリ審査やビジネスアカウントが必要で、デモの前提が重くなるため）。

## ツール

| ツール | 内容 |
|---|---|
| `platform_rules` | SNS ごとの文字数の上限・ハッシュタグ・画像の要否・URL がタップできるか、と書き方のコツ |
| `set_weekly_targets` | 「週に何本出すか」の目標（SNS ごと） |
| `add_idea` / `list_ideas` | ネタ帳。どのネタがどの SNS で下書き・予定・投稿済みかも見える |
| `check_text` | 文面がルールに収まっているか（X は日本語1文字=2、URL=23 で数える） |
| `save_draft` / `edit_draft` | SNS ごとの下書き。ルール違反があれば保存せずに直す点を返す |
| `schedule_post` | 投稿日時を入れる。同じ SNS に12時間以内の予定があれば警告 |
| `calendar` | 期間の予定・投稿と、週ごとの目標に対する不足。日時未定の下書きも |
| `mark_posted` / `record_metrics` | 投稿したことと、表示回数・いいね・コメント・シェア・保存・クリックの記録 |
| `performance_report` | SNS ごとの反応率、反応が良かった投稿、目標に届かなかった週 |
| `export_calendar` | 予定を CSV（Excel で開ける）に書き出し。チーム共有や予約投稿ツールへの転記用 |

プロンプト:

- `plan_week` : 来週の不足を確認 → ネタを選ぶ（無ければ聞く）→ SNS ごとに書き分け → 保存 → 日時を入れる → 1週間分を表で確認
- `weekly_review` : 直近2週間の数字 → 未記録の数字を聞いて記録 → 良かった投稿とその理由 → 来週やること

## チェックするルール（2026年時点の目安）

| SNS | 文字数 | ハッシュタグ | 画像・動画 | 本文の URL |
|---|---|---|---|---|
| X | 280（日本語は1文字=2なので約140字、URL は23） | 2個程度まで推奨 | 任意 | タップできる |
| Instagram | 2,200 | 30個まで（5個程度推奨） | 必須 | タップできない |
| Threads | 500 | 1個まで | 任意 | タップできる |
| TikTok | 2,200 | 5個程度推奨 | 必須 | タップできない |
| YouTube ショート（タイトル） | 100 | 3個程度推奨 | 必須 | タップできる |

各 SNS の仕様は変わるので、合わなくなったら `rules.py` を直してください。迷う値は厳しい側に寄せています。

## デモの流れ（Claude Code で話しかける例）

架空の「サンプル町 観光案内」（`sample_planner.json`、ネタ5件・2週間分の投稿と数字）で試せます。

1. 「この2週間どうだった？」→ Instagram の反応率 12.2% が最も高く、X はクリックが多い。紅葉スポットのカルーセル投稿が一番反応が良い
2. 「来週の投稿を計画して」→ 週の目標に対する不足を確認 → 朝市のネタを X・Instagram・Threads 向けに書き分け → 日時を入れる
3. 「来週の予定を Excel で」→ CSV を書き出し

## 動かし方

```bash
pip install -r mcp/sns_planner/requirements.txt

# サンプルで試す（変更はメモリ上だけ）
claude mcp add sns-planner -- python mcp/sns_planner/server.py

# 自分のアカウント用に使う（ネタ・予定・数字をファイルに保存）
claude mcp add sns-planner -e SNS_DATA_PATH=/path/to/sns_planner.json -- python mcp/sns_planner/server.py
```

| 環境変数 | 内容 | 省略時 |
|---|---|---|
| `SNS_DATA_PATH` | ネタ・予定・数字を保存する JSON ファイル（無ければ作る） | サンプルをメモリ上で使う |
| `SNS_EXPORT_DIR` | `export_calendar` の保存先 | `reports/` |

最初に「週に Instagram 2本、X 3本を目標にしたい」と話しかけると、`set_weekly_targets` で目標が入ります。

## テスト

```bash
pip install pytest
python -m pytest mcp/sns_planner/test_server.py
```

`mcp/` 配下の各サーバーはモジュール名（`server` など）が同じなので、テストはサーバーごとに分けて実行してください。

## 制限

- 投稿は自動では行わない（予約投稿ツールや各 SNS のアプリで行い、`mark_posted` で記録する）
- 反応の数字は手入力（各 SNS のインサイト画面の値を伝えて `record_metrics` で記録する）
- 1つのファイルを複数人で同時に書き換える使い方は想定していない
