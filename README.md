# Gemini Family Token Tracker / 家庭共享 Token 消耗统计插件

[English](#english) | [中文说明](#中文说明)

---

## English

An **Antigravity** plugin for family-shared accounts. It automatically records per-device token usage into a local SQLite database using the current logged-in OS username (with **no quota cap** by default), and syncs daily summaries to a shared **Notion** dashboard — family members simply paste the shared Notion dashboard link to bind and sync.

### Features

- **Automatic Zero-Friction Tracking**: Uses Antigravity `Stop` and `PreInvocation` lifecycle hooks to parse local `transcript.jsonl` logs and deduplicate by `(conversation_id, step_index)`, recording `input_tokens`, `cache_read_tokens`, `output_tokens`, and model name.
- **Ready Out of the Box (No Quota Limit by Default)**: Automatically uses the current system login account (`getpass.getuser()`) as the member name. Daily and monthly limits default to `0` (unlimited) — records and syncs without interrupting your workflow.
- **One-Link Notion Sync**: Uses the installed Antigravity Notion plugin (no manual API keys required). Family members simply send the shared Notion dashboard URL in chat to bind and sync aggregated usage (`Date + Member + Device + Model`).
- **Privacy First**: Only aggregated token counts, dates, model names, and device names are synced — **no conversation content is ever uploaded**.

### Installation

#### Prerequisites

1. **Antigravity** installed and signed in (requires Python 3.9+, pre-installed on macOS/Linux).
2. The official **Notion** plugin installed and connected in Antigravity.

#### Step 1: Clone the plugin into Antigravity's global plugins directory

```bash
mkdir -p ~/.gemini/config/plugins
git clone https://github.com/LiuMengxuan04/Gemini-family-token-tracker.git \
  ~/.gemini/config/plugins/family-token-tracker
```

> No restart required — Antigravity automatically discovers and enables `Family Token Tracker / 家庭 Token 统计`.

#### Step 2: Bind the shared Notion dashboard and sync

- **Family Admin (First-time setup)**:
  Ask Antigravity in chat:
  > "Initialize a shared family Token Tracker Notion database and sync my usage."

  Then share the created Notion database URL with your family members.

- **Family Members (Joining an existing dashboard)**:
  Simply paste the Notion dashboard URL in Antigravity chat:
  > "Bind this family Notion dashboard and sync my token usage: https://www.notion.so/xxxxxx"

  The assistant will automatically use your logged-in username, bind the dashboard, and upload your usage.

### Common Chat Prompts

- **Sync to Notion**: `Sync my token usage to our family Notion dashboard`
- **Local summary**: `Show my token usage summary for today and this month`
- **Family breakdown**: `Show our family's monthly token usage breakdown from Notion`
- **Scheduled daily sync (Optional)**: Use `/schedule` in Antigravity to set up an automatic daily sync.

### Local Storage & Optional Configuration

- **Data directory**: `~/.gemini/antigravity/plugin_data/family-token-tracker/`
  - `usage.db`: Local SQLite database of token events and Notion sync state
  - `config.json`: Local settings (`daily_limit: 0`, `monthly_limit: 0` by default)

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

一款用于 **Antigravity** 的家庭共享账号 Token 消耗统计插件。自动记录每台设备的 Token 消耗，默认使用当前电脑登录账号作为用户名、**不设额度上限**，只需发送家庭共享的 Notion 看板链接即可一键绑定并同步上传。

### 功能特点

- **零感自动记录**：通过 Antigravity `Stop` / `PreInvocation` 生命周期钩子自动解析本地对话日志（`transcript.jsonl`），按 `(conversation_id, step_index)` 精确去重记录 `input_tokens`、`cache_read_tokens`、`output_tokens` 与模型名称。
- **开箱即用、默认不限额**：自动读取当前电脑的登录用户名作为成员名，默认不设置任何每日/每月上限（仅记录和同步上传，不打断正常使用）。
- **粘贴链接一键同步 Notion**：依托 Antigravity 官方 Notion 插件，家庭成员无需配置任何 API Key，直接把 Notion 看板链接发给助手即可自动绑定，并按「日期 + 成员 + 设备 + 模型」聚合同步。
- **隐私安全**：仅上传聚合后的 Token 数量、日期、模型与设备名，**绝不上传任何对话正文内容**。

### 安装说明

#### 前置条件

1. 已安装并登录 **Antigravity**（需要 Python 3.9+，macOS/Linux 自带）。
2. 已在 Antigravity 中安装并登录 **Notion** 插件（用于同步家庭共享看板）。

#### 第一步：安装插件

在终端中执行以下命令，将本仓库克隆到 Antigravity 的全局插件目录：

```bash
mkdir -p ~/.gemini/config/plugins
git clone https://github.com/LiuMengxuan04/Gemini-family-token-tracker.git \
  ~/.gemini/config/plugins/family-token-tracker
```

> 安装完成后无需重启，Antigravity 会自动发现并启用 `Family Token Tracker / 家庭 Token 统计` 插件（也可在 Customizations 页面查看开关状态）。

#### 第二步：绑定 Notion 看板并同步

- **如果你是家庭管理员（首次建表）**：
  在 Antigravity 对话中直接说：
  > 「帮我初始化一个家庭 Token 消耗 Notion 看板并同步数据」

  创建完成后，把生成的 Notion 看板链接分享给其他家庭成员即可。

- **如果你是家庭成员（加入已有看板）**：
  在 Antigravity 对话中直接把管理员发给你的 Notion 看板链接发给助手，例如：
  > 「帮我绑定这个家庭 Token 看板并同步我的消耗：https://www.notion.so/xxxxxx」

  助手会自动识别你的当前登录用户名，绑定该看板并将本机 Token 消耗同步上去。

### 日常常用指令

安装完成后，直接在 Antigravity 对话中使用自然语言即可：

- **同步数据**：`把我的 Token 消耗同步到家庭 Notion 看板`
- **查看本机统计**：`查看我今天和本月的 Token 消耗统计`
- **查看全家汇总**：`从 Notion 查看全家本月的 Token 消耗排名`
- **定时自动同步（推荐）**：在对话中使用 `/schedule` 设置每日自动同步任务

### 数据存储与可选配置

- **本地数据目录**：`~/.gemini/antigravity/plugin_data/family-token-tracker/`
  - `usage.db`：本地 SQLite 消耗明细与 Notion 同步状态
  - `config.json`：本机配置（默认 `daily_limit: 0`、`monthly_limit: 0`，不设上限）

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
