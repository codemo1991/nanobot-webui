# nanobot 项目两项重要改进：SSE 流式响应与配置 Web UI 可视化

> 在 AI Agent 时代，用户对实时交互体验的要求越来越高。nanobot 作为一款功能强大的 AI 助手框架，近期进行了两项重要的功能升级：SSE 流式响应增强和配置内容的 Web UI 可视化。本文将详细介绍这两项改进的技术实现、使用方法和使用场景。

## 一、技术背景与问题

### 1.1 传统 HTTP 请求的局限性

在传统的 HTTP 请求/响应模式中，客户端发送请求后必须等待服务器完成全部处理才能收到响应。这种模式在 AI 对话场景中存在以下问题：

- **等待时间长**：LLM 生成内容需要一定时间，用户只能看到"转圈"等待
- **体验单调**：无法实时看到 AI 正在"思考"的过程
- **资源浪费**：如果生成内容较长，用户需要等待很久才能看到部分结果
- **不支持实时进度**：无法向用户展示工具调用、搜索等中间过程

### 1.2 配置管理的痛点

nanobot 作为一个功能丰富的 AI Agent 框架，需要管理大量配置：

- **多渠道接入**：WhatsApp、Telegram、飞书、Discord、QQ、钉钉
- **多 AI 提供商**：OpenAI、Anthropic、DeepSeek、智谱、通义千问等
- **MCP 服务器**：Model Context Protocol 服务器配置
- **系统参数**：Agent 行为、并发控制、记忆系统等

传统方式需要通过修改配置文件或命令行参数来管理这些配置，门槛较高。

---

## 二、SSE 流式响应增强

### 2.1 什么是 SSE？

Server-Sent Events（SSE）是一种允许服务器主动向客户端推送数据的技术。相比 WebSocket，SSE 更轻量，基于 HTTP 协议，不需要复杂的协议握手，适合单向数据流场景。

SSE 的核心特点：
- 单向通信：服务器 → 客户端
- 基于 HTTP：使用 `text/event-stream` 内容类型
- 自动重连：浏览器自动处理连接断开后的重连
- 简单易用：相比 WebSocket 协议更简单

### 2.2 nanobot 的 SSE 实现

nanobot 在 `web/api.py` 中实现了完整的 SSE 流式响应功能：

#### 2.2.1 聊天流式响应

```python
def _handle_chat_stream(
    self, app: "NanobotWebAPI", session_id: str, content: str, images: list[str] | None = None
) -> None:
    """Stream chat progress via SSE. Resilient to client disconnect and worker errors."""
    evt_queue, thread = app.chat_stream(session_id, content, images)
    thread.start()
    
    self.send_response(HTTPStatus.OK)
    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("Connection", "keep-alive")
    # ...
```

核心实现要点：

1. **事件队列**：使用 `queue.Queue` 作为线程间通信的桥梁
2. **独立线程**：在独立线程中运行 AI 处理，不阻塞主请求处理
3. **SSE 协议**：正确设置 `Content-Type` 为 `text/event-stream`
4. **心跳机制**：定期发送心跳保持连接活跃

#### 2.2.2 事件类型定义

```python
# 聊天流事件
{"type": "start", "session_id": "xxx"}          # 开始处理
{"type": "progress", "content": "正在思考..."} # 思考中
{"type": "tool_call", "tool": "search", ...}   # 工具调用
{"type": "tool_result", "tool": "search", ...}# 工具返回
{"type": "content", "delta": "生成的文字"}      # 内容片段
{"type": "done", "content": "完整回复"}         # 处理完成
{"type": "error", "message": "错误信息"}        # 发生错误
```

#### 2.2.3 子 Agent 进度流

nanobot 还支持子 Agent（Claude Code）进度实时推送：

```python
def _handle_subagent_progress_stream(
    self, app: "NanobotWebAPI", session_id: str
) -> None:
    """以 SSE 形式持续推送子 Agent 进度事件"""
    origin_key = f"web:{session_id}"
    evt_queue = app.subagent_progress_stream(session_id)
    # 持续监听并推送进度事件
```

这使得用户可以实时看到子 Agent 的执行进度：
- 子 Agent 启动
- 当前正在执行的任务
- 任务完成或失败

#### 2.2.4 心跳与断连处理

为了保证连接的稳定性，实现了完善的心跳机制：

```python
heartbeat_interval = 30  # 心跳间隔（秒）
last_heartbeat = time.time()

# 定期发送心跳
if now - last_heartbeat >= heartbeat_interval:
    self.wfile.write(b": heartbeat\n\n")
    self.wfile.flush()
    last_heartbeat = now
```

断连检测：
```python
except (BrokenPipeError, ConnectionResetError, OSError):
    logger.debug("Client disconnected, stopping stream")
    break
```

### 2.3 前端 SSE 消费

前端通过 `EventSource` API 消费 SSE：

```typescript
// 创建 EventSource
const eventSource = new EventSource(`/api/v1/chat/sessions/${sessionId}/messages?stream=1`);

// 监听消息
eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    switch (data.type) {
        case 'content':
            // 追加内容
            appendContent(data.delta);
            break;
        case 'done':
            // 完成
            eventSource.close();
            break;
        case 'error':
            // 错误处理
            handleError(data.message);
            break;
    }
};
```

### 2.4 使用效果

启用 SSE 流式响应后，用户体验显著提升：

1. **即时反馈**：输入发送后立即开始显示"AI 正在思考"
2. **逐字显示**：AI 生成的文字逐字显示，更有交互感
3. **过程可见**：可以清楚看到 AI 调用了哪些工具
4. **实时进度**：子 Agent 执行进度实时推送

---

## 三、配置内容的 Web UI 可视化

### 3.1 设计理念

配置 Web UI 可视化的设计理念：

- **直观易用**：用表单、开关、卡片替代配置文件
- **实时生效**：修改配置后立即生效，无需重启
- **分组管理**：按功能模块分组，便于查找
- **安全提示**：敏感信息（如 API Key）显示为掩码

### 3.2 功能模块

nanobot 的配置页面包含以下模块：

#### 3.2.1 渠道配置（Channels）

支持多种即时通讯渠道的一站式配置：

| 渠道 | 功能 |
|------|------|
| WhatsApp | 企业微信 WhatsApp Business API |
| Telegram | Bot API 配置 |
| 飞书 | 企业自建应用配置 |
| Discord | Discord Bot 配置 |
| QQ | QQ 机器人配置 |
| 钉钉 | 钉钉企业自建应用 |

每个渠道都支持：
- 启用/禁用开关
- 认证信息配置（Token、App Secret 等）
- 代理设置
- 白名单用户限制

```typescript
// 渠道配置表单
<Form.Item name="enabled" valuePropName="checked">
    <Switch />  // 启用开关
</Form.Item>
<Form.Item name="appId" label="App ID">
    <Input placeholder="cli_..." />
</Form.Item>
<Form.Item name="appSecret" label="App Secret">
    <Input.Password />  // 密码掩码
</Form.Item>
```

#### 3.2.2 AI 提供商配置（Providers）

支持 10+ 主流 AI 提供商：

```typescript
const providerOptions = [
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'openai', label: 'OpenAI' },
    { value: 'openrouter', label: 'OpenRouter' },
    { value: 'deepseek', label: 'DeepSeek' },
    { value: 'minimax', label: 'Minimax' },
    { value: 'groq', label: 'Groq' },
    { value: 'zhipu', label: 'Zhipu (智谱)' },
    { value: 'dashscope', label: 'Qwen (通义)' },
    { value: 'gemini', label: 'Gemini' },
    { value: 'vllm', label: 'vLLM' },
];
```

每个提供商支持：
- API Key 配置（掩码显示）
- 自定义 API Base URL
- 可选参数配置

#### 3.2.3 模型配置（Models）

```typescript
<Form.Item name="modelName" label="模型名称">
    <Input placeholder="anthropic/claude-3-5-sonnet-20241022" />
</Form.Item>

<Form.Item name="temperature" label="Temperature">
    <Input type="number" step="0.1" min="0" max="2" />
</Form.Item>

<Form.Item name="maxTokens" label="Max Tokens">
    <Input type="number" min="1" />
</Form.Item>

<Form.Item name="subagentModel" label="子 Agent 模型">
    <Input placeholder="dashscope/qwen-vl-plus" />
</Form.Item>
```

#### 3.2.4 MCP 服务器配置

支持可视化创建和管理 MCP 服务器：

```typescript
// MCP 传输类型
const transportOptions = [
    { value: 'stdio', label: '标准输入输出' },
    { value: 'http', label: 'HTTP' },
    { value: 'sse', label: 'SSE' },
    { value: 'streamable_http', label: 'Streamable HTTP' },
];
```

支持：
- 从 JSON 文件导入 MCP 配置
- 从剪贴板 JSON 生成 MCP 配置
- 支持 Cursor 格式的 `mcpServers` 配置
- 连接测试功能

#### 3.2.5 系统配置

##### Agent 行为配置

```typescript
<Form.Item name="maxToolIterations" label="最大工具调用次数">
    <InputNumber min={1} max={200} />
</Form.Item>

<Form.Item name="maxExecutionTime" label="最大执行时间(秒)">
    <InputNumber min={0} />
</Form.Item>
```

##### 并发控制配置

```typescript
<Form.Item name="threadPoolSize" label="线程池大小" />
<Form.Item name="maxParallelToolCalls" label="最大并行工具数" />
<Form.Item name="maxConcurrentSubagents" label="最大并行子代理数" />
<Form.Item name="enableParallelTools" label="启用工具并行">
    <Switch />
</Form.Item>
<Form.Item name="enableSmartParallel" label="启用智能并行">
    <Switch />
</Form.Item>
```

##### 记忆系统配置

```typescript
<Form.Item name="autoIntegrateEnabled" label="启用自动整合">
    <Switch />
</Form.Item>
<Form.Item name="autoIntegrateIntervalMinutes" label="整合间隔(分钟)" />
<Form.Item name="lookbackMinutes" label="回顾时间(分钟)" />
<Form.Item name="maxMessages" label="最大消息数" />
<Form.Item name="maxEntries" label="最大长期记忆条目" />
<Form.Item name="maxChars" label="最大字符数" />
```

##### 工作目录切换

```typescript
<Button icon={<SwapOutlined />} onClick={() => setModalVisible(true)}>
    切换工作目录
</Button>
```

### 3.3 后端 API 设计

配置相关 API 设计遵循 RESTful 风格：

```python
# 获取配置
GET /api/v1/config

# 获取特定配置
GET /api/v1/config/memory
GET /api/v1/config/concurrency

# 更新配置
PUT /api/v1/config/agent      # Agent 配置
PUT /api/v1/config/concurrency # 并发配置
PUT /api/v1/config/memory     # 记忆配置

# 渠道配置
GET /api/v1/channels
PUT /api/v1/channels

# 提供商配置
GET /api/v1/providers
POST /api/v1/providers
PUT /api/v1/providers/{providerId}
DELETE /api/v1/providers/{providerId}

# MCP 配置
GET /api/v1/mcps
POST /api/v1/mcps
PUT /api/v1/mcps/{mcpId}
DELETE /api/v1/mcps/{mcpId}
POST /api/v1/mcps/{mcpId}/test  # 测试连接
```

### 3.4 热更新机制

配置修改后无需重启服务，通过热更新机制立即生效：

```python
def update_agent_config(self, data: dict[str, Any]) -> dict[str, Any]:
    """Update agent system config. Hot-updates running agent."""
    config = load_config()
    defaults = config.agents.defaults
    
    # 更新配置
    if "maxToolIterations" in data:
        defaults.max_tool_iterations = int(data["maxToolIterations"])
    if "maxExecutionTime" in data:
        defaults.max_execution_time = int(data["maxExecutionTime"])
    
    save_config(config)
    
    # 热更新运行中的 Agent
    self.agent.update_agent_params(
        max_iterations=defaults.max_tool_iterations,
        max_execution_time=defaults.max_execution_time,
    )
```

---

## 四、总结与展望

### 4.1 功能总结

本文介绍的两项改进：

| 功能 | 改进前 | 改进后 |
|------|--------|--------|
| AI 响应 | 等待完整生成 | 实时流式显示 |
| 工具调用 | 完成后才知道 | 实时进度推送 |
| 子 Agent | 无法查看进度 | SSE 实时推送 |
| 配置管理 | 修改配置文件 | Web UI 可视化 |
| 配置生效 | 需要重启 | 热更新即时生效 |

### 4.2 技术亮点

1. **SSE 实现**：
   - 完善的线程间通信机制
   - 智能心跳保持连接
   - 断连自动检测和处理
   - 支持多种事件类型

2. **Web UI 配置**：
   - 分组清晰的配置界面
   - 敏感信息保护
   - JSON 批量导入
   - 配置热更新

### 4.3 未来展望

后续可能的改进方向：

1. **流式响应增强**：
   - 支持更多事件类型（Token 使用量、推理时间等）
   - Markdown 实时渲染
   - 代码块流式输出

2. **配置管理增强**：
   - 配置文件版本管理
   - 配置模板导出/导入
   - 配置变更审计日志

3. **移动端适配**：
   - 响应式布局优化
   - PWA 支持

---

## 相关资源

- nanobot 项目地址：https://github.com/codemo1991/nanobot-webui
- SSE 规范：https://html.spec.whatwg.org/multipage/server-sent-events.html
- MCP 规范：https://modelcontextprotocol.io/

---

*本文档由 nanobot AI Agent 自动生成*
