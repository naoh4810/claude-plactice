---
name: skill-catalog-sync
description: >-
  Sync all local Claude Code skills into the user's Notion "スキルカタログ" database
  and the .claude/skills/README.md index. Use when the user asks to sync/refresh the
  skill catalog, "スキル一覧を最新にして", "カタログを同期", "スキルの棚卸し", or after
  adding several skills at once. Scans .claude/skills/*/SKILL.md (the source of truth),
  adds/updates rows, and NEVER deletes skill files. This is a superset of
  skill-catalog-add (which files one skill); use this to reconcile everything.
---

# スキルカタログ 同期（安全・非破壊）

Reconcile the Notion **スキルカタログ** table and the repo README index with the actual
skill files. **The SKILL.md files are the single source of truth; this skill only READS
them and never edits or deletes them.**

Direction (one-way, local → catalog):

```
.claude/skills/*/SKILL.md  ──read──▶  Notion catalog rows  +  .claude/skills/README.md
```

This has nothing to do with any cloud/online skill store — it is purely local files → the
user's own Notion table and README.

## Targets

- Skill files: `.claude/skills/*/SKILL.md` in this repo (repo scope). Optionally also
  `~/.claude/skills/*/SKILL.md` (personal scope) if the user asks to include those.
- Notion database: **スキルカタログ**, data source `0d3c765a-ea62-4b14-8355-7492d9d5c8e2`
  (if the id fails: `notion-search "スキルカタログ"` → `fetch` → read the `collection://` url).
  Schema/columns are documented in the `skill-catalog-add` skill.
- README index: `.claude/skills/README.md`.

## Steps

1. **List skills.** Glob `.claude/skills/*/SKILL.md`; for each, read the YAML frontmatter
   `name` (→ Slug) and `description`. Ignore `README.md` itself.
2. **Read the catalog.** `notion-query-data-sources` (SQL) over the data source to get all
   existing rows with their `Slug`.
3. **Diff by Slug and apply — additive only:**
   - **File present, no row** → create a row (map fields as in `skill-catalog-add`;
     `Location` = GitHub blob URL on the default branch).
   - **File present, row exists** → update the row only where fields changed
     (Description/Trigger/Category/Location). Keep the user's manual edits to Notes.
   - **Row exists, file missing** → **do NOT delete.** Report it and, only with the
     user's OK, set that row's `Status` to `廃止`. Never remove Notion rows automatically,
     and never touch any file.
4. **Refresh the README table.** Rewrite the table in `.claude/skills/README.md` from the
   scanned skills (one row each: name, `/slug`, description, scope, status). Leave the rest
   of the README as-is.
5. **Commit** the README change to the repo (and note that skill files themselves are
   unchanged). **Report a summary**: added / updated / flagged-as-廃止 (with counts and the
   catalog URL). Explicitly confirm that no skill files were modified or deleted.

## Safety rules (do not violate)

- Never edit or delete any `SKILL.md` file — read-only on the filesystem.
- Never auto-delete Notion rows. Missing-file rows are flagged `廃止` only after the user
  agrees; otherwise just reported.
- One row per Slug; prefer update over creating duplicates.
