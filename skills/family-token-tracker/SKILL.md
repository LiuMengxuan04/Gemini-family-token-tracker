---
name: family-token-tracker
description: >-
  Manage family-shared Antigravity token usage tracking and Notion family
  dashboard sync. Tracks total accumulated tokens per member starting from a
  configurable `since` date (default: plugin installation time), displays a
  visual progress bar, and supports zero-LLM background auto-sync on every turn
  via Notion REST API.
---

# Family Token Tracker / 家庭 Token 统计

This plugin automatically records token consumption (`input_tokens`,
`cache_read_tokens`, `output_tokens`, `model`) from every conversation turn via
`Stop` and `PreInvocation` lifecycle hooks into a local SQLite database, and
syncs **one clean summary row per member** (with a visual progress bar) to a
shared Notion dashboard.

- **Username / 用户名**: Automatically defaults to the current logged-in system
  account (`getpass.getuser()`).
- **Start Date (`since`) & Cycle (`cycle`) / 起始时间与周期**: Automatically
  defaults to the exact moment the plugin is first installed/initialized.
- **One-Person Reset, Whole-Family Auto-Follow / 一人重置，全家自动切新周期**:
  - When anyone runs `tracker.py reset` (or tells the agent "额度重置了，开个新周期"),
    it creates a brand-new `Cycle` in Notion starting from `now` **without
    overwriting previous cycles** (old cycles remain archived as separate
    grouped sections in the same Notion database).
  - Every other family member's device automatically checks Notion's latest
    `Cycle` before syncing (`auto_follow_reset: true`) and switches to the new
    cycle & `since` timestamp automatically — zero manual reconfiguration!
- **Quotas / 额度**: Disabled by default (`daily_limit: 0`, `monthly_limit: 0`,
  no upper limit). When unlimited, `Progress` shows a clean visual gauge bar
  (`🟦🟦🟦⬜⬜⬜⬜⬜⬜⬜ 3.62M (219 calls)`); when a limit is set, it shows a
  percentage progress bar (`🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜ 62.5%`).
- **Default Auto-Sync (Zero Extra LLM Calls) / 默认自动同步（零模型消耗）**:
  - `auto_sync` is `true` by default.
  - Once `notion.database_url` and `notion.api_token` are configured, every
    `Stop` hook spawns a detached background process (`sync-notion-direct
    --quiet`) that updates the member's single summary row in Notion directly —
    **zero extra LLM calls and zero chat latency**.

## Script Path

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py <subcommand>
```

> Data directory: `~/.gemini/antigravity/plugin_data/family-token-tracker/`
> (`config.json`, `usage.db`, `last_sync.json`).

## Sharing & Permissions Guide / 如何确保家庭成员「有权限访问和编辑」

Whenever creating a new Notion dashboard or helping the user share it, always
include this guidance:

> **如何确保家庭成员「有权限访问和编辑」**
>
> 你的 Notion 目前是个人工作区（无 Teamspace），且 Notion 官方 API / MCP 出于安全策略不支持通过接口直接修改页面的共享权限，因此新建的看板默认是 Private（仅自己可见）。
>
> 请打开上面的链接，点击右上角 **Share（共享）** 完成一次授权（二选一）：
>
> - **家庭成员使用各自的 Notion 账号（推荐）**：在 **Share → Invite** 中输入家庭成员的 Notion 邮箱，右侧权限选择 **Can edit（可编辑）** 并发送邀请。对方接受后，即可通过 Antigravity 的 Notion 插件读写这个看板。
> - **若开启零模型消耗自动同步（Integration Token）**：在看板右上角 `··· → Connections（连接）` 中关联同一个 Notion Integration，其他家庭成员配置相同的看板链接 + Token 即可每次对话结束后静默自动更新自己的汇总行。

## Workflows

### 1. Bind an Existing Notion Dashboard by Link (and Optional Zero-LLM Auto-Sync Token)

1. If the user has not provided a link yet, reply briefly (in the user's
   language, or bilingual if unclear):
   > Please send your shared family Notion dashboard link (and optionally your Notion Integration Token for zero-LLM auto-sync after every turn) and I will bind and sync automatically. (If you don't have a dashboard yet, let me know and I can create one for you.)
   > 请直接把家庭共享的 Notion 看板链接发给我（如需每次对话结束后零模型消耗自动同步，也可一并附上 Notion Integration Token），我会自动绑定并同步。（如果还没有创建看板，也可以告诉我帮你新建一个。）
2. Save locally:

   ```bash
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
     --notion-db-url "<database_url>" \
     --notion-token "<optional_token>" \
     --auto-sync true
   ```

3. Run **Workflow 3** to sync the member's summary row.

### 2. Initialize a New Shared Notion Family Dashboard (First-Time Admin)

Call `notion_notion` tool `notion-create-database` with:
- `title`: `"Family Antigravity Token Tracker / 家庭 Token 消耗看板"`
- `schema`:
  ```sql
  CREATE TABLE (
    "Record" TITLE,
    "Progress" RICH_TEXT,
    "Cycle" SELECT,
    "Billable Tokens" NUMBER,
    "Calls" NUMBER,
    "Since" DATE,
    "Last Updated" DATE,
    "Member" SELECT,
    "Device" RICH_TEXT,
    "Input Tokens" NUMBER,
    "Output Tokens" NUMBER,
    "Cache Read Tokens" NUMBER,
    "Total Tokens" NUMBER,
    "Sync Key" RICH_TEXT,
    "Device Breakdown" RICH_TEXT
  )
  ```

After creating the database, optionally call `notion-update-view` on its default
view so `"Progress"` is placed immediately after `"Record"`, rows are grouped
by `"Cycle"` (newest cycle as its own sub-table!), and `"Sync Key"` / `"Device Breakdown"` are hidden:
`GROUP BY "Cycle"; SHOW "Record", "Progress", "Billable Tokens", "Calls", "Since", "Last Updated", "Device", "Input Tokens", "Output Tokens", "Cache Read Tokens", "Total Tokens", "Member"; HIDE "Sync Key", "Device Breakdown"; WIDTH "Progress" 320; SORT BY "Billable Tokens" DESC`.

Save the URL/ID via `tracker.py config` and display the **Sharing & Permissions
Guide** above.

### 3. Sync Member Summary Row to Notion

1. If `notion.api_token` and `notion.database_id` are set (Zero-LLM Direct Sync):

   ```bash
   python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py sync-notion-direct --force
   ```

2. Otherwise (Notion MCP fallback): run `tracker.py notion-pending`, create or
   update the single summary page via `notion-create-pages` /
   `notion-update-page`, and call `tracker.py notion-mark-synced`.

### 4. Start a New Cycle on Quota Reset (Without Overwriting History)

When any family member sees the account quota reset and says "额度重置了，开个新周期" / "Quota reset, start a new cycle":

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py reset
```

Optional custom name (e.g. `2026-12` or `Cycle 2`):

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py reset --name "2026-12"
```

- Preserves all previous cycles intact in Notion (grouped under their own `Cycle` section).
- Creates the new cycle row starting at `0` (`Since = now`).
- All other family members' devices automatically detect the newer `Cycle` on their next sync and switch over with zero manual steps!

### 5. Configure Start Date (`since`), Member Name, or Optional Quotas

- Count from a specific date (e.g. start of month):
  ```bash
  python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since 2026-11-01
  ```
- Reset counter to right now:
  ```bash
  python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since now
  ```
- Count all local history:
  ```bash
  python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since all
  ```
- After changing `--since`, run `sync-notion-direct --force` so Notion updates immediately.

### 5. View Local or Family-Wide Summary

Local summary (defaults to `--period since`):

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py report --period since
```

Family-wide summary: query the Notion database with `notion-query-data-sources`
and show each member's `Progress`, `Billable Tokens`, `Since`, and share of
total family usage.
