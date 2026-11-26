# Gemini Family Token Tracker / 家庭共享 Token 消耗统计插件

[English](#english) | [中文说明](#中文说明)

---

## English

An **Antigravity** plugin for family-shared accounts. It automatically records per-device token usage into a local SQLite database using the current logged-in OS username (with **no quota cap** by default), and supports **zero-LLM background auto-sync** to a shared **Notion** dashboard after every conversation turn.

### Features

- **Automatic Zero-Friction Tracking**: Uses Antigravity `Stop` and `PreInvocation` lifecycle hooks to parse local `transcript.jsonl` logs and deduplicate by `(conversation_id, step_index)`, recording `input_tokens`, `cache_read_tokens`, `output_tokens`, and model name.
- **Zero-LLM Auto-Sync on Every Turn (Default)**: When bound with a Notion dashboard link and a free Notion Internal Integration Token, every `Stop` hook spawns a detached background process that pushes directly to the Notion REST API — **0 extra LLM calls, 0 added chat latency**.
- **Ready Out of the Box (No Quota Limit by Default)**: Automatically uses the current system login account (`getpass.getuser()`) as the member name. Daily and monthly limits default to `0` (unlimited) — records and syncs without interrupting your workflow.
- **MCP / Scheduled Sync Fallback**: Don't want to create an Integration Token? You can still bind the Notion dashboard link via the installed Antigravity Notion plugin and use `/schedule` for daily scheduled sync.
- **Privacy First**: Only aggregated token counts, dates, model names, and device names are synced — **no conversation content is ever uploaded**.

### Installation

#### Prerequisites

1. **Antigravity** installed and signed in (requires Python 3.9+, pre-installed on macOS/Linux).
2. The official **Notion** plugin installed and connected in Antigravity (for creating/querying the dashboard and MCP fallback).

#### Step 1: Clone the plugin into Antigravity's global plugins directory

```bash
mkdir -p ~/.gemini/config/plugins
git clone https://github.com/LiuMengxuan04/Gemini-family-token-tracker.git \
  ~/.gemini/config/plugins/family-token-tracker
```

> No restart required — Antigravity automatically discovers and enables `Family Token Tracker / 家庭 Token 统计`.

#### Step 2: Create or bind the shared Notion dashboard

- **Family Admin (First-time setup)**:
  Ask Antigravity in chat:
  > "Initialize a shared family Token Tracker Notion database."

- **How to make sure family members have View & Edit access**:
  Personal Notion workspaces have no Teamspace, and Notion's official API / MCP does not allow modifying page sharing permissions programmatically for security reasons, so a newly created dashboard is **Private** by default.

  Open the dashboard link and click **Share** in the top-right corner to grant access once:
  - **Family members using their own Notion accounts (Recommended)**: In **Share → Invite**, enter each family member's Notion email and set permission to **Can edit**, then send the invite. Once accepted, they can read and write to this dashboard via the Antigravity Notion plugin.

#### Step 3: Enable Zero-LLM Automatic Sync After Every Turn (Recommended)

To sync silently after every turn without spending any extra model tokens:

1. Create a free Internal Integration at https://www.notion.so/profile/integrations and copy its token (`ntn_...`).
2. Open your shared Notion dashboard → click **`···` (top right) → Connections → Connect to** your integration.
3. On each family member's computer, bind the dashboard link and token once (either in Antigravity chat or via terminal):

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
  --notion-db-url "https://app.notion.com/p/<your-database-id>" \
  --notion-token "ntn_xxxxxxxx" \
  --auto-sync true
```

4. Run a one-time backfill + sync test:

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py backfill && \
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py sync-notion-direct
```

From then on, every conversation turn will automatically sync to Notion in the background with **zero extra model calls**.

> **Without an Integration Token**: Family members can simply paste the Notion dashboard link in Antigravity chat (`"Bind this family Notion dashboard and sync: <link>"`) and optionally use `/schedule` for daily scheduled sync.

### Common Chat Prompts

- **Sync to Notion**: `Sync my token usage to our family Notion dashboard`
- **Local summary**: `Show my token usage summary for today and this month`
- **Family breakdown**: `Show our family's monthly token usage breakdown from Notion`

### Local Storage & Optional Configuration

- **Data directory**: `~/.gemini/antigravity/plugin_data/family-token-tracker/`
  - `usage.db`: Local SQLite database of token events and Notion sync state
  - `config.json`: Local settings (`auto_sync: true`, `daily_limit: 0`, `monthly_limit: 0` by default)
  - `last_sync.json`: Timestamp and result of the latest direct auto-sync

```bash
# View current configuration
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config

# Optional: override member name or enable gentle quota reminders (0 = unlimited)
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
  --member "YourName" \
  --daily-limit 0 \
  --monthly-limit 0
```

---

## 中文说明

一款用于 **Antigravity** 的家庭共享账号 Token 消耗统计插件。自动记录每台设备的 Token 消耗，默认使用当前电脑登录账号作为用户名、**不设额度上限**，并支持**零模型消耗的后台自动同步**——每次对话结束后自动静默同步到家庭共享 Notion 看板。

### 功能特点

- **零感自动记录**：通过 Antigravity `Stop` / `PreInvocation` 生命周期钩子自动解析本地对话日志（`transcript.jsonl`），按 `(conversation_id, step_index)` 精确去重记录 `input_tokens`、`cache_read_tokens`、`output_tokens` 与模型名称。
- **默认自动同步（零额外模型消耗）**：配置 Notion 看板链接和免费的 Notion Integration Token 后，每次对话结束（`Stop` 钩子）会自动拉起后台静默进程直连 Notion REST API 完成同步——**不额外调用大模型、不消耗任何额外 Token、不阻塞对话**。
- **开箱即用、默认不限额**：自动读取当前电脑的登录用户名作为成员名，默认不设置任何每日/每月上限（仅记录和同步上传，不打断正常使用）。
- **兼容 Notion 插件定时同步**：如果不想申请 Integration Token，也可以直接把 Notion 看板链接发给助手绑定，并配合 `/schedule` 做每日定时同步。
- **隐私安全**：仅上传聚合后的 Token 数量、日期、模型与设备名，**绝不上传任何对话正文内容**。

### 安装说明

#### 前置条件

1. 已安装并登录 **Antigravity**（需要 Python 3.9+，macOS/Linux 自带）。
2. 已在 Antigravity 中安装并登录 **Notion** 插件（用于创建/查询看板或 MCP 同步）。

#### 第一步：安装插件

在终端中执行以下命令，将本仓库克隆到 Antigravity 的全局插件目录：

```bash
mkdir -p ~/.gemini/config/plugins
git clone https://github.com/LiuMengxuan04/Gemini-family-token-tracker.git \
  ~/.gemini/config/plugins/family-token-tracker
```

> 安装完成后无需重启，Antigravity 会自动发现并启用 `Family Token Tracker / 家庭 Token 统计` 插件（也可在 Customizations 页面查看开关状态）。

#### 第二步：创建看板并配置共享权限

- **如果你是家庭管理员（首次建表）**：
  在 Antigravity 对话中直接说：
  > 「帮我初始化一个家庭 Token 消耗 Notion 看板」

- **如何确保家庭成员「有权限访问和编辑」**：
  你的 Notion 目前是个人工作区（无 Teamspace），且 Notion 官方 API / MCP 出于安全策略不支持通过接口直接修改页面的共享权限，因此新建的看板默认是 Private（仅自己可见）。

  请打开上面的链接，点击右上角 **Share（共享）** 完成一次授权（二选一）：
  - **家庭成员使用各自的 Notion 账号（推荐）**：在 **Share → Invite** 中输入家庭成员的 Notion 邮箱，右侧权限选择 **Can edit（可编辑）** 并发送邀请。对方接受后，即可通过 Antigravity 的 Notion 插件读写这个看板。

#### 第三步：开启「每次对话结束零模型消耗自动同步」（推荐）

只需一次性配置一个免费的 Notion Integration Token（全家人可共用同一个 Token）：

1. 打开 https://www.notion.so/profile/integrations 创建一个 Internal Integration，复制以 `ntn_` 开头的 Token。
2. 打开家庭共享 Notion 看板，点击右上角 **`···` → Connections（连接）**，将刚才创建的 Integration 关联到该看板。
3. 在每台家庭成员电脑上执行一次绑定（或在对话中把链接和 Token 发给助手）：

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
  --notion-db-url "https://app.notion.com/p/<你的看板ID>" \
  --notion-token "ntn_xxxxxxxx" \
  --auto-sync true
```

4. 立即执行一次历史回溯与直连同步测试：

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py backfill && \
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py sync-notion-direct
```

配置完成后，以后**每次对话结束都会在后台静默自动同步到 Notion，完全不消耗额外模型 Token**。

> **如果不配置 Integration Token**：家庭成员也可以直接把 Notion 看板链接发给助手（「帮我绑定这个家庭 Token 看板并同步我的消耗：`<链接>`」），并配合 `/schedule` 设置每日定时同步。

### 日常常用指令

安装完成后，直接在 Antigravity 对话中使用自然语言即可：

- **手动同步数据**：`把我的 Token 消耗同步到家庭 Notion 看板`
- **查看本机统计**：`查看我今天和本月的 Token 消耗统计`
- **查看全家汇总**：`从 Notion 查看全家本月的 Token 消耗排名`

### 数据存储与可选配置

- **本地数据目录**：`~/.gemini/antigravity/plugin_data/family-token-tracker/`
  - `usage.db`：本地 SQLite 消耗明细与 Notion 同步状态
  - `config.json`：本机配置（默认 `auto_sync: true`、`daily_limit: 0`、`monthly_limit: 0`）
  - `last_sync.json`：最近一次直连自动同步的时间与结果

如需手动调整成员名或开启可选的额度温和提醒，可以直接对助手说，或运行 CLI：

```bash
# 查看当前配置
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config

# 可选：自定义成员名或设置日/月额度提醒（0 表示不限额）
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
  --member "你的名字" \
  --daily-limit 0 \
  --monthly-limit 0
```
