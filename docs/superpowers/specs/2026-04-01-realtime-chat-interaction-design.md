# 实时会话交互设计文档

**日期**: 2026-04-01  
**主题**: WebUI 与后端实时交互增强设计  
**状态**: 待审核

---

## 1. 概述

### 1.1 目标

增强 Nanobot WebUI 的实时交互体验，实现类似 OpenAI/DeepSeek 的流畅对话效果，特别关注工具调用过程的实时可视化反馈。

### 1.2 设计决策总结

| 决策项 | 选择 | 说明 |
|--------|------|------|
| 核心架构 | 增强 SSE | 基于现有 SSE 架构扩展 |
| 工具进度 | 细粒度 | 支持中间进度回调 |
| 工具卡片位置 | 内嵌式 | 消息气泡内，可展开/收起（类似 ChatGPT）|
| 进度推送频率 | 节流 | 每秒最多一次 |
| 完成后展示 | 自动收起 | 显示"✓ 已完成"，点击展开 |
| 标签页同步 | 独立 | 各标签页独立状态 |

---

## 2. 架构

### 2.1 事件类型扩展

在现有 `StreamEvent` 基础上新增以下事件类型：

```typescript
type StreamEvent =
  // 现有类型
  | { type: 'start'; session_id: string }
  | { type: 'thinking' }
  | { type: 'tool_start'; name: string; arguments: Record<string, unknown> }
  | { type: 'tool_end'; name: string; arguments: Record<string, unknown>; result: string }
  | { type: 'claude_code_progress'; task_id: string; subtype: string; content: string; tool_name?: string; timestamp?: string }
  | { type: 'done'; content: string; assistantMessage: Message | null }
  | { type: 'error'; message: string }
  | { type: 'timeout' }
  // 新增类型
  | { type: 'tool_progress'; tool_id: string; status: 'running' | 'waiting'; detail: string; progress_percent?: number }
  | { type: 'tool_stream_chunk'; tool_id: string; chunk: string; is_error?: boolean }  // 工具输出流式分片

// 工具步骤数据结构更新
interface ToolStep {
  id: string;           // 唯一标识，用于关联 progress 事件
  name: string;
  arguments: Record<string, unknown> | string;
  result: string;
  status: 'pending' | 'running' | 'waiting' | 'completed' | 'error';
  progress?: {
    detail: string;
    percent?: number;
    lastUpdate: number;  // 用于节流控制
  };
  startTime?: number;
  endTime?: number;
  durationMs?: number;
  outputChunks?: Array<{ chunk: string; isError: boolean; timestamp: number }>;  // 流式输出缓冲
}
```

### 2.2 后端进度节流机制

```python
# 后端节流逻辑示例
import time

class ToolProgressThrottler:
    """工具进度节流器，确保每秒最多推送一次进度更新。"""

    def __init__(self, min_interval: float = 1.0):
        self.min_interval = min_interval
        self._last_push: dict[str, float] = {}

    def should_push(self, tool_id: str) -> bool:
        now = time.time()
        last = self._last_push.get(tool_id, 0)
        if now - last >= self.min_interval:
            self._last_push[tool_id] = now
            return True
        return False

    def push_progress(self, tool_id: str, detail: str, percent: int | None = None):
        if self.should_push(tool_id):
            # 通过 SSE 发送 tool_progress 事件
            self._emit_event({
                "type": "tool_progress",
                "tool_id": tool_id,
                "status": "running",
                "detail": detail,
                "progress_percent": percent
            })
```

### 2.3 后端 Agent Loop 集成

工具基类增加进度回调支持：

```python
# nanobot/agent/tools/base.py

from abc import ABC, abstractmethod
from typing import Any, Callable

class ToolProgressCallback(Protocol):
    """工具进度回调协议。"""

    def __call__(self, detail: str, percent: int | None = None) -> None: ...

class BaseTool(ABC):
    """工具基类，支持进度回调。"""

    def __init__(self, progress_callback: ToolProgressCallback | None = None):
        self._progress_callback = progress_callback
        self._throttler = ToolProgressThrottler(min_interval=1.0)

    def report_progress(self, detail: str, percent: int | None = None) -> None:
        """报告进度，会自动节流。"""
        if self._progress_callback and self._throttler.should_push(self.tool_id):
            self._progress_callback(detail, percent)

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """执行工具，子类可在执行过程中调用 report_progress。"""
        pass
```

---

## 3. 组件设计

### 3.1 前端组件结构

```
ChatPage
├── MessagesContainer
│   └── MessageBubble (assistant)
│       ├── ToolStepsPanel (新增/增强)
│       │   └── ToolStepCard (新增)
│       │       ├── ToolStepHeader (名称 + 状态图标)
│       │       ├── ToolStepProgress (进度条 + 详情)
│       │       └── ToolStepResult (展开后显示)
│       └── MarkdownContent
└── ChatInput
```

### 3.2 ToolStepCard 组件设计

```typescript
interface ToolStepCardProps {
  step: ToolStep;
  isExpanded: boolean;
  onToggle: () => void;
  isLast: boolean;  // 是否为最后一个步骤，用于显示运行动画
}

// 状态映射
const statusConfig = {
  pending: { icon: <ClockCircleOutlined />, color: '#8c8c8c', text: '等待中' },
  running: { icon: <LoadingOutlined />, color: '#1890ff', text: '运行中' },
  waiting: { icon: <PauseCircleOutlined />, color: '#faad14', text: '等待输入' },
  completed: { icon: <CheckCircleOutlined />, color: '#52c41a', text: '已完成' },
  error: { icon: <CloseCircleOutlined />, color: '#ff4d4f', text: '执行失败' },
};
```

### 3.3 展开/收起行为

- **默认状态**: 所有工具卡片收起，仅显示头部（图标 + 名称 + 状态）
- **运行中**: 自动展开当前运行的工具卡片
- **完成后**: 自动收起，显示"✓ 已完成"摘要
- **用户交互**: 点击头部可手动展开/收起

---

## 4. 数据流

### 4.1 工具调用完整流程

```
┌──────────┐    tool_start     ┌──────────┐
│  后端    │ ─────────────────> │  前端    │
│          │  {id, name, args}  │          │ ──> 创建 ToolStep，状态 pending
│          │                    │          │
│  工具    │  tool_progress    │          │
│  开始    │ ─────────────────> │          │
│  执行    │  (节流: 1次/秒)    │          │ ──> 更新状态为 running，显示进度
│          │                    │          │
│  工具    │  tool_stream_chunk│          │
│  输出    │ ─────────────────> │          │ ──> 追加输出片段到 outputChunks
│  (可选)  │  (如shell输出)     │          │
│          │                    │          │
│  工具    │  tool_end         │          │
│  完成    │ ─────────────────> │          │ ──> 更新状态为 completed，收起卡片
│          │  {id, result}      │          │
└──────────┘                    └──────────┘
```

### 4.2 前端状态管理

```typescript
// 每个会话独立的工具步骤状态
interface SessionToolState {
  steps: ToolStep[];
  activeStepId: string | null;  // 当前正在运行的工具ID
}

// 全局流式状态（保持不变，按会话ID索引）
const streamingStates = new Map<string, SessionToolState>();

// 事件处理逻辑
function handleStreamEvent(evt: StreamEvent) {
  switch (evt.type) {
    case 'tool_start':
      addToolStep(evt.id, evt.name, evt.arguments);
      break;
    case 'tool_progress':
      updateToolProgress(evt.tool_id, evt.detail, evt.progress_percent);
      break;
    case 'tool_stream_chunk':
      appendToolOutput(evt.tool_id, evt.chunk, evt.is_error);
      break;
    case 'tool_end':
      completeToolStep(evt.id, evt.result);
      break;
  }
}
```

---

## 5. 错误处理

### 5.1 后端错误场景

| 场景 | 处理方式 | 前端展示 |
|------|----------|----------|
| 工具执行超时 | 发送 tool_end + error 状态 | 显示"执行超时"，允许重试 |
| 工具执行异常 | 发送 tool_end + error 状态 | 显示错误信息，展开查看详情 |
| SSE 连接断开 | 前端自动重连 | 保留现有状态，重连后继续 |
| 进度推送失败 | 忽略，下次继续 | 用户感知不到 |

### 5.2 前端错误边界

```typescript
// 工具卡片错误边界
class ToolStepErrorBoundary extends React.Component {
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return <Alert message="工具显示出错" type="error" showIcon />;
    }
    return this.props.children;
  }
}
```

---

## 6. 性能优化

### 6.1 节流机制

- **后端**: 使用 `ToolProgressThrottler` 确保每秒最多推送一次进度更新
- **前端**: 对高频事件（如 tool_stream_chunk）使用 requestAnimationFrame 批量处理

### 6.2 渲染优化

```typescript
// ToolStepCard 使用 React.memo 避免不必要的重渲染
const ToolStepCard = React.memo<ToolStepCardProps>(
  ({ step, isExpanded, onToggle, isLast }) => {
    // 组件实现
  },
  // 自定义比较函数，仅比较关键字段
  (prev, next) => {
    return (
      prev.step.status === next.step.status &&
      prev.step.progress?.detail === next.step.progress?.detail &&
      prev.isExpanded === next.isExpanded
    );
  }
);
```

### 6.3 内存管理

- 工具步骤结果限制最大长度（如 10KB），超长截断并提示"内容已截断"
- 输出分片（outputChunks）限制数量（如最多 100 条），超出时合并旧数据

---

## 7. 测试策略

### 7.1 单元测试

```typescript
// 测试节流器
describe('ToolProgressThrottler', () => {
  it('should limit pushes to 1 per second', () => {
    const throttler = new ToolProgressThrottler(1000);
    expect(throttler.shouldPush('tool-1')).toBe(true);
    expect(throttler.shouldPush('tool-1')).toBe(false);
    jest.advanceTimersByTime(1000);
    expect(throttler.shouldPush('tool-1')).toBe(true);
  });
});

// 测试事件处理
describe('handleStreamEvent', () => {
  it('should create tool step on tool_start', () => {
    const state = { steps: [] };
    handleStreamEvent({ type: 'tool_start', id: 't1', name: 'shell', arguments: {} });
    expect(state.steps).toHaveLength(1);
    expect(state.steps[0].status).toBe('pending');
  });
});
```

### 7.2 集成测试

- 模拟长时运行工具，验证进度更新频率
- 模拟 SSE 断开重连，验证状态恢复
- 测试工具卡片展开/收起交互

---

## 8. 实现计划

### Phase 1: 后端基础 (2-3 天)
1. 实现 `ToolProgressThrottler` 节流器
2. 扩展 `StreamEvent` 类型定义
3. 修改 `BaseTool` 支持进度回调
4. 在 `AgentLoop` 中集成进度事件发射

### Phase 2: 前端基础 (2-3 天)
1. 更新 `ToolStep` 类型定义
2. 创建 `ToolStepCard` 组件
3. 增强 `ToolStepsPanel` 支持展开/收起
4. 修改事件处理器支持新事件类型

### Phase 3: 工具集成 (3-4 天)
1. 为常用工具添加进度回调（shell、claude_code、file_search 等）
2. 测试各工具的进度显示效果
3. 调整节流参数优化体验

### Phase 4: 优化打磨 (2 天)
1. 性能优化（memo、批量处理）
2. 边界情况处理
3. 完整测试覆盖

---

## 9. 附录

### 9.1 参考设计

- **OpenAI ChatGPT**: 工具调用卡片展开/收起交互
- **DeepSeek R1**: 思考过程可视化
- **Claude Code**: 终端式实时输出流

### 9.2 相关文件

| 文件 | 说明 |
|------|------|
| `nanobot/agent/tools/base.py` | 工具基类，需添加进度回调 |
| `nanobot/agent/loop.py` | Agent 循环，需集成进度事件 |
| `nanobot/web/api.py` | API 层，需扩展 SSE 事件类型 |
| `web-ui/src/types.ts` | 前端类型定义 |
| `web-ui/src/pages/ChatPage.tsx` | 主页面，需增强事件处理 |
| `web-ui/src/components/ToolStepsPanel.tsx` | 新建：工具步骤面板组件 |

---

## 10. 审核记录

| 审核人 | 日期 | 意见 | 状态 |
|--------|------|------|------|
| | | | |

---

**设计完成，等待审核确认。**
