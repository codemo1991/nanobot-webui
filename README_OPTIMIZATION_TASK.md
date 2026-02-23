# README 文件优化任务

## 项目概述
nanobot-webui 是一个基于 nanobot 的二次开发项目，是一个超轻量级的个人AI助手框架，具有Web管理界面、多IM渠道支持、MCP协议集成、丰富的技能系统和系统监控功能。

## 当前问题
现有的 README.md 和 README.zh-CN.md 文件功能描述不够全面，需要根据项目的实际功能和能力进行优化。

## 项目核心功能

### 1. 核心架构
- 基于 nanobot 的二次开发，约4000行核心代码实现完整Agent功能
- 轻量、可读、易扩展的基础架构

### 2. Web UI 功能
- React + TypeScript 单页应用
- 聊天界面：多轮对话、Markdown渲染、会话持久化
- 配置管理：Channels/Providers/Models/MCP/Skills
- 系统状态：健康检查、运行时长、会话数量、系统信息
- 镜像系统：悟/辩/赏/镜四模块的自我认知探索系统

### 3. 支持的IM渠道
- 原有：Telegram、WhatsApp、飞书
- 新增：Discord、QQ (qq-botpy)、钉钉

### 4. MCP (Model Context Protocol) 支持
- 通过 stdio/HTTP/SSE 接入外部工具
- 支持多种传输协议

### 5. 丰富的技能系统
#### 核心技能：
- **claude-code**: 委托编码任务给Claude Code CLI实现
- **code-review-expert**: Git diff代码审查，SOLID原则、安全性、性能
- **git-manager**: Git管理工具
- **mirror-system**: 个人镜像系统（悟/辩/赏/镜四模块）
- **skill-creator**: 创建新技能的工具

#### 文档处理技能：
- **docx**: Word文档创建、编辑、分析
- **pdf**: PDF读/写/合并/分割/OCR
- **pptx**: PowerPoint演示文稿处理
- **xlsx**: Excel电子表格操作

#### 实用工具技能：
- **github**: GitHub CLI集成
- **weather**: 天气信息获取
- **summarize**: URL/文件/YouTube视频摘要
- **tmux**: 远程控制tmux会话
- **cron**: 定时任务和提醒

### 6. 支持的AI模型提供商
- 智谱 (Zhipu)
- 通义千问 (DashScope)
- vLLM
- OpenRouter
- Anthropic
- OpenAI
- DeepSeek
- Groq
- Gemini
- Minimax

### 7. 系统功能
- StatusRepository (SQLite)
- SystemStatusService (运行时长、会话数)
- 集中日志系统

### 8. 部署方式
- 本地安装 (pip install -e .)
- Docker容器化部署
- 一键启动脚本 (startup.sh / startup.bat)

### 9. 技术栈
#### 后端：
- Python ≥3.11
- 核心库：typer, litellm, pydantic, httpx, loguru
- 渠道库：lark-oapi, qq-botpy, dingtalk-stream, python-telegram-bot

#### 前端：
- React 18 + TypeScript
- Ant Design UI组件库
- Vite构建工具
- i18next国际化

## 优化要求

### 英文 README.md 优化要求：
1. **项目概述**：清晰描述项目定位、核心价值和主要特性
2. **功能详解**：详细说明所有核心功能，包括Web UI、渠道、MCP、技能系统等
3. **快速开始**：提供多种部署方式的详细步骤
4. **技能目录**：创建完整的技能目录，描述每个技能的功能和使用场景
5. **配置指南**：提供详细的配置说明
6. **API文档**：简要介绍API接口
7. **开发指南**：为贡献者提供开发指南
8. **常见问题**：添加常见问题解答
9. **致谢**：保留对nanobot和贡献者的致谢

### 中文 README.zh-CN.md 优化要求：
1. **完整翻译**：确保所有内容都有准确的中文翻译
2. **本地化调整**：针对中文用户调整示例和说明
3. **技术术语**：保持技术术语的一致性
4. **中文社区**：添加中文社区相关的信息

### 格式要求：
1. 使用清晰的章节结构
2. 添加目录导航
3. 使用表格展示功能对比
4. 添加代码块示例
5. 使用徽章展示项目状态
6. 添加截图展示界面效果

## 输出要求
生成两个文件：
1. README.md (英文版)
2. README.zh-CN.md (中文版)

两个文件都需要：
- 包含完整的功能描述
- 结构清晰，易于阅读
- 包含实际使用示例
- 提供详细的安装和配置指南