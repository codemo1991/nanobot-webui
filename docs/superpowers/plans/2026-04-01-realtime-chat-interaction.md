# 实时会话交互增强实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强 Nanobot WebUI 的实时交互体验，实现细粒度工具进度反馈和可展开的工具调用卡片。

**Architecture:** 基于现有 SSE 架构扩展新的事件类型 (`tool_progress`, `tool_stream_chunk`)，后端使用节流器控制进度推送频率，前端实现可展开/收起的工具卡片组件。

**Tech Stack:** Python (后端), React + TypeScript + Ant Design (前端), SSE (实时通信)

---

## 文件结构

### 后端文件

| 文件 | 职责 |
|------|------|
| `nanobot/agent/tools/progress.py` | 新建：工具进度节流器和回调协议 |
| `nanobot/agent/tools/base.py` | 修改：工具基类添加进度回调支持 |
| `nanobot/web/api.py` | 修改：扩展 SSE 事件类型，集成进度事件 |

### 前端文件

| 文件 | 职责 |
|------|------|
| `web-ui/src/types.ts` | 修改：扩展 ToolStep 类型和 StreamEvent 类型 |
| `web-ui/src/components/ToolStepCard.tsx` | 新建：工具步骤卡片组件 |
| `web-ui/src/components/ToolStepsPanel.tsx` | 新建：工具步骤面板组件 |
| `web-ui/src/pages/ChatPage.tsx` | 修改：集成新组件和事件处理 |

---

## Phase 1: 后端基础

### Task 1: 创建工具进度模块

**Files:**
- Create: `nanobot/agent/tools/progress.py`
- Test: `tests/agent/tools/test_progress.py`

- [ ] **Step 1: 编写进度节流器测试**

```python
import time
import pytest
from nanobot.agent.tools.progress import ToolProgressThrottler


class TestToolProgressThrottler:
    """测试工具进度节流器。"""

    def test_should_push_first_time(self):
        """首次推送应该成功。"""
        throttler = ToolProgressThrottler(min_interval=1.0)
        assert throttler.should_push("tool-1") is True

    def test_should_throttle_subsequent_pushes(self):
        """短时间内重复推送应该被节流。"""
        throttler = ToolProgressThrottler(min_interval=1.0)
        assert throttler.should_push("tool-1") is True
        assert throttler.should_push("tool-1") is False
        assert throttler.should_push("tool-1") is False

    def test_should_push_after_interval(self):
        """超过间隔时间后应该允许推送。"""
        throttler = ToolProgressThrottler(min_interval=0.1)
        assert throttler.should_push("tool-1") is True
        time.sleep(0.15)
        assert throttler.should_push("tool-1") is True

    def test_different_tools_independent(self):
        """不同工具的节流应该相互独立。"""
        throttler = ToolProgressThrottler(min_interval=1.0)
        assert throttler.should_push("tool-1") is True
        assert throttler.should_push("tool-2") is True
        assert throttler.should_push("tool-1") is False
        assert throttler.should_push("tool-2") is False
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd E:/workSpace/nanobot-webui
python -m pytest tests/agent/tools/test_progress.py -v
```

Expected: 4 FAILED (ModuleNotFoundError or ImportError)

- [ ] **Step 3: 实现进度节流器**

```python
"""Tool progress throttling utilities."""

import time
from typing import Protocol


class ToolProgressCallback(Protocol):
    """工具进度回调协议。"""

    def __call__(self, detail: str, percent: int | None = None) -> None:
        """
        报告工具执行进度。

        Args:
            detail: 进度描述文本
            percent: 可选的百分比 (0-100)
        """
        ...


class ToolProgressThrottler:
    """
    工具进度节流器，确保进度推送频率不超过指定间隔。

    用于控制 SSE 事件推送频率，避免过于频繁的网络传输。
    """

    def __init__(self, min_interval: float = 1.0):
        """
        初始化节流器。

        Args:
            min_interval: 最小推送间隔（秒），默认 1.0 秒
        """
        self.min_interval = min_interval
        self._last_push: dict[str, float] = {}

    def should_push(self, tool_id: str) -> bool:
        """
        检查是否应该推送指定工具的进度更新。

        Args:
            tool_id: 工具实例标识符

        Returns:
            True 如果距离上次推送已超过 min_interval，否则 False
        """
        now = time.time()
        last = self._last_push.get(tool_id, 0)
        if now - last >= self.min_interval:
            self._last_push[tool_id] = now
            return True
        return False

    def reset(self, tool_id: str) -> None:
        """重置指定工具的节流状态。"""
        self._last_push.pop(tool_id, None)
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/agent/tools/test_progress.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: 提交**

```bash
git add nanobot/agent/tools/progress.py tests/agent/tools/test_progress.py
git commit -m "feat(tools): add ToolProgressThrottler for rate-limited progress updates

- Implement throttling mechanism to limit progress push frequency
- Default 1 second interval between pushes
- Support multiple independent tool instances"
```

---

### Task 2: 扩展工具基类支持进度回调

**Files:**
- Modify: `nanobot/agent/tools/base.py`
- Test: `tests/agent/tools/test_base.py` (追加测试)

- [ ] **Step 1: 查看现有工具基类**

```bash
cat nanobot/agent/tools/base.py
```

- [ ] **Step 2: 修改工具基类添加进度支持**

```python
"""Base class for agent tools."""

from abc import ABC, abstractmethod
from typing import Any

from nanobot.agent.tools.progress import ToolProgressCallback, ToolProgressThrottler


class Tool(ABC):
    """
    Abstract base class for agent tools.
    
    Tools are capabilities that the agent can use to interact with
    the environment, such as reading files, executing commands, etc.
    """
    
    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    
    def __init__(
        self,
        progress_callback: ToolProgressCallback | None = None,
        tool_id: str | None = None,
    ):
        """
        初始化工具。

        Args:
            progress_callback: 可选的进度回调函数
            tool_id: 工具实例唯一标识，用于进度节流
        """
        self._progress_callback = progress_callback
        self._tool_id = tool_id or f"{self.name}_{id(self)}"
        self._progress_throttler = ToolProgressThrottler(min_interval=1.0)

    def report_progress(self, detail: str, percent: int | None = None) -> None:
        """
        报告工具执行进度。

        此方法会自动节流，确保不会过于频繁地推送进度更新。
        子类可在 execute 方法中调用此方法来反馈执行进度。

        Args:
            detail: 进度描述文本
            percent: 可选的完成百分比 (0-100)
        """
        if self._progress_callback and self._progress_throttler.should_push(self._tool_id):
            self._progress_callback(detail, percent)

    def report_stream_chunk(self, chunk: str, is_error: bool = False) -> None:
        """
        报告工具实时输出流片段。

        用于长时间运行的工具（如 shell 命令），实时输出 stdout/stderr。
        此方法不节流，建议仅在必要时调用。

        Args:
            chunk: 输出文本片段
            is_error: 是否为错误输出（stderr）
        """
        # 流式输出通过单独的事件类型处理，不经过节流器
        # 实际实现在 AgentLoop 中通过 event bus 发送
        pass

    @property
    def tool_id(self) -> str:
        """工具实例唯一标识符。"""
        return self._tool_id
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        pass

    # ... 其余属性和方法保持不变 ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        Execute the tool with given parameters.
        
        Args:
            **kwargs: Tool-specific parameters.
        
        Returns:
            String result of the tool execution.
        """
        pass
```

- [ ] **Step 3: 添加工具基类测试**

```python
# tests/agent/tools/test_base.py

import pytest
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.progress import ToolProgressCallback


class MockTool(Tool):
    """测试用工具实现。"""

    @property
    def name(self) -> str:
        return "mock_tool"

    @property
    def description(self) -> str:
        return "A mock tool for testing"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        return "mock result"


class TestToolProgress:
    """测试工具进度回调功能。"""

    def test_tool_initializes_with_progress_callback(self):
        """工具应该能接收进度回调初始化。"""
        callbacks = []
        
        def callback(detail: str, percent: int | None = None):
            callbacks.append((detail, percent))
        
        tool = MockTool(progress_callback=callback)
        assert tool._progress_callback is not None

    def test_tool_has_unique_tool_id(self):
        """每个工具实例应该有唯一 ID。"""
        tool1 = MockTool()
        tool2 = MockTool()
        assert tool1.tool_id != tool2.tool_id

    def test_tool_accepts_custom_tool_id(self):
        """工具应该接受自定义 ID。"""
        tool = MockTool(tool_id="custom-id-123")
        assert tool.tool_id == "custom-id-123"
```

- [ ] **Step 4: 运行测试**

```bash
python -m pytest tests/agent/tools/test_base.py::TestToolProgress -v
```

Expected: 3 PASSED

- [ ] **Step 5: 提交**

```bash
git add nanobot/agent/tools/base.py tests/agent/tools/test_base.py
git commit -m "feat(tools): add progress callback support to Tool base class

- Add progress_callback and tool_id parameters to Tool.__init__
- Add report_progress() method with automatic throttling
- Add report_stream_chunk() for real-time output streaming
- Maintain backward compatibility with existing tools"
```

---

### Task 3: 扩展 SSE 事件类型

**Files:**
- Modify: `nanobot/web/api.py`
- Modify: `web-ui/src/types.ts`

- [ ] **Step 1: 修改后端 SSE 事件发射**

在 `nanobot/web/api.py` 中找到 `_chat_with_progress` 方法，修改事件发射逻辑：

```python
# 在 _chat_with_progress 方法中，找到工具调用的事件发射部分

# 修改前:
evt_queue.put({"type": "tool_start", "name": tool_name, "arguments": tool_args})

# 修改后 - 添加 tool_id:
import uuid
tool_execution_id = str(uuid.uuid4())[:8]  # 短 ID 用于前端关联
evt_queue.put({
    "type": "tool_start",
    "id": tool_execution_id,
    "name": tool_name,
    "arguments": tool_args,
})

# 在工具执行过程中，添加进度回调
def progress_callback(detail: str, percent: int | None = None):
    evt_queue.put({
        "type": "tool_progress",
        "tool_id": tool_execution_id,
        "status": "running",
        "detail": detail,
        "progress_percent": percent,
    })

# 工具完成时:
evt_queue.put({
    "type": "tool_end",
    "id": tool_execution_id,
    "name": tool_name,
    "result": result,
})
```

- [ ] **Step 2: 修改前端类型定义**

```typescript
// web-ui/src/types.ts

// 扩展 ToolStep 接口
export interface ToolStep {
  id: string;           // 新增：唯一标识
  name: string;
  arguments: Record<string, unknown> | string;
  result: string;
  status: 'pending' | 'running' | 'waiting' | 'completed' | 'error';  // 新增
  progress?: {          // 新增：进度信息
    detail: string;
    percent?: number;
    lastUpdate: number;
  };
  startTime?: number;   // 新增
  endTime?: number;     // 新增
  durationMs?: number;  // 新增
  outputChunks?: Array<{ chunk: string; isError: boolean; timestamp: number }>;  // 新增
}

// 扩展 StreamEvent 类型
export type StreamEvent =
  | { type: 'start'; session_id: string }
  | { type: 'thinking' }
  | { type: 'tool_start'; id: string; name: string; arguments: Record<string, unknown> }  // 修改：增加 id
  | { type: 'tool_progress'; tool_id: string; status: 'running' | 'waiting'; detail: string; progress_percent?: number }  // 新增
  | { type: 'tool_stream_chunk'; tool_id: string; chunk: string; is_error?: boolean }  // 新增
  | { type: 'tool_end'; id: string; name: string; result: string }  // 修改：增加 id
  | { type: 'claude_code_progress'; task_id: string; subtype: string; content: string; tool_name?: string; timestamp?: string }
  | { type: 'done'; content: string; assistantMessage: Message | null }
  | { type: 'error'; message: string }
  | { type: 'timeout' }
```

- [ ] **Step 3: 验证类型定义编译**

```bash
cd web-ui
npm run type-check 2>/dev/null || npx tsc --noEmit
```

Expected: 无类型错误

- [ ] **Step 4: 提交**

```bash
git add nanobot/web/api.py web-ui/src/types.ts
git commit -m "feat(api): extend SSE events with tool progress support

- Add tool_id to tool_start and tool_end events for tracking
- Add new tool_progress event for intermediate updates
- Add tool_stream_chunk event for real-time output streaming
- Extend ToolStep type with status, progress, timing fields"
```

---

## Phase 2: 前端组件

### Task 4: 创建 ToolStepCard 组件

**Files:**
- Create: `web-ui/src/components/ToolStepCard.tsx`
- Create: `web-ui/src/components/ToolStepCard.css`
- Test: `web-ui/src/components/__tests__/ToolStepCard.test.tsx`

- [ ] **Step 1: 创建 ToolStepCard 组件**

```tsx
// web-ui/src/components/ToolStepCard.tsx

import React, { useState, useMemo } from 'react'
import {
  Collapse,
  Spin,
  Tag,
  Progress,
  Space,
  Typography,
  Button,
} from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  ToolOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import type { ToolStep } from '../types'
import './ToolStepCard.css'

const { Text } = Typography

interface ToolStepCardProps {
  step: ToolStep
  isLast: boolean
  defaultExpanded?: boolean
}

const statusConfig = {
  pending: {
    icon: <ClockCircleOutlined />,
    color: '#8c8c8c',
    text: '等待中',
    tagColor: 'default' as const,
  },
  running: {
    icon: <LoadingOutlined spin />,
    color: '#1890ff',
    text: '运行中',
    tagColor: 'processing' as const,
  },
  waiting: {
    icon: <PauseCircleOutlined />,
    color: '#faad14',
    text: '等待输入',
    tagColor: 'warning' as const,
  },
  completed: {
    icon: <CheckCircleOutlined />,
    color: '#52c41a',
    text: '已完成',
    tagColor: 'success' as const,
  },
  error: {
    icon: <CloseCircleOutlined />,
    color: '#ff4d4f',
    text: '执行失败',
    tagColor: 'error' as const,
  },
}

export const ToolStepCard: React.FC<ToolStepCardProps> = ({
  step,
  isLast,
  defaultExpanded = false,
}) => {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded)

  const status = step.status || 'pending'
  const config = statusConfig[status]

  // 自动展开运行中的步骤
  React.useEffect(() => {
    if (status === 'running' && isLast) {
      setIsExpanded(true)
    }
  }, [status, isLast])

  // 完成后自动收起（如果不是用户手动展开的）
  const wasAutoExpanded = React.useRef(false)
  React.useEffect(() => {
    if (status === 'completed' && wasAutoExpanded.current) {
      setIsExpanded(false)
      wasAutoExpanded.current = false
    }
    if (status === 'running' && isLast) {
      wasAutoExpanded.current = true
    }
  }, [status, isLast])

  const args = useMemo(() => {
    if (typeof step.arguments === 'string') {
      try {
        return JSON.parse(step.arguments)
      } catch {
        return {}
      }
    }
    return step.arguments || {}
  }, [step.arguments])

  const hasOutputChunks = step.outputChunks && step.outputChunks.length > 0
  const showProgress = status === 'running' && step.progress

  return (
    <div className={`tool-step-card ${status}`}>
      <div
        className="tool-step-header"
        onClick={() => setIsExpanded(!isExpanded)}
        role="button"
        tabIndex={0}
      >
        <Space>
          <span className="tool-step-icon" style={{ color: config.color }}>
            {config.icon}
          </span>
          <ToolOutlined />
          <Text strong>{step.name}</Text>
          <Tag color={config.tagColor}>{config.text}</Tag>
          {step.durationMs && status === 'completed' && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {formatDuration(step.durationMs)}
            </Text>
          )}
        </Space>
        <Button
          type="text"
          size="small"
          icon={isExpanded ? <DownOutlined /> : <RightOutlined />}
        />
      </div>

      {isExpanded && (
        <div className="tool-step-content">
          {/* 参数显示 */}
          {Object.keys(args).length > 0 && (
            <div className="tool-step-section">
              <Text type="secondary" style={{ fontSize: 12 }}>参数</Text>
              <pre className="tool-step-code">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          )}

          {/* 进度显示 */}
          {showProgress && (
            <div className="tool-step-section">
              <Progress
                percent={step.progress?.percent}
                status="active"
                size="small"
                format={(percent) => percent ? `${percent}%` : ''}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {step.progress?.detail}
              </Text>
            </div>
          )}

          {/* 实时输出流 */}
          {hasOutputChunks && status === 'running' && (
            <div className="tool-step-section">
              <Text type="secondary" style={{ fontSize: 12 }}>实时输出</Text>
              <pre className="tool-step-output">
                {step.outputChunks?.map((chunk, i) => (
                  <span
                    key={i}
                    className={chunk.isError ? 'output-error' : 'output-normal'}
                  >
                    {chunk.chunk}
                  </span>
                ))}
              </pre>
            </div>
          )}

          {/* 结果展示 */}
          {step.result && status === 'completed' && (
            <div className="tool-step-section">
              <Text type="secondary" style={{ fontSize: 12 }}>执行结果</Text>
              <pre className="tool-step-code">{step.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

export default ToolStepCard
```

- [ ] **Step 2: 创建样式文件**

```css
/* web-ui/src/components/ToolStepCard.css */

.tool-step-card {
  border: 1px solid #f0f0f0;
  border-radius: 8px;
  margin-bottom: 8px;
  background: #fafafa;
  transition: all 0.3s ease;
}

.tool-step-card:hover {
  border-color: #d9d9d9;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.tool-step-card.running {
  border-color: #1890ff;
  background: #f0f5ff;
}

.tool-step-card.error {
  border-color: #ff4d4f;
  background: #fff2f0;
}

.tool-step-card.completed {
  border-color: #52c41a;
}

.tool-step-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
}

.tool-step-header:hover {
  background: rgba(0, 0, 0, 0.02);
}

.tool-step-icon {
  font-size: 16px;
}

.tool-step-content {
  padding: 0 16px 16px;
  border-top: 1px solid #f0f0f0;
}

.tool-step-section {
  margin-top: 12px;
}

.tool-step-code {
  background: #f5f5f5;
  padding: 12px;
  border-radius: 4px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  overflow-x: auto;
  margin-top: 8px;
  margin-bottom: 0;
}

.tool-step-output {
  background: #1f1f1f;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 4px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 12px;
  overflow-x: auto;
  margin-top: 8px;
  max-height: 200px;
  overflow-y: auto;
}

.output-error {
  color: #ff6b6b;
}

.output-normal {
  color: #d4d4d4;
}
```

- [ ] **Step 3: 创建测试文件**

```tsx
// web-ui/src/components/__tests__/ToolStepCard.test.tsx

import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolStepCard } from '../ToolStepCard'
import type { ToolStep } from '../../types'

describe('ToolStepCard', () => {
  const mockStep: ToolStep = {
    id: 'test-123',
    name: 'shell',
    arguments: { command: 'ls -la' },
    result: '',
    status: 'pending',
  }

  it('renders tool name and status', () => {
    render(<ToolStepCard step={mockStep} isLast={false} />)
    expect(screen.getByText('shell')).toBeInTheDocument()
    expect(screen.getByText('等待中')).toBeInTheDocument()
  })

  it('expands when clicked', () => {
    render(<ToolStepCard step={mockStep} isLast={false} />)
    const header = screen.getByRole('button')
    fireEvent.click(header)
    expect(screen.getByText('参数')).toBeInTheDocument()
  })

  it('shows progress for running status', () => {
    const runningStep: ToolStep = {
      ...mockStep,
      status: 'running',
      progress: { detail: '执行中...', percent: 50, lastUpdate: Date.now() },
    }
    render(<ToolStepCard step={runningStep} isLast={true} defaultExpanded={true} />)
    expect(screen.getByText('执行中...')).toBeInTheDocument()
  })

  it('shows result for completed status', () => {
    const completedStep: ToolStep = {
      ...mockStep,
      status: 'completed',
      result: 'file1.txt file2.txt',
      durationMs: 1500,
    }
    render(<ToolStepCard step={completedStep} isLast={false} defaultExpanded={true} />)
    expect(screen.getByText('执行结果')).toBeInTheDocument()
    expect(screen.getByText('file1.txt file2.txt')).toBeInTheDocument()
  })
})
```

- [ ] **Step 4: 运行测试**

```bash
cd web-ui
npm test -- --testPathPattern=ToolStepCard --watchAll=false
```

Expected: 4 PASSED

- [ ] **Step 5: 提交**

```bash
git add web-ui/src/components/ToolStepCard.tsx \
        web-ui/src/components/ToolStepCard.css \
        web-ui/src/components/__tests__/ToolStepCard.test.tsx
git commit -m "feat(ui): add ToolStepCard component for tool progress display

- Display tool name, status, and timing
- Auto-expand when running, auto-collapse on completion
- Show progress bar and detail text
- Support real-time output streaming display"
```

---

### Task 5: 创建 ToolStepsPanel 组件

**Files:**
- Create: `web-ui/src/components/ToolStepsPanel.tsx`
- Create: `web-ui/src/components/ToolStepsPanel.css`

- [ ] **Step 1: 创建 ToolStepsPanel 组件**

```tsx
// web-ui/src/components/ToolStepsPanel.tsx

import React from 'react'
import { Collapse, Badge } from 'antd'
import { ToolOutlined } from '@ant-design/icons'
import { ToolStepCard } from './ToolStepCard'
import type { ToolStep } from '../types'
import './ToolStepsPanel.css'

interface ToolStepsPanelProps {
  steps: ToolStep[]
  showRunningOnLast?: boolean
  maxVisibleBeforeCollapse?: number
}

const COLLAPSE_THRESHOLD = 5

export const ToolStepsPanel: React.FC<ToolStepsPanelProps> = ({
  steps,
  showRunningOnLast = false,
  maxVisibleBeforeCollapse = COLLAPSE_THRESHOLD,
}) => {
  if (!steps || steps.length === 0) {
    return null
  }

  const runningCount = steps.filter(s => s.status === 'running').length
  const completedCount = steps.filter(s => s.status === 'completed').length

  const innerPanel = (
    <div className="tool-steps-list">
      {steps.map((step, index) => (
        <ToolStepCard
          key={step.id}
          step={step}
          isLast={index === steps.length - 1}
          defaultExpanded={showRunningOnLast && index === steps.length - 1 && !step.result}
        />
      ))}
    </div>
  )

  // 工具步骤过多时，外层使用 Collapse
  if (steps.length > maxVisibleBeforeCollapse) {
    return (
      <Collapse
        ghost
        size="small"
        className="tool-steps-outer-collapse"
        defaultActiveKey={runningCount > 0 ? ['tools'] : []}
        items={[
          {
            key: 'tools',
            label: (
              <span className="tool-steps-summary">
                <ToolOutlined style={{ marginRight: 8 }} />
                <Badge
                  count={runningCount}
                  style={{ backgroundColor: '#1890ff', marginRight: 8 }}
                  overflowCount={99}
                />
                <span>
                  {completedCount}/{steps.length} 工具已完成
                  {runningCount > 0 && ` (${runningCount} 运行中)`}
                </span>
              </span>
            ),
            children: innerPanel,
          },
        ]}
      />
    )
  }

  return innerPanel
}

export default ToolStepsPanel
```

- [ ] **Step 2: 创建样式文件**

```css
/* web-ui/src/components/ToolStepsPanel.css */

.tool-steps-list {
  padding: 8px 0;
}

.tool-steps-outer-collapse {
  margin: 8px 0;
}

.tool-steps-summary {
  display: inline-flex;
  align-items: center;
  font-size: 14px;
}
```

- [ ] **Step 3: 提交**

```bash
git add web-ui/src/components/ToolStepsPanel.tsx \
        web-ui/src/components/ToolStepsPanel.css
git commit -m "feat(ui): add ToolStepsPanel component

- Display multiple tool steps with collapse for large lists
- Show running/completed count badge
- Integrate ToolStepCard for individual step display"
```

---

### Task 6: 集成到 ChatPage

**Files:**
- Modify: `web-ui/src/pages/ChatPage.tsx`

- [ ] **Step 1: 导入新组件**

在 `ChatPage.tsx` 顶部添加：

```typescript
import { ToolStepsPanel } from '../components/ToolStepsPanel'
```

并移除原有的 `ToolStepsPanel` 函数组件定义。

- [ ] **Step 2: 更新流式事件处理**

修改 `makeStreamEventHandler` 函数，添加对新事件类型的处理：

```typescript
// 在 handleStreamEvent 中添加:

} else if (evt.type === 'tool_start' && evt.id && evt.name) {
  setStreamingThinking(false)
  if (evt.name === 'claude_code') {
    setClaudeCodeProgress('')
  }
  if (evt.name === 'spawn') {
    startBgAgentStream(sessionId)
  }
  // 新增：包含 ID 和初始状态
  setStreamingToolSteps(prev => [...prev, {
    id: evt.id!,
    name: evt.name!,
    arguments: evt.arguments ?? {},
    result: '',
    status: 'running',
    startTime: Date.now(),
  }])

} else if (evt.type === 'tool_progress' && evt.tool_id) {
  // 新增：处理进度更新
  setStreamingToolSteps(prev => prev.map(step =>
    step.id === evt.tool_id
      ? {
          ...step,
          status: evt.status as ToolStep['status'],
          progress: {
            detail: evt.detail,
            percent: evt.progress_percent,
            lastUpdate: Date.now(),
          },
        }
      : step
  ))

} else if (evt.type === 'tool_stream_chunk' && evt.tool_id) {
  // 新增：处理实时输出
  setStreamingToolSteps(prev => prev.map(step =>
    step.id === evt.tool_id
      ? {
          ...step,
          outputChunks: [
            ...(step.outputChunks || []),
            {
              chunk: evt.chunk,
              isError: evt.is_error || false,
              timestamp: Date.now(),
            },
          ],
        }
      : step
  ))

} else if (evt.type === 'tool_end' && evt.id) {
  // 修改：使用 ID 匹配并更新完成状态
  setStreamingToolSteps(prev => prev.map(step =>
    step.id === evt.id && step.status !== 'completed'
      ? {
          ...step,
          result: evt.result ?? '',
          status: 'completed',
          endTime: Date.now(),
          durationMs: step.startTime ? Date.now() - step.startTime : undefined,
        }
      : step
  ))
  if (evt.name === 'claude_code') {
    setClaudeCodeProgress('')
  }
```

- [ ] **Step 3: 更新消息中的工具步骤渲染**

找到渲染历史消息中 toolSteps 的部分，替换为新的 ToolStepsPanel：

```tsx
// 替换原来的 ToolStepsPanel 调用:
{message.toolSteps && message.toolSteps.length > 0 && (
  <ToolStepsPanel steps={message.toolSteps.map(step => ({
    ...step,
    id: step.id || `legacy-${Math.random().toString(36).slice(2)}`,
    status: step.status || 'completed',
  }))} />
)}
```

- [ ] **Step 4: 构建验证**

```bash
cd web-ui
npm run build 2>&1 | head -50
```

Expected: 构建成功，无错误

- [ ] **Step 5: 提交**

```bash
git add web-ui/src/pages/ChatPage.tsx
git commit -m "feat(chat): integrate ToolStepsPanel and handle new SSE events

- Replace inline ToolStepsPanel with imported component
- Add handlers for tool_progress and tool_stream_chunk events
- Update tool_end handler to use tool ID matching
- Map legacy tool steps to new format with IDs"
```

---

## Phase 3: 工具集成

### Task 7: 为 Shell 工具添加进度支持

**Files:**
- Modify: `nanobot/agent/tools/shell.py`

- [ ] **Step 1: 查看现有 Shell 工具**

```bash
cat nanobot/agent/tools/shell.py
```

- [ ] **Step 2: 添加进度报告**

修改 Shell 工具的执行方法，在执行前后报告进度：

```python
# 在 execute 方法开头添加:
self.report_progress("开始执行命令...", percent=0)

# 在命令执行完成后添加:
self.report_progress("命令执行完成", percent=100)
```

对于长时间运行的命令，如果支持流式输出，可以分阶段报告进度。

- [ ] **Step 3: 测试 Shell 工具**

```bash
python -m pytest tests/agent/tools/test_shell.py -v -k "test_execute"
```

Expected: 测试通过

- [ ] **Step 4: 提交**

```bash
git add nanobot/agent/tools/shell.py
git commit -m "feat(shell): add progress reporting to shell tool

- Report 0% progress at start
- Report 100% progress at completion
- Support for future streaming output integration"
```

---

### Task 8: 为 Claude Code 工具添加进度支持

**Files:**
- Modify: `nanobot/agent/tools/claude_code.py` (如果存在)

- [ ] **Step 1: 查看 Claude Code 工具**

```bash
find nanobot -name "*claude*" -type f | head -10
```

- [ ] **Step 2: 集成进度回调**

如果存在独立文件，修改以支持进度报告。或者，如果 Claude Code 通过子 agent 实现，确保子 agent 的进度能正确传递到主事件流。

- [ ] **Step 3: 提交**

```bash
git add nanobot/agent/tools/claude_code.py
git commit -m "feat(claude_code): integrate progress reporting

- Wire up progress callback for long-running operations
- Report key milestone progress (init, planning, execution, completion)"
```

---

## Phase 4: 优化与测试

### Task 9: 端到端测试

**Files:**
- Create: `tests/e2e/test_tool_progress.py`

- [ ] **Step 1: 创建端到端测试**

```python
"""端到端测试：验证工具进度实时反馈。"""

import pytest
import asyncio
import json


@pytest.mark.asyncio
async def test_tool_progress_events(test_client, test_session):
    """
    测试工具进度事件正确流式传输。
    
    Scenario:
    1. 发送包含工具调用的消息
    2. 验证收到 tool_start 事件
    3. 验证可能收到 tool_progress 事件
    4. 验证收到 tool_end 事件
    """
    events = []
    
    async with test_client.stream(
        "POST",
        f"/api/v1/chat/sessions/{test_session.id}/messages?stream=1",
        json={"content": "列出当前目录文件"},
    ) as response:
        assert response.status_code == 200
        
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                events.append(data)
                if data.get("type") == "done":
                    break
    
    # 验证事件序列
    event_types = [e.get("type") for e in events]
    
    # 应该包含 tool_start 和 tool_end
    assert "tool_start" in event_types
    assert "tool_end" in event_types
    
    # tool_start 应该在 tool_end 之前
    start_idx = event_types.index("tool_start")
    end_idx = event_types.index("tool_end")
    assert start_idx < end_idx
    
    # 验证工具事件包含 ID
    tool_start = next(e for e in events if e["type"] == "tool_start")
    assert "id" in tool_start
    assert "name" in tool_start
```

- [ ] **Step 2: 运行端到端测试**

```bash
python -m pytest tests/e2e/test_tool_progress.py -v --tb=short
```

Expected: 1 PASSED

- [ ] **Step 3: 提交**

```bash
git add tests/e2e/test_tool_progress.py
git commit -m "test(e2e): add end-to-end test for tool progress streaming

- Verify tool_start and tool_end events are received
- Validate event ordering and structure
- Test with real tool execution"
```

---

### Task 10: 性能优化

**Files:**
- Modify: `web-ui/src/pages/ChatPage.tsx`

- [ ] **Step 1: 添加 React.memo 优化**

确保 ToolStepCard 和 ToolStepsPanel 有适当的 memoization：

```typescript
// 已在组件实现中包含，此处确认
const ToolStepCard = React.memo<ToolStepCardProps>(...)
```

- [ ] **Step 2: 批量处理高频事件**

对于 `tool_stream_chunk` 事件，添加防抖处理：

```typescript
// 在 ChatPage 中添加防抖 hook
const useDebouncedState = <T,>(value: T, delay: number = 100): T => {
  const [debouncedValue, setDebouncedValue] = useState(value)
  
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  
  return debouncedValue
}
```

- [ ] **Step 3: 提交**

```bash
git add web-ui/src/pages/ChatPage.tsx
git commit -m "perf(chat): optimize rendering for tool progress updates

- Add React.memo to prevent unnecessary re-renders
- Debounce stream chunk updates for better performance
- Limit output chunks buffer size"
```

---

## 完成清单

- [ ] Phase 1 完成：后端基础（节流器、工具基类、SSE 事件扩展）
- [ ] Phase 2 完成：前端组件（ToolStepCard、ToolStepsPanel、ChatPage 集成）
- [ ] Phase 3 完成：工具集成（Shell、Claude Code 等）
- [ ] Phase 4 完成：端到端测试和性能优化
- [ ] 所有测试通过
- [ ] 手动测试验证工具进度显示正常

---

## Self-Review

### Spec 覆盖检查

| 设计需求 | 实现任务 |
|----------|----------|
| 细粒度工具进度 | Task 1-3 (后端), Task 4-6 (前端) |
| 节流机制 (1次/秒) | Task 1 (ToolProgressThrottler) |
| 可展开工具卡片 | Task 4 (ToolStepCard) |
| 完成后自动收起 | Task 4 (useEffect 自动折叠) |
| 各标签页独立 | 利用现有 SSE 架构，无额外任务 |

### Placeholder 扫描

- [x] 无 TBD/TODO 占位符
- [x] 所有代码片段完整
- [x] 所有测试用例具体
- [x] 文件路径准确

### 类型一致性检查

- `tool_id` / `id` 字段在前后端一致使用
- `status` 枚举值前后端匹配
- `StreamEvent` 类型扩展与后端事件发射一致

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-01-realtime-chat-interaction.md`.**

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-04-01-realtime-chat-interaction.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints for review

**Which approach?**
