<div align="center">
  <img src="logo.png" alt="nanobot" width="500">
  <h1>nanobot-webui: Ultra-Lightweight Personal AI Assistant</h1>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://github.com/HKUDS/nanobot"><img src="https://img.shields.io/badge/基于-Base%20on%20nanobot-00d4ff" alt="Based on nanobot"></a>
    <a href="./README.zh-CN.md"><img src="https://img.shields.io/badge/中文-README-00d4ff" alt="中文"></a>
  </p>
</div>

---

<p align="center">
  <strong>Tribute to nanobot</strong><br>
  <em>This project is built upon <a href="https://github.com/HKUDS/nanobot">nanobot</a> for secondary development. nanobot was inspired by <a href="https://github.com/openclaw/openclaw">Clawdbot</a>, implementing complete Agent functionality with approximately 4000 lines of core code. Thanks to the nanobot community for the lightweight, readable, and extensible foundation.</em>
</p>

---

## 📢 What's New in This Fork

This fork extends nanobot with **Web UI**, **MCP support**, **additional channels** (Discord, QQ, DingTalk), **document/office skills**, **system monitoring**, and more.

---

## ✨ Features Overview

| Category | Features |
|----------|----------|
| **Core Capabilities** | Multi-platform: Web UI, Telegram, Feishu, CLI; Local运行，保护隐私; Support for multiple LLMs (DeepSeek, Claude, GPT, etc.) |
| **Function Modules** | File system operations, Code execution, Memory system (long-term + daily notes), Extensible skill system |
| **Web UI** | React + TypeScript SPA: Chat, Config (Channels/Providers/Models/MCP/Skills), System Status |
| **Channels** | Discord, QQ (qq-botpy), DingTalk (钉钉), plus original Telegram, WhatsApp, Feishu |
| **MCP** | Model Context Protocol integration — connect external tools via stdio/HTTP/SSE |
| **Skills** | `claude-code`, `git-manager`, `xlsx`, `pdf`, `pptx`, `skill-creator`, `mirror-system`, `code-review-expert` |
| **Cron** | Schedule reminders and recurring tasks — set fixed-time or interval-based jobs |
| **Providers** | Zhipu (智谱), DashScope (通义千问), vLLM, OpenRouter, Anthropic, OpenAI, DeepSeek, Groq, Gemini, Minimax |
| **Startup Scripts** | `nanobot-launcher.sh` (Linux/macOS) + `nanobot-launcher.ps1` (Windows) — Auto environment check, dependency update, frontend build, one-click launch |
| **System** | StatusRepository (SQLite), SystemStatusService (uptime, session count), centralized logging |

---

## 🛠️ Built-in Skills

nanobot-webui comes with a powerful skill system that extends the AI's capabilities:

| Skill | Description |
|-------|-------------|
| **claude-code** | Delegate coding tasks to Claude Code CLI for advanced code generation and refactoring |
| **git-manager** | Git repository management — commit, push, pull, branch operations |
| **xlsx** | Excel spreadsheet operations — read, write, edit .xlsx files |
| **pdf** | PDF operations — read, write, merge, split PDF documents |
| **pptx** | PowerPoint presentation operations — create and edit .pptx files |
| **skill-creator** | Create new skills to extend nanobot's capabilities |
| **mirror-system** | Self-awareness exploration system for personal growth |
| **code-review-expert** | Git diff code review — analyze changes and provide feedback |

### Creating Custom Skills

The skill system is extensible. You can create custom skills by implementing skill functions that the AI can invoke. See the `skill-creator` skill for a template.

---

## ⏰ Scheduled Tasks (Cron)

nanobot-webui supports scheduled tasks through a built-in Cron system, allowing you to set up recurring reminders and automated actions:

| Feature | Description |
|---------|-------------|
| **Fixed-Time Jobs** | Schedule tasks at specific times (e.g., daily 9:00 AM, weekly Monday 10:00 AM) |
| **Interval Jobs** | Run tasks at regular intervals (e.g., every 30 minutes, every 2 hours) |
| **Recurring Reminders** | Set up periodic reminders for meetings, deadlines, health checks, etc. |
| **Message Channels** | Send scheduled messages to any configured channel (Feishu, Telegram, Discord, etc.) |

### Usage Example

```
# Set a daily reminder
"每天早上 9 点提醒我喝水"

# Set a weekly meeting reminder
"每周一上午 10 点提醒我开周会"

# Set an interval check
"每半小时检查一下服务器状态"
```

The Cron system runs in the background and automatically delivers scheduled messages to your configured channels.

---

## 📸 Screenshots

### Chat
Multi-turn conversation with Nanobot, Markdown rendering, new sessions and history. Example shows weather query — Agent fetches real-time data via MCP tools and returns structured card results.

<p align="center">
  <img src="case/chat.png" alt="Chat interface" width="800">
</p>

### System Status
View service health, uptime, active session count, installed Skills, workspace path, and system environment info.

<p align="center">
  <img src="case/system_status.png" alt="System status" width="800">
</p>

### Config — Channels (IM)
Manage IM channel enablement and config — Feishu, Discord, QQ, DingTalk, Telegram, WhatsApp, and more.

<p align="center">
  <img src="case/setting_channels.png" alt="Channels config" width="800">
</p>

### Config — Providers (AI)
Manage AI model providers — DeepSeek, Qwen, Zhipu, OpenAI, Anthropic, OpenRouter, vLLM, Groq, Gemini, etc. Add, edit, or remove providers.

<p align="center">
  <img src="case/setting_providers.png" alt="Providers config" width="800">
</p>

### Config — Default Model
Set the default Agent model — model name (provider/model-name format), Temperature, Max Tokens, and other parameters.

<p align="center">
  <img src="case/default_model.png" alt="Default model config" width="800">
</p>

### Config — MCP
Manage Model Context Protocol servers — stdio, http, sse, streamable_http. Import/generate JSON or add new MCP services.

<p align="center">
  <img src="case/mcp.png" alt="MCP config" width="800">
</p>

### Config — Skills
Manage AI skills — select skill folders for upload to workspace, view installed skills (version, status, description), enable or disable.

<p align="center">
  <img src="case/skills.png" alt="Skills management" width="800">
</p>

---

## 🚀 Quick Start

### Install

```bash
git clone https://github.com/codemo1991/nanobot-webui.git
cd nanobot-webui
pip install -e .
```

### 🚀 One-Click Startup (Recommended for New Users) ⭐

The intelligent startup scripts in the `scripts/` directory handle everything automatically — the simplest way to get started:

**Supported Platforms:**
- **Linux / macOS:** `nanobot-launcher.sh`
- **Windows PowerShell:** `nanobot-launcher.ps1`

**What it does:**
- ✅ Automatically checks Python (≥3.11) and Node.js environment
- ✅ Automatically installs/updates Python dependencies
- ✅ Automatically builds the frontend
- ✅ One-click launches the Web UI service

**Usage:**

- **Windows (PowerShell):**
  ```powershell
  .\scripts\nanobot-launcher.ps1
  ```
- **Linux / macOS:**
  ```bash
  chmod +x scripts/nanobot-launcher.sh
  ./scripts/nanobot-launcher.sh
  ```

Then open http://127.0.0.1:6788

> **Tip:** Run the script again anytime to update dependencies and restart the service — the simplest way to keep your installation up-to-date!

### Manual Startup (Without Scripts)

### Web UI

```bash
# Build frontend (first time)
cd web-ui && npm install && npm run build && cd ..

# Start Web UI (backend + static files)
nanobot web-ui
```

Open http://127.0.0.1:6788 — Chat, Config, and System pages are available.

### CLI Chat

```bash
nanobot onboard   # Initialize config
# Edit ~/.nanobot/config.json with your API key
nanobot agent -m "Hello!"
```

### Docker

```bash
# Build the image
docker build -t nanobot-webui .

# Run (recommended: mount volume for config persistence)
docker run -d -p 6788:6788 -v nanobot-data:/root/.nanobot --name nanobot nanobot-webui

# Or use host path for config
docker run -d -p 6788:6788 -v ~/.nanobot:/root/.nanobot --name nanobot nanobot-webui
```

Then open http://127.0.0.1:6788. On first launch, the app auto-creates `~/.nanobot/config.json`; add your API key via the Config page in the Web UI.

---

## 💻 Web Interface Features

- **Chat** — Create sessions, multi-turn conversations, Markdown rendering, session persistence
- **File Browser** — Browse and manage workspace files directly from the UI
- **Session History** — View and manage conversation history
- **Model Switching** — Easily switch between different AI models
- **Config** — Manage Channels (IM), Providers, Models, MCP servers, Skills
- **System** — Health check, uptime, session count, system info, config export

---

## 📦 Optional Dependencies

```bash
# Feishu
pip install nanobot-ai[feishu]

# QQ
pip install nanobot-ai[qq]

# DingTalk
pip install nanobot-ai[dingtalk]

# MCP
pip install nanobot-ai[mcp]
```

---

## 📁 Project Structure (This Fork)

```
nanobot/
├── agent/          # Core agent (loop, context, memory, tools)
│   └── tools/      # mcp.py, filesystem, shell, registry...
├── channels/       # telegram, whatsapp, feishu, discord, qq, dingtalk
├── web/            # REST API server (api.py)
├── mcp/            # MCP loader
├── storage/        # StatusRepository (SQLite)
├── services/       # SystemStatusService
├── skills/         # claude-code, git-manager, xlsx, pdf, pptx, skill-creator, mirror-system, code-review-expert...
├── providers/      # LLM providers (OpenAI, Anthropic, DeepSeek, etc.)
├── config/         # Extended schema (Discord, QQ, DingTalk, MCP)
└── cli/            # web-ui command, status, etc.
web-ui/             # React SPA (Chat, Config, System)
scripts/             # Startup scripts (nanobot-launcher.sh, nanobot-launcher.ps1)
```

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request or open an Issue for bugs and feature requests.

### Development Setup

```bash
# Clone the repository
git clone https://github.com/codemo1991/nanobot-webui.git
cd nanobot-webui

# Install in development mode
pip install -e .

# Install web-ui dependencies
cd web-ui && npm install

# Run development server
cd ..
nanobot web-ui
```

### Code Style

- Python: Follow PEP 8
- JavaScript/TypeScript: Follow ESLint configuration
- Commit messages: Use clear, descriptive messages

---

## 📄 License

MIT License — see LICENSE file for details.

---

## 🤝 Credits

- **[nanobot](https://github.com/HKUDS/nanobot)** — The base project this fork builds upon
- **nanobot contributors** — For the lightweight, research-friendly design

---

<p align="center">
  <em>Thanks for visiting ✨ nanobot-webui!</em>
</p>
