# Gemini Family Token Tracker / 家庭共享 Token 消耗统计插件

[English](#english) | [中文说明](#中文说明)

---

## English

An **Antigravity** plugin for family-shared accounts. It automatically tracks per-device token usage into a local SQLite database using the logged-in OS username (with **no quota cap** by default), counts from a **configurable start date** (defaulting to the exact moment the plugin is installed), and syncs **one clean summary row per member with a visual progress bar** to a shared **Notion** dashboard after every conversation turn with **zero extra LLM calls**.

### Features

- **Clean Member Summary + Progress Bar**: Instead of flooding Notion with per-day/per-model rows, each family member has **one summary row** showing a visual progress bar (`🟦🟦🟦⬜⬜⬜⬜⬜⬜⬜ 3.62M (219 calls)`, or `🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜ 62.5%` if a quota is set), total Billable Tokens, Calls, Start Date (`Since`), and `Last Updated`.
- **Configurable Start Date (`since`)**: Automatically starts counting from the moment the plugin is installed on each device. You can change it anytime to any date (e.g. `--since 2026-11-01`), reset to now (`--since now`), or count all history (`--since all`).
- **Zero-LLM Auto-Sync on Every Turn (Default)**: When configured with a Notion dashboard link and a free Notion Internal Integration Token, every `Stop` hook spawns a detached background process that updates your summary row directly via the Notion REST API — **0 extra LLM calls, 0 added chat latency**.
- **Ready Out of the Box (No Quota Limit by Default)**: Automatically uses the current system login account (`getpass.getuser()`) as the member name. Daily and monthly limits default to `0` (unlimited).
- **Privacy First**: Only aggregated token totals and device/member metadata are synced — **no conversation content is ever uploaded**.

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

#### Step 2: Create the Notion dashboard & grant edit access

- **Family Admin (First-time setup)**:
  Ask Antigravity in chat:
  > "Initialize a shared family Token Tracker Notion database."

- **How to make sure family members have View & Edit access**:
  Personal Notion workspaces have no Teamspace, and Notion's official API / MCP does not allow modifying page sharing permissions programmatically for security reasons, so a newly created dashboard is **Private** by default.

  Open the dashboard link and click **Share** in the top-right corner to grant access once:
  - **Family members using their own Notion accounts (Recommended)**: In **Share → Invite**, enter each family member's Notion email and set permission to **Can edit**, then send the invite. Once accepted, they can read and write to this dashboard via the Antigravity Notion plugin.

#### Step 3: Enable Zero-LLM Automatic Sync After Every Turn (Recommended)

1. Create a free Internal Integration at https://www.notion.so/profile/integrations and copy its token (`ntn_...`).
2. Open your shared Notion dashboard → click **`···` (top right) → Connections → Connect to** your integration.
3. On each family member's computer, bind the dashboard link and token once:

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config \
  --notion-db-url "https://app.notion.com/p/<your-database-id>" \
  --notion-token "ntn_xxxxxxxx" \
  --auto-sync true
```

4. Run an initial sync:

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py sync-notion-direct --force
```

From then on, every conversation turn automatically updates your single summary row in Notion with **zero extra model calls**.

### Configure Start Date (`since`) & Optional Settings

```bash
# View current config (default `since` = plugin install timestamp)
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config

# Count starting from a specific date (e.g. billing cycle start)
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since 2026-11-01

# Reset counter to right now
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since now

# Count all local history (after running `backfill`)
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since all

# Optional: set a reference quota so the progress bar shows percentage (0 = unlimited)
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --monthly-limit 50000000
```

---

## 中文说明

一款用于 **Antigravity** 的家庭共享账号 Token 消耗统计插件。自动记录每台设备的 Token 消耗，默认使用当前电脑登录账号作为用户名、**不设额度上限**、**默认从插件安装那一刻开始累计**（可随时配置从任意日期开始），每次对话结束后在后台**零模型消耗**自动更新 Notion 看板上**每位成员唯一的一行汇总进度条**。

### 功能特点

- **极简汇总看板 + 进度条展示**：不再按天/按模型刷屏，每位家庭成员在 Notion 看板中只占**一行总汇总**，自带可视化进度条（不限额时显示消耗刻度条 `🟦🟦🟦⬜⬜⬜⬜⬜⬜⬜ 3.62M (219 calls)`；设置参考额度后显示百分比进度条 `🟩🟩🟩🟩🟩🟩⬜⬜⬜⬜ 62.5%`）、起始统计时间（`Since`）与最后更新时间（`Last Updated`）。
- **支持自定义起始日期（`since`）**：默认自动从插件安装/首次初始化的那一刻开始统计，也可随时设置为指定日期（如账单日 `--since 2026-11-01`）、立即归零重算（`--since now`）或统计全部历史（`--since all`）。
- **默认自动同步（零额外模型消耗）**：配置 Notion 看板链接和免费的 Notion Integration Token 后，每次对话结束（`Stop` 钩子）会自动拉起后台静默进程直连 Notion REST API 更新自己的汇总行——**不额外调用大模型、零 Token 消耗、不阻塞对话**。
- **开箱即用、默认不限额**：自动读取当前电脑的登录用户名作为成员名，默认不设置任何每日/每月上限。
- **隐私安全**：仅上传聚合后的 Token 总数、设备名与成员名，**绝不上传任何对话正文内容**。

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

> 安装完成后无需重启，Antigravity 会自动发现并启用 `Family Token Tracker / 家庭 Token 统计` 插件。

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

4. 立即执行一次首次同步：

```bash
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py sync-notion-direct --force
```

配置完成后，以后**每次对话结束都会在后台静默自动更新你的那一行汇总进度条，完全不消耗额外模型 Token**。

### 配置起始日期（`since`）与可选设置

```bash
# 查看当前配置（since 默认为插件安装/首次初始化时刻）
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config

# 配置从指定日期开始统计（例如每月账单日）
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since 2026-11-01

# 从此刻重新开始累计
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since now

# 统计本机全部历史记录（配合 backfill）
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --since all

# 可选：设置参考额度，让进度条显示百分比（0 表示不限额）
python3 ~/.gemini/config/plugins/family-token-tracker/scripts/tracker.py config --monthly-limit 50000000
```
