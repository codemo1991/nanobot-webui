## 停止 Agent 后台运行的实现计划

### 1. 后端 - AgentLoop 添加取消机制
- 在 `AgentLoop` 类中添加一个 `_cancel_event` (asyncio.Event) 来跟踪取消状态
- 在 `process_direct` 方法中检查这个取消事件，如果被设置则抛出 `asyncio.CancelledError`

### 2. 后端 - API 添加停止端点
- 在 `api.py` 中添加 `/api/stop` POST 端点
- 停止端点需要能够找到当前正在处理的会话/请求，并设置取消事件

### 3. 前端 - 添加停止 API 调用
- 在 `ChatPage.tsx` 的 `handleStop` 函数中调用新的 `/api/stop` API

### 文件修改
- `nanobot/agent/loop.py` - 添加取消事件支持
- `nanobot/web/api.py` - 添加停止 API 端点
- `web-ui/src/pages/ChatPage.tsx` - 添加停止 API 调用