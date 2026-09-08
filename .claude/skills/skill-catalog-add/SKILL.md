---
name: skill-catalog-add
description: >-
  Register a Claude Code skill into the user's Notion "スキルカタログ" database.
  Use when the user asks to add/record a skill to the catalog, "スキルをカタログに追加",
  "スキル一覧に登録して", "作ったスキルを表に入れて", or right after creating a new
  SKILL.md and wanting it tracked. Reads a skill's SKILL.md frontmatter and files a
  row (name, slug, category, trigger, scope, location, status) in the skill catalog.
---

# スキルカタログへ登録

Add one row to the user's Notion **スキルカタログ** database for a Claude Code skill —
typically a skill just created under `.claude/skills/` (repo) or `~/.claude/skills/`
(personal). Keeps a living index of the user's self-made skills.

## Target (Notion)

- Database: **スキルカタログ**
- Data source (parent for new rows): `0d3c765a-ea62-4b14-8355-7492d9d5c8e2`
  - Pass as `parent: { type: "data_source_id", data_source_id: "0d3c765a-ea62-4b14-8355-7492d9d5c8e2" }`
- If that id ever fails, find it with `notion-search "スキルカタログ"` → `fetch` the
  database → read the `collection://<id>` data-source URL.

## Schema (exact property names)

| Property | Type | Notes |
|---|---|---|
| `Name` | title | Human-readable skill name (e.g. "Notion デイリーログ追加") |
| `Slug` | text | frontmatter `name` (e.g. `notion-daily-log`) — invoked as `/slug` |
| `Category` | select | `Notion` / `Gmail` / `リサーチ` / `開発` / `自動化` / `その他` |
| `Description` | text | one line: what it does |
| `Trigger` | text | trigger phrases / `/slug` |
| `Scope` | select | `リポジトリ` (`.claude/skills`) or `個人` (`~/.claude/skills`) |
| `Location` | url | GitHub blob URL of the SKILL.md (default branch), or its path |
| `Status` | select | `有効` / `実験中` / `廃止` |
| `date:Created:start` | date | `YYYY-MM-DD` (today unless told otherwise) |
| `Notes` | text | optional |

## Steps

1. **Identify the skill.** Use the SKILL.md the user points to (or the one just
   created). Read its YAML frontmatter for `name` (→ `Slug`) and `description`.
2. **Derive fields:**
   - `Name`: a readable label (from the skill's purpose; not just the slug).
   - `Description` / `Trigger`: summarize from the frontmatter `description`.
   - `Category`: pick the best fit from the select options above.
   - `Scope`: `リポジトリ` if under a repo's `.claude/skills/`, `個人` if under `~/.claude/skills/`.
   - `Location`: prefer the GitHub blob URL on the repo's **default branch**
     (`https://github.com/<owner>/<repo>/blob/<default-branch>/.claude/skills/<slug>/SKILL.md`);
     otherwise the local path.
   - `Status`: `有効` unless the user says it's experimental.
3. **Avoid duplicates.** Query the catalog for an existing row with the same `Slug`
   (`notion-query-data-sources`, SQL `WHERE "Slug" = '<slug>'`). If it exists, update
   that row instead of adding a second.
4. **Create the row** with `notion-create-pages` into the data source above, mapping the
   fields. Leave the page body empty unless the user wants extra detail.
5. **Report** the catalog row URL (and the database URL) back to the user.

## Notes

- This pairs with skill creation: after writing a new `.claude/skills/<slug>/SKILL.md`,
  offer to register it here so the catalog stays complete.
- Keep one row per skill; prefer updating over duplicating.
