---
name: notion-learning-backlog
description: >-
  Manage the user's Notion "学びたいことリスト" (learning backlog / wishlist) database.
  Use when the user wants to jot down something they want to learn, "〇〇を学びたい",
  "学習リストに追加", "学びたいことメモしといて", "あとで勉強したい", "学びたいことリスト見せて",
  "〇〇を学習中にして", "〇〇を完了にして", "add to my learning list", or to review /
  update that backlog. This tracks FUTURE things to learn (distinct from the
  デイリー学習ログ skill, which records what was already done).
---

# Notion 学びたいことリスト 管理

Capture, list, and update the user's **学びたいことリスト** — a backlog of things they
want to learn someday. The user is studying to become an AI コンサル, so this feeds the
"次に何をやるか" pipeline; completed items typically graduate into the **デイリー学習ログ**.

This skill covers three actions: **追加 (add)**, **一覧 (list)**, **更新 (update status)**.
Pick the one matching the user's request.

## Target (Notion)

- Database: **学びたいことリスト**
- URL: https://app.notion.com/p/fde71bc7dfc74d4083b0c469d15d6978
- Data source (parent for new pages): `b9ad0690-fd0a-4d90-8a12-95512e470eb9`
  - Pass as `parent: { type: "data_source_id", data_source_id: "b9ad0690-fd0a-4d90-8a12-95512e470eb9" }`
- If that id ever fails (workspace changed), find it with `notion-search "学びたいことリスト"`,
  `fetch` the database, and read the `collection://<id>` data-source URL from the result.

## Schema (property names are exact — Japanese)

| Property | Type | Allowed values / format |
|---|---|---|
| `学びたいこと` | title | the thing to learn, concise (required) |
| `カテゴリ` | select | `言語` / `ツール` / `概念` / `ビジネス` / `その他` |
| `優先度` | select | `高` / `中` / `低` |
| `ステータス` | select | `未着手` / `学習中` / `完了` |
| `メモ` | text | why learn it, resources, scope — short free text |
| `追加日` | created_time | **automatic — never set it** (Notion fills it) |

## Action A — 追加 (add one or more items)

Triggered by "〇〇を学びたい", "学習リストに追加", "メモしといて", etc.

1. **Extract the item(s).** One page per distinct thing to learn. Keep the title concise
   and specific (e.g. "TypeScript の型システム", not "プログラミング").
2. **Classify sensibly from context; do not interrogate the user.**
   - `カテゴリ`: programming language → `言語`; a tool/service/framework → `ツール`;
     a theory/idea/principle → `概念`; consulting/domain/soft skill → `ビジネス`; else `その他`.
   - `優先度`: default `中` unless the user signals urgency ("最優先" → `高`, "いつか" → `低`).
   - `ステータス`: `未着手` for a new backlog item (use `学習中` only if the user says
     they are already on it).
   - `メモ`: capture any stated reason / link / scope. Leave empty if none — do not invent.
3. **Create the page(s)** with `notion-create-pages`,
   `parent` = the data source above, `properties` = the mapped fields.
   Do **not** set `追加日` (created_time is automatic). Usually no `content` body is needed;
   add a short body only if the user dictated notes too long for the メモ field.
4. **Report** back: the title(s) added, their カテゴリ/優先度, and the page URL(s).

## Action B — 一覧 (list / review the backlog)

Triggered by "学びたいことリスト見せて", "何を学びたいんだっけ", "バックログ確認".

1. Query the data source with `notion-query-data-sources` (data source id above).
2. Unless the user asks otherwise, **exclude `完了`** and sort by 優先度 (高→低), showing
   `学びたいこと` / `カテゴリ` / `優先度` / `ステータス` (and `メモ` if useful).
3. Present as a compact Markdown table. If the backlog is empty, say so and offer to add one.

## Action C — 更新 (change status / priority)

Triggered by "〇〇を学習中にして", "〇〇終わった/完了にして", "優先度上げて".

1. Find the page: query the data source filtering on `学びたいこと` matching the user's words
   (confirm which row if ambiguous — show the candidates, don't guess).
2. Update with `notion-update-page`, setting only the changed property
   (`ステータス` → `学習中` / `完了`, or `優先度`).
3. **When marking `完了`**, offer in one line to also log it to the デイリー学習ログ
   (via the `notion-daily-log` skill) — this is how finished backlog items become study records.
   Only do it if the user agrees.

## Notes

- Keep titles specific and deduplicate: before adding, a quick query avoids obvious dupes.
- This is the *future* list; the **デイリー学習ログ** is the *past* record. Route
  "やったこと" to that skill, "学びたいこと" to this one.
- One clear report back with URLs beats a long confirmation dialog — act, then summarize.
