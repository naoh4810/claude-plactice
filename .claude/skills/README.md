# スキル一覧（.claude/skills）

このフォルダには、リポジトリ **naoh4810/claude-plactice** で使える Claude Code スキルが入っています。
各スキルは `<slug>/SKILL.md`（YAMLフロントマター + Markdown手順）という1ファイル構成です。
会話で該当の言い回しをするか、`/<slug>` と入力すると呼び出せます。

> この索引は手で更新してもよいですが、`/skill-catalog-sync` を実行すると
> `.claude/skills/*/SKILL.md` を読み取ってこの表と Notion カタログを自動で最新化します（**ファイルは消しません**）。

## 一覧

| スキル | 呼び出し | 説明 | Scope | Status |
|---|---|---|---|---|
| notion-daily-log | `/notion-daily-log` | その日の作業を Notion「デイリー学習ログ」に1件追加する | リポジトリ | 有効 |
| skill-catalog-add | `/skill-catalog-add` | 作ったスキルを Notion「スキルカタログ」に1行登録する | リポジトリ | 有効 |
| skill-catalog-sync | `/skill-catalog-sync` | 全スキルをスキャンして、この索引と Notion カタログを差分更新する（削除はしない） | リポジトリ | 有効 |

## 保存場所

- リポジトリ: `naoh4810/claude-plactice`
- パス: `.claude/skills/<slug>/SKILL.md`
- デフォルトブランチ: `claude/daily-ai-article-generation-7dcya2`（= どのセッションでも読み込まれる場所）
- Notion「スキルカタログ」: https://app.notion.com/p/d1de131901424580ab340e4554e60e9e

## スキルの置き場所の種類

- **リポジトリスキル**: `.claude/skills/`（このフォルダ）。そのリポジトリを開いたセッションで有効。
- **個人スキル**: `~/.claude/skills/`。全セッションで有効（このリポジトリには含まれない）。

## 参考（先人の例）

- Anthropic 公式 skills リポジトリ: https://github.com/anthropics/skills
- Agent Skills ドキュメント: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
