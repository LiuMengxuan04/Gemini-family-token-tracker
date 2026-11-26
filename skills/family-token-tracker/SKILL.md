---
name: family-token-tracker
description: >-
  Manage family-shared Antigravity token usage tracking and Notion family
  dashboard sync. Supports zero-LLM background auto-sync on every turn via
  Notion REST API, or Notion MCP sync by dashboard link. Use when the user asks
  to view token usage, bind a shared Notion dashboard link or integration token,
  initialize a Notion token database, sync local usage to Notion, view a
  family-wide usage breakdown from Notion, or optionally configure member name
  or quota limits.
---

# Family Token Tracker / 家庭 Token 统计

This plugin automatically records token consumption (`input_tokens`,
`cache_read_tokens`, `output_tokens`, `model`) from every conversation turn via
`Stop` and `PreInvocation` lifecycle hooks into a local SQLite database.

- **Username / 用户名**: Automatically defaults to the current logged-in system
  account (`getpass.getuser()`). Do not ask the user to configure a username
  unless they explicitly ask to change it.
- **Quotas / 额度**: Disabled by default (`daily_limit: 0`, `monthly_limit: 0`,
  no upper limit). Only records and syncs unless the user explicitly sets a
  limit.
- **Default Auto-Sync (Zero Extra LLM Calls) / 默认自动同步（零模型消耗）**:
  - `auto_sync` is `true` by default.
  - Once `notion.database_url` and `notion.api_token` (a free Notion Internal
    Integration Token connected to the database) are configured, every `Stop`
    hook automatically spawns a detached background process
    (`sync-notion-direct --quiet`) that calls the Notion REST API directly —
    **zero extra LLM calls and zero chat latency**.
  - If the user has only bound the Notion dashboard link (without an integration
    token), they can sync on demand via the installed `notion_notion` MCP plugin
    or set up a daily `/schedule` task.

## Script Path

Always run the bundled tracker CLI at:

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py <subcommand>
```

> All data is stored under
> `~/.gemini/antigravity/plugin_data/family-token-tracker/` (`config.json`,
> `usage.db`, `last_sync.json`), or `FAMILY_TOKEN_TRACKER_DATA` if overridden.

## Sharing & Permissions Guide / 如何确保家庭成员「有权限访问和编辑」

Whenever creating a new Notion dashboard or helping the user share it, always
include this guidance (in the user's language, or bilingual):

> **如何确保家庭成员「有权限访问和编辑」**
>
> 你的 Notion 目前是个人工作区（无 Teamspace），且 Notion 官方 API / MCP 出于安全策略不支持通过接口直接修改页面的共享权限，因此新建的看板默认是 Private（仅自己可见）。
>
> 请打开上面的链接，点击右上角 **Share（共享）** 完成一次授权（二选一）：
>
> - **家庭成员使用各自的 Notion 账号（推荐）**：在 **Share → Invite** 中输入家庭成员的 Notion 邮箱，右侧权限选择 **Can edit（可编辑）** 并发送邀请。对方接受后，即可通过 Antigravity 的 Notion 插件读写这个看板。
> - **若开启零模型消耗自动同步（Integration Token）**：在看板右上角 `··· → Connections（连接）` 中关联同一个 Notion Integration，其他家庭成员配置相同的看板链接 + Token 即可每次对话结束后静默自动同步。

## Workflows

### 1. Bind an Existing Notion Dashboard by Link (and Optional Zero-LLM Auto-Sync Token)

When `notion.database_url` / `notion.data_source_id` is empty and the user wants
to sync (or sends a Notion URL):

1. If the user has not provided a link yet, reply briefly (in the user's
   language, or bilingual if unclear):
   > Please send your shared family Notion dashboard link (and optionally your Notion Integration Token for zero-LLM auto-sync after every turn) and I will bind and sync automatically. (If you don't have a dashboard yet, let me know and I can create one for you.)
   > 请直接把家庭共享的 Notion 看板链接发给我（如需每次对话结束后零模型消耗自动同步，也可一并附上 Notion Integration Token），我会自动绑定并同步。（如果还没有创建看板，也可以告诉我帮你新建一个。）
2. Once the user sends the Notion database URL (and optional `ntn_...` / `secret_...` token):
   - Call `notion_notion` tool `notion-fetch` with the URL (if available) to
     retrieve its `collection://...` data source URL.
   - Save locally (the script automatically extracts `database_id` from the URL):

     ```bash
     python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
       --notion-db-url "<database_url>" \
       --notion-ds-id "<collection://...>" \
       --auto-sync true
     ```

     If the user also provided an Integration Token, include `--notion-token "<token>"`.
   - Immediately run **Workflow 3 (Sync Local Usage to Notion)**.

### 2. Initialize a New Shared Notion Family Dashboard (First-Time Admin)

1. Check if `notion.data_source_id` is already set via `config`. If already set,
   confirm with the user before creating a new one.
2. Call `notion_notion` tool `notion-create-database` with:
   - `title`: `"Family Antigravity Token Tracker / 家庭 Token 消耗看板"`
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
     --notion-ds-id "<collection://...>" \
     --auto-sync true
   ```

4. Give the user the Notion database URL AND display the **Sharing & Permissions
   Guide** above so family members can access and edit it.

### 3. Sync Local Usage to Notion

1. First run `backfill` (fast & idempotent) and check `config`:

   ```bash
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py backfill && \
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config
   ```

2. **If `notion.api_token` and `notion.database_id` are set (Zero-LLM Direct Sync)**:
   Run:

   ```bash
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py sync-notion-direct
   ```

   Report created/updated counts (and any errors, e.g., remind the user to
   connect the Integration to the database via `··· → Connections` if HTTP 404).
3. **Otherwise (Notion MCP Fallback)**:
   - If `notion.data_source_id` is empty, follow Workflow 1.
   - Run `tracker.py notion-pending`, create/update pages via `notion-create-pages` / `notion-update-page`, and mark synced via `tracker.py notion-mark-synced`.
   - Remind the user they can optionally configure a free Notion Integration Token (`--notion-token`) to enable zero-LLM automatic sync after every turn, or use `/schedule` for daily scheduled sync.

### 4. View Local Usage Report

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py backfill && \
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py report --period month
```

Present a clear Markdown table showing Member, Device, Auto-Sync status,
Billable Tokens (`Input + Output`), Cache Read Tokens, Calls, and Model
breakdown.

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
