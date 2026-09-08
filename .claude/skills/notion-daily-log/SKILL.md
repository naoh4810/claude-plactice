---
name: notion-daily-log
description: >-
  Add an entry to the user's Notion "デイリー学習ログ" (daily learning log) database.
  Use when the user asks to log today's work/study to Notion, "デイリーログに追加",
  "今日の学習を記録", "学習ログをつけて", "log today to Notion", or wants the day's
  activity written into their daily learning log. Records what was done in this
  session (or what the user describes) as a dated, structured log entry.
---

# Notion デイリー学習ログ 追加

Create one entry in the user's Notion **デイリー学習ログ** database for a given day
(default: today). The user is studying to become an AI コンサル; this log tracks
daily study/output toward that goal.

## Target (Notion)

- Database: **デイリー学習ログ**
- Data source (parent for new pages): `33bdbb42-5bd6-8099-a908-000bfab794e7`
  - Pass as `parent: { type: "data_source_id", data_source_id: "33bdbb42-5bd6-8099-a908-000bfab794e7" }`
- If that id ever fails (workspace changed), find it with `notion-search "デイリー学習ログ"`,
  `fetch` the database, and read the `collection://<id>` data-source URL from the result.

## Schema (property names are exact — Japanese)

| Property | Type | Allowed values / format |
|---|---|---|
| `タイトル` | title | short summary of the day (required) |
| `date:学習日:start` | date | `YYYY-MM-DD` (the log date; use today unless told otherwise) |
| `学習タイプ` | select | `インプット` / `アウトプット` / `復習` |
| `実践度` | select | `理解のみ` / `手を動かした` / `応用できた` |
| `完了` | checkbox | `__YES__` (done) or `__NO__` |
| `学習内容(概要)` | text | 1–3 sentence summary |

## Steps

1. **Determine the date.** Default to today (from the session's current date). If the
   user names another day ("昨日の分", a specific date), use that for `date:学習日:start`.
2. **Gather the content.** Prefer summarizing what was actually accomplished — from
   this session's work if it produced something concrete, otherwise ask the user in one
   line: 「今日やったこと・学んだことを一言で？」 Keep it truthful; do not invent activity.
3. **Classify:**
   - `学習タイプ`: reading/watching → `インプット`; building/doing/shipping → `アウトプット`; review → `復習`.
   - `実践度`: `理解のみ` / `手を動かした` / `応用できた` (pick the highest that honestly applies).
   - `完了`: `__YES__` if the day's task was finished, else `__NO__`.
4. **Write a short body** (Notion Markdown) with a few sections when there is enough to
   say — e.g. `## やったこと`, `## 気づき・学び`, `## AIコンサルへの活用アイデア`,
   `## 次への課題` (with `- [ ]` items). Skip sections that would be empty.
5. **Create the page** with `notion-create-pages`, `parent` = the data source above,
   `properties` = the mapped fields, `content` = the body. Do not put the title inside
   `content` (it comes from the `タイトル` property).
6. **Report** the created page URL back to the user.

## Notes

- Titles should be specific to the day's real work, not generic ("学習した" is too vague).
- One entry per call unless the user asks for several days at once.
- This log feeds the "月曜ブリーフィング" dashboard's 学習セクション, so concrete,
  outcome-focused entries make the weekly "次の目標" suggestions sharper.
