---
name: family-token-tracker
description: >-
  Manage family-shared Antigravity token usage tracking and Notion family
  dashboard sync. Use when the user asks to view token usage, bind a shared
  Notion dashboard link, initialize a Notion token database, sync local usage
  to Notion, view a family-wide usage breakdown from Notion, or optionally
  configure member name or quota limits.
---

# Family Token Tracker

This plugin automatically records token consumption (`input_tokens`,
`cache_read_tokens`, `output_tokens`, `model`) from every conversation turn via
`Stop` and `PreInvocation` lifecycle hooks into a local SQLite database, and
syncs daily summaries to a shared Notion database using the installed
`notion_notion` MCP server.

- **Username**: Automatically defaults to the current logged-in system account
  (`getpass.getuser()`). Do not ask the user to configure a username unless they
  explicitly ask to change it.
- **Quotas**: Disabled by default (`daily_limit: 0`, `monthly_limit: 0`, no
  upper limit). Only records and syncs unless the user explicitly sets a limit.
- **Notion Onboarding**: If no Notion dashboard is bound yet, simply guide the
  user to paste/send their shared Notion dashboard link (or offer to create a
  new Notion database if they are setting it up for the first time).

## Script Path

Always run the bundled tracker CLI at:

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py <subcommand>
```

> All data is stored under
> `~/.gemini/antigravity/plugin_data/family-token-tracker/` (`config.json` and
> `usage.db`), or `FAMILY_TOKEN_TRACKER_DATA` if overridden.

## Workflows

### 1. Bind an Existing Notion Dashboard by Link (Recommended for Family Members)

When `notion.data_source_id` is empty and the user wants to sync (or sends a
Notion URL):

1. If the user has not provided a link yet, reply briefly (in the user's language, or bilingual if unclear):
   > Please send your shared family Notion dashboard link and I will bind it and sync your token usage automatically. (If you don't have one yet, let me know and I can create one for you.)
   > 请直接把家庭共享的 Notion 看板链接发给我，我会自动绑定并同步你的 Token 消耗。（如果还没有创建看板，也可以告诉我帮你新建一个。）
2. Once the user sends the Notion database URL:
   - Call `notion_notion` tool `notion-fetch` with the URL to retrieve the
     database metadata and its `collection://...` data source URL.
   - Save both values locally:

     ```bash
     python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
       --notion-db-url "<database_url>" \
       --notion-ds-id "<collection://...>"
     ```
   - Immediately proceed to **Workflow 3 (Sync Local Usage to Notion)**.

### 2. Initialize a New Shared Notion Family Dashboard (First-Time Admin)

1. Check if `notion.data_source_id` is already set via `config`. If already set,
   confirm with the user before creating a new one.
2. Call `notion_notion` tool `notion-create-database` with:
   - `title`: `"家庭 Antigravity Token 消耗看板"`
   - `schema`:
     ```sql
     CREATE TABLE (
       "Record" TITLE,
       "Date" DATE,
       "Member" SELECT,
       "Device" RICH_TEXT,
       "Model" SELECT,
       "Input Tokens" NUMBER,
       "Cache Read Tokens" NUMBER,
       "Output Tokens" NUMBER,
       "Billable Tokens" NUMBER,
       "Total Tokens" NUMBER,
       "Calls" NUMBER,
       "Sync Key" RICH_TEXT
     )
     ```
3. Extract the database URL and `collection://...` data source ID, then save:

   ```bash
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
     --notion-db-url "<database_url>" \
     --notion-ds-id "<collection://...>"
   ```

4. Give the user the Notion database URL and tell them other family members only
   need to paste this link in their own Antigravity chat to bind and sync.

### 3. Sync Local Usage to Notion

1. First check `config`. If `notion.data_source_id` is empty, follow Workflow 1
   (ask the user to send the Notion dashboard link).
2. Run `backfill` (fast & idempotent) and get pending daily rows:

   ```bash
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py backfill && \
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py notion-pending
   ```

3. If `pending_count` is `0`, inform the user everything is already up to date.
4. For items with `"action": "create"`:
   - Optionally check if a row with the same `"Sync Key"` already exists in
     Notion using `notion-query-data-sources` to avoid duplicates.
   - Call `notion-create-pages` with `parent: {"data_source_id": "<data_source_id>"}`
     and each item's `properties`.
5. For items with `"action": "update"`:
   - Call `notion-update-page` on `item.notion_page_id` with updated properties.
6. After Notion confirms creation/updates, record the synced state locally:

   ```bash
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py notion-mark-synced \
     --items-json '[{"sync_key":"...","notion_page_id":"...","input_tokens":123,"cache_read_tokens":456,"output_tokens":78,"calls":2}]'
   ```

### 4. View Local Usage Report

Import any past transcripts and show local report (`today`, `week`, `month`, or
`all`):

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py backfill && \
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py report --period month
```

Present a clear Markdown table showing Member, Device, Billable Tokens
(`Input + Output`), Cache Read Tokens, Calls, and Model breakdown.

### 5. View Family-Wide Usage Summary (From Notion)

1. Read `notion.data_source_id` from `config` (if empty, ask the user to send
   the Notion dashboard link per Workflow 1).
2. Fetch schema with `notion-fetch` if needed, then query with
   `notion-query-data-sources` (`mode: "rows"` or `mode: "sql"`).
3. Aggregate by `Member` for Today and Current Month, and present a clear
   Markdown table comparing each family member's Billable Tokens, Cache Read
   Tokens, Calls, and share of total family usage.

### 6. Optional: Customize Member Name or Quota Limits

Only needed if the user explicitly asks to override the default OS username or
turn on optional gentle quota reminders (`0` = unlimited):

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
  --member "<name>" \
  --daily-limit 0 \
  --monthly-limit 0
```
