# Nanobot Web UI

基于 React + TypeScript + Vite 构建的 Nanobot Web 用户界面。

## 快速开始

### 安装依赖

```bash
npm install
```

### 开发模式

```bash
npm run dev
```

开发服务器将在 http://localhost:5173 启动，并自动代理 API 请求到 http://127.0.0.1:6788

### 构建生产版本

```bash
npm run build
```

构建产物将输出到 `dist/` 目录。

### 预览生产构建

```bash
npm run preview
```

## 项目结构

```
web-ui/
├── src/
│   ├── components/     # 可复用组件
│   │   └── Layout.tsx  # 主布局和导航
│   ├── pages/          # 页面组件
│   │   ├── ChatPage.tsx
│   │   ├── ConfigPage.tsx
│   │   ├── SkillMarketPage.tsx
│   │   ├── SkillBuilderPage.tsx
│   │   └── SystemPage.tsx
│   ├── api.ts          # API 客户端
│   ├── store.ts        # 全局状态管理
│   ├── types.ts        # TypeScript 类型定义
│   ├── App.tsx         # 根组件
│   ├── main.tsx        # 应用入口
│   └── index.css       # 全局样式
├── index.html
├── package.json
├── tsconfig.json
└── vite.config.ts
```

## 功能特性

### 1. 聊天页面 (ChatPage)
- ✅ 会话列表管理（创建、切换、删除、重命名）
- ✅ 多轮对话
- ✅ Markdown 渲染
- ✅ 消息持久化
- ✅ 错误提示

### 2. 配置页面 (ConfigPage)
- 🚧 Channel 配置管理
- 🚧 Model 配置管理
- 🚧 MCP 服务配置
- 🚧 已安装 Skills 管理

### 3. Skill 市场 (SkillMarketPage)
- 🚧 浏览和搜索 Skills
- 🚧 查看 Skill 详情
- 🚧 安装/升级/卸载 Skills

### 4. Skill 创建工具 (SkillBuilderPage)
- 🚧 五步向导创建 Skill
- 🚧 基础信息配置
- 🚧 输入输出定义
- 🚧 运行时配置
- 🚧 测试样例
- 🚧 生成与导出

### 5. 系统状态 (SystemPage)
- ✅ 健康检查
- ✅ 系统信息展示
- 🚧 运行状态监控

图例：
- ✅ 已实现
- 🚧 UI 框架已搭建，功能待完善

## 界面截图

### 聊天
与 Nanobot 进行多轮对话，支持 Markdown 渲染、新建会话及历史记录。示例展示天气查询功能，Agent 通过 MCP 工具获取实时数据并以结构化卡片形式返回。

<p align="center">
  <img src="../case/chat.png" alt="聊天界面" width="800">
</p>

### 系统状态
查看服务健康状态、运行时长、活跃会话数、已安装 Skills，以及工作空间路径和系统环境信息。

<p align="center">
  <img src="../case/system_status.png" alt="系统状态" width="800">
</p>

### 配置 — Channels (IM)
管理即时通讯渠道的启用与配置，支持飞书、Discord、QQ、钉钉、Telegram、WhatsApp 等多平台集成。

<p align="center">
  <img src="../case/setting_channels.png" alt="Channels 配置" width="800">
</p>

### 配置 — Providers (AI)
管理 AI 模型提供商，支持 DeepSeek、通义千问、智谱、OpenAI、Anthropic、OpenRouter、vLLM、Groq、Gemini 等，可新增、编辑、删除 Provider。

<p align="center">
  <img src="../case/setting_providers.png" alt="Providers 配置" width="800">
</p>

### 配置 — Default Model
设置默认 Agent 模型，指定模型名称（如 provider/model-name 格式），以及 Temperature、Max Tokens 等参数。

<p align="center">
  <img src="../case/default_model.png" alt="默认模型配置" width="800">
</p>

### 配置 — MCP
管理 Model Context Protocol 服务器，支持 stdio、http、sse、streamable_http 等协议，可导入/生成 JSON 或新增 MCP 服务。

<p align="center">
  <img src="../case/mcp.png" alt="MCP 配置" width="800">
</p>

### 配置 — Skills
管理 AI 技能，可选择技能文件夹上传至工作区，查看已安装技能的版本、状态及功能描述，支持启用或禁用。

<p align="center">
  <img src="../case/skills.png" alt="Skills 管理" width="800">
</p>

## 技术栈

- **React 18** - UI 框架
- **TypeScript** - 类型安全
- **Vite** - 构建工具
- **React Router** - 路由管理
- **Zustand** - 状态管理
- **React Markdown** - Markdown 渲染

## API 集成

Web UI 通过 `/api/v1` 端点与后端通信：

- `GET /api/v1/health` - 健康检查
- `GET /api/v1/chat/sessions` - 获取会话列表
- `POST /api/v1/chat/sessions` - 创建会话
- `DELETE /api/v1/chat/sessions/{id}` - 删除会话
- `PATCH /api/v1/chat/sessions/{id}` - 重命名会话
- `GET /api/v1/chat/sessions/{id}/messages` - 获取消息
- `POST /api/v1/chat/sessions/{id}/messages` - 发送消息

## 开发指南

### 添加新页面

1. 在 `src/pages/` 创建新组件
2. 在 `src/App.tsx` 添加路由
3. 在 `src/components/Layout.tsx` 添加导航链接

### 添加新 API

1. 在 `src/types.ts` 定义类型
2. 在 `src/api.ts` 添加 API 方法
3. 在组件中使用

### 状态管理

使用 Zustand 管理全局状态（见 `src/store.ts`）。

## 部署

构建完成后，将 `dist/` 目录的内容复制到后端的静态文件目录，或通过 `nanobot web-ui` 命令启动时自动查找。
