<div align="center">
  <img src="logo.png" alt="nanobot" width="500">
  <h1>nanobot-webui: 超级个人AI助手</h1>
  <p>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="https://github.com/HKUDS/nanobot"><img src="https://img.shields.io/badge/基于-nanobot-00d4ff" alt="基于 nanobot"></a>
    <a href="./README.md"><img src="https://img.shields.io/badge/English-README-00d4ff" alt="English"></a>
  </p>
</div>

---

<p align="center">
  <strong>致敬 nanobot</strong><br>
  <em>本项目基于 <a href="https://github.com/HKUDS/nanobot">nanobot</a> 进行二次开发。nanobot 受 <a href="https://github.com/openclaw/openclaw">Clawdbot</a> 启发，以约 4000 行核心代码实现完整 Agent 功能。感谢 nanobot 社区提供的轻量、可读、易扩展的基础架构。</em>
</p>

---

## 📢 本分支新特性

鉴于 openclaw、nanobot 都是通过命令行的配置方式，且缺乏直观的配置管理，同时修改配置项目需要重启服务的问题，本分支在 nanobot 基础上新增 **Web 管理界面**、**MCP 协议支持**、**更多 IM 渠道**（Discord、QQ、钉钉）、**文档/办公 Skills**、**系统状态监控** 等能力。

---

## ✨ 功能概览

| 类别 | 功能 |
|------|------|
| **核心能力** | 多平台支持：Web UI、Telegram、飞书、CLI; 本地运行，保护隐私; 支持多种大语言模型（DeepSeek、Claude、GPT等） |
| **功能模块** | 文件系统操作、代码执行、记忆系统（长期记忆+每日笔记）、可扩展技能系统 |
| **Web UI** | React + TypeScript 单页应用：聊天、配置（Channels/Providers/Models/MCP/Skills）、系统状态 |
| **渠道** | Discord、QQ (qq-botpy)、钉钉，以及原有 Telegram、WhatsApp、飞书 |
| **MCP** | Model Context Protocol 集成，通过 stdio/HTTP/SSE 接入外部工具 |
| **Skills** | `claude-code`、`git-manager`、`xlsx`、`pdf`、`pptx`、`skill-creator`、`mirror-system`、`code-review-expert` |
| **模型提供商** | 智谱、通义千问、vLLM、OpenRouter、Anthropic、OpenAI、DeepSeek、Groq、Gemini、Minimax |
| **启动脚本** | `nanobot-launcher.sh` (Linux/macOS) + `nanobot-launcher.ps1` (Windows) — 自动环境检查、依赖更新、前端构建、一键启动 |
| **系统** | StatusRepository (SQLite)、SystemStatusService（运行时长、会话数）、集中日志 |

---

## 🛠️ 内置技能

nanobot-webui 配备了强大的技能系统，可扩展 AI 的能力：

| 技能 | 描述 |
|------|------|
| **claude-code** | 委托编码任务给 Claude Code CLI，用于高级代码生成和重构 |
| **git-manager** | Git 仓库管理 —— 提交、推送、拉取、分支操作 |
| **xlsx** | Excel 电子表格操作 —— 读取、写入、编辑 .xlsx 文件 |
| **pdf** | PDF 操作 —— 读取、写入、合并、分割 PDF 文档 |
| **pptx** | PowerPoint 演示文稿操作 —— 创建和编辑 .pptx 文件 |
| **skill-creator** | 创建新技能以扩展 nanobot 的能力 |
| **mirror-system** | 自我认知探索系统，助力个人成长 |
| **code-review-expert** | Git diff 代码审查 —— 分析变更并提供反馈 |

### 创建自定义技能

技能系统是可扩展的。你可以通过实现技能函数来创建自定义技能，供 AI 调用。请参考 `skill-creator` 技能作为模板。

---

## 📸 界面截图

### 聊天
与 Nanobot 进行多轮对话，支持 Markdown 渲染、新建会话及历史记录。示例展示天气查询功能，Agent 通过 MCP 工具获取实时数据并以结构化卡片形式返回。

<p align="center">
  <img src="case/chat.png" alt="聊天界面" width="800">
</p>

### 系统状态
查看服务健康状态、运行时长、活跃会话数、已安装 Skills，以及工作空间路径和系统环境信息。

<p align="center">
  <img src="case/system_status.png" alt="系统状态" width="800">
</p>

### 配置 — Channels (IM)
管理即时通讯渠道的启用与配置，支持飞书、Discord、QQ、钉钉、Telegram、WhatsApp 等多平台集成。

<p align="center">
  <img src="case/setting_channels.png" alt="Channels 配置" width="800">
</p>

### 配置 — Providers (AI)
管理 AI 模型提供商，支持 DeepSeek、通义千问、智谱、OpenAI、Anthropic、OpenRouter、vLLM、Groq、Gemini 等，可新增、编辑、删除 Provider。

<p align="center">
  <img src="case/setting_providers.png" alt="Providers 配置" width="800">
</p>

### 配置 — Default Model
设置默认 Agent 模型，指定模型名称（如 provider/model-name 格式），以及 Temperature、Max Tokens 等参数。

<p align="center">
  <img src="case/default_model.png" alt="默认模型配置" width="800">
</p>

### 配置 — MCP
管理 Model Context Protocol 服务器，支持 stdio、http、sse、streamable_http 协议，可导入/生成 JSON 或新增 MCP 服务。

<p align="center">
  <img src="case/mcp.png" alt="MCP 配置" width="800">
</p>

### 配置 — Skills
管理 AI 技能，可选择技能文件夹上传至工作区，查看已安装技能的版本、状态及功能描述，支持启用或禁用。

<p align="center">
  <img src="case/skills.png" alt="Skills 管理" width="800">
</p>

---

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/codemo1991/nanobot-webui.git
cd nanobot-webui
pip install -e .
```

### 🚀 一键启动（推荐新用户使用）⭐

`scripts/` 目录下的智能启动脚本帮您搞定一切 — 最简单的启动方式：

**支持平台：**
- **Linux / macOS：** `nanobot-launcher.sh`
- **Windows PowerShell：** `nanobot-launcher.ps1`

**功能特性：**
- ✅ 自动检查 Python (≥3.11) 和 Node.js 环境
- ✅ 自动安装/更新 Python 依赖
- ✅ 自动构建前端
- ✅ 一键启动 Web UI 服务

**使用方法：**

- **Windows (PowerShell)：**
  ```powershell
  .\scripts\nanobot-launcher.ps1
  ```
- **Linux / macOS：**
  ```bash
  chmod +x scripts/nanobot-launcher.sh
  ./scripts/nanobot-launcher.sh
  ```

然后访问 http://127.0.0.1:6788

> **小提示：** 再次运行脚本即可更新依赖并重启服务 — 保持程序最新最简单的办法！

### 手动启动（不使用脚本）

### Web 界面

```bash
# 首次使用需构建前端
cd web-ui && npm install && npm run build && cd ..

# 启动 Web UI（后端 + 静态文件）
nanobot web-ui
```

打开 http://127.0.0.1:6788，可使用聊天、配置和系统状态页面。

### 命令行对话

```bash
nanobot onboard   # 初始化配置
# 编辑 ~/.nanobot/config.json 填入 API Key
nanobot agent -m "Hello!"
```

### Docker

```bash
# 构建镜像
docker build -t nanobot-webui .

# 启动（推荐挂载数据卷以持久化配置）
docker run -d -p 6788:6788 -v nanobot-data:/root/.nanobot --name nanobot nanobot-webui

# 或使用宿主机路径存放配置
docker run -d -p 6788:6788 -v ~/.nanobot:/root/.nanobot --name nanobot nanobot-webui
```

然后访问 http://127.0.0.1:6788。首次启动时会自动创建 `~/.nanobot/config.json`，可在 Web 界面的配置页中添加 API Key。

---

## 💻 Web 界面功能

- **聊天** — 创建会话、多轮对话、Markdown 渲染、会话持久化
- **文件浏览器** — 直接在界面中浏览和管理工作区文件
- **会话历史** — 查看和管理对话历史
- **模型切换** — 轻松在不同 AI 模型之间切换
- **配置** — 管理 Channels（IM）、Providers、Models、MCP 服务、Skills
- **系统状态** — 健康检查、运行时长、会话数量、系统信息、配置导出

---

## 📦 可选依赖

```bash
# 飞书
pip install nanobot-ai[feishu]

# QQ
pip install nanobot-ai[qq]

# 钉钉
pip install nanobot-ai[dingtalk]

# MCP
pip install nanobot-ai[mcp]
```

---

## 📁 项目结构（本分支）

```
nanobot/
├── agent/          # 核心 Agent（loop、context、memory、tools）
│   └── tools/      # mcp.py、filesystem、shell、registry...
├── channels/       # telegram、whatsapp、飞书、discord、qq、dingtalk
├── web/            # REST API 服务 (api.py)
├── mcp/            # MCP 加载器
├── storage/        # StatusRepository (SQLite)
├── services/       # SystemStatusService
├── skills/         # claude-code、git-manager、xlsx、pdf、pptx、skill-creator、mirror-system、code-review-expert...
├── providers/      # LLM 提供商（OpenAI、Anthropic、DeepSeek 等）
├── config/         # 扩展配置（Discord、QQ、钉钉、MCP）
└── cli/            # web-ui 命令、status 等
web-ui/             # React 单页应用（聊天、配置、系统）
scripts/            # 启动脚本（nanobot-launcher.sh、nanobot-launcher.ps1）
```

---

## 🤝 贡献指南

欢迎贡献代码！请随时提交 Pull Request 或提交 Issue 报告 bug 和功能请求。

### 开发环境设置

```bash
# 克隆仓库
git clone https://github.com/codemo1991/nanobot-webui.git
cd nanobot-webui

# 以开发模式安装
pip install -e .

# 安装 web-ui 依赖
cd web-ui && npm install

# 运行开发服务器
cd ..
nanobot web-ui
```

### 代码规范

- Python：遵循 PEP 8
- JavaScript/TypeScript：遵循 ESLint 配置
- 提交信息：使用清晰、描述性的消息

---

## 📄 许可证

MIT 许可证 — 详见 LICENSE 文件。

---

## 🤝 致谢

- **[nanobot](https://github.com/HKUDS/nanobot)** — 本分支所基于的基础项目
- **nanobot 贡献者** — 感谢轻量、利于研究的架构设计

---

<p align="center">
  <em>感谢访问 ✨ nanobot-webui！</em>
</p>
