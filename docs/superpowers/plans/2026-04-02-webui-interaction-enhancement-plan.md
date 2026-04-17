# WebUI 聊天交互体验增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强聊天流式反馈可感知性 + 工具调用过程透明化。

**Architecture:** 渐进增强 CSS + UI 状态增量，不改 SSE 事件处理逻辑，不动后端。

**Tech Stack:** React 18 + TypeScript + Ant Design + CSS animations

---

### Task 1: ToolStepCard.css — 动画 + 新样式

**Files:**
- Modify: `web-ui/src/components/ToolStepCard.css`

- [ ] **Step 1: Append animation keyframes to ToolStepCard.css**

打开 `ToolStepCard.css`，在文件末尾追加以下内容：

```css
/* ── Animation keyframes ──────────────────────────────── */
@keyframes pulse-dot {
  0%, 100% { opacity: 0.4; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}

@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

@keyframes output-grow {
  from { opacity: 0.5; }
  to { opacity: 1; }
}

/* ── Status badge ──────────────────────────────────────── */
.tool-status-badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
  margin-left: auto;
  flex-shrink: 0;
}
.tool-status-badge.running {
  background: #eff6ff;
  color: #3b82f6;
}
.tool-status-badge.done {
  background: #ecfdf5;
  color: #10b981;
}
.tool-status-badge.error {
  background: #fef2f2;
  color: #ef4444;
}

/* ── Output preview ───────────────────────────────────── */
.tool-output-wrap {
  position: relative;
}
.tool-output-collapsed {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  animation: output-grow 0.15s ease-out;
}
.tool-output-expanded {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 400px;
  overflow-y: auto;
}
.output-hidden-anchor {
  background: #fffbeb;
  color: #92400e;
  font-size: 11px;
  padding: 3px 12px;
  cursor: pointer;
  border-top: 1px solid #fde68a;
}
.output-hidden-anchor:hover {
  background: #fef3c7;
}
.output-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
}
.output-action-btn {
  background: none;
  border: none;
  color: #3b82f6;
  cursor: pointer;
  padding: 0;
  font-size: 11px;
  line-height: 1;
}
.output-action-btn:hover {
  text-decoration: underline;
}
.output-action-btn.copied {
  color: #10b981;
}

/* ── Params section ───────────────────────────────────── */
.tool-params-section {
  margin-top: 8px;
}
.params-toggle-btn {
  background: none;
  border: none;
  color: #9ca3af;
  cursor: pointer;
  font-size: 11px;
  padding: 0;
  line-height: 1;
}
.params-toggle-btn:hover {
  color: #6b7280;
}
.tool-params-body {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  padding: 6px 10px;
  margin-top: 4px;
  font-size: 11px;
}
.param-row {
  display: flex;
  gap: 4px;
  line-height: 1.7;
}
.param-key {
  color: #6b7280;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  flex-shrink: 0;
  user-select: none;
}
.param-value {
  color: #111827;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 280px;
}
.param-value.overflow {
  cursor: pointer;
}
.param-value.overflow:hover {
  color: #3b82f6;
}
.output-error {
  color: #ff6b6b;
}
.output-normal {
  color: #d4d4d4;
}
```

- [ ] **Step 2: Verify CSS syntax**

检查追加的样式无语法错误（keyframes、选择器、大括号配对）。

- [ ] **Step 3: Commit**

```bash
git add web-ui/src/components/ToolStepCard.css
git commit -m "feat(ui): add tool card animation and status badge CSS"
```

---

### Task 2: ToolStepCard — 运行时计时 + 完成/错误徽章

**Files:**
- Modify: `web-ui/src/components/ToolStepCard.tsx`

- [ ] **Step 1: Read the current ToolStepCard.tsx to confirm exact code**

确认第 73 行 `const [isExpanded, setIsExpanded] = useState(defaultExpanded)` 的精确内容。

- [ ] **Step 2: Add runningSeconds state and timerRef below existing useState**

在 `const [isExpanded, setIsExpanded] = useState(defaultExpanded)` 之后插入：

```typescript
  const [runningSeconds, setRunningSeconds] = useState(0)
  const timerRef = React.useRef<ReturnType<typeof setInterval> | null>(null)

  React.useEffect(() => {
    if (step.status === 'running') {
      const start = step.startTime || Date.now()
      setRunningSeconds(0)
      timerRef.current = setInterval(() => {
        setRunningSeconds((Date.now() - start) / 1000)
      }, 100)
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [step.status, step.startTime])
```

**注意**：`step.startTime` 已在 deps 中，因为计时器需要从正确的起点开始。

- [ ] **Step 3: Compute durationStr and statusBadge below the status config lookup**

在 `const config = statusConfig[status]` 之后（约第 76 行）插入：

```typescript
  const durationMs = step.endTime && step.startTime ? step.endTime - step.startTime : 0
  const durationStr = durationMs > 0
    ? durationMs < 1000
      ? `${durationMs}ms`
      : durationMs < 60000
        ? `${(durationMs / 1000).toFixed(1)}s`
        : `${(durationMs / 60000).toFixed(1)}min`
    : null

  const StatusBadge = () => {
    if (status === 'running') {
      return (
        <span className="tool-status-badge running">
          运行中 {runningSeconds.toFixed(1)}s
        </span>
      )
    }
    if (status === 'completed') {
      return (
        <span className="tool-status-badge done">
          ✅ Done {durationStr ?? '--'}
        </span>
      )
    }
    if (status === 'error') {
      return (
        <span className="tool-status-badge error">
          ❌ Error {durationStr ?? '--'}
        </span>
      )
    }
    return null
  }
```

- [ ] **Step 4: Replace the header Tag + duration Text with StatusBadge()**

找到 header 区域（约第 130-135 行）：

```tsx
// 原来：
          <Tag color={config.tagColor}>{config.text}</Tag>
          {step.durationMs && status === 'completed' && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {formatDuration(step.durationMs)}
            </Text>
          )}
```

替换为：

```tsx
// 替换为：
          <StatusBadge />
```

同时在 import 中删除 `Tag`（如果不再使用），删除 `formatDuration` 函数引用（已被内联替代）。

- [ ] **Step 5: Update memo comparison to handle runningSeconds**

找到文件末尾的 memo 比较函数（约第 199-218 行），在比较条件中加上 `runningSeconds`：

```typescript
}, (prevProps, nextProps) => {
  const prev = prevProps.step
  const next = nextProps.step
  return (
    prev.id === next.id &&
    prev.status === next.status &&
    prev.result === next.result &&
    prev.name === next.name &&
    prev.durationMs === next.durationMs &&
    prev.startTime === next.startTime &&
    prev.endTime === next.endTime &&
    prev.progress?.detail === next.progress?.detail &&
    prev.progress?.percent === next.progress?.percent &&
    Math.abs((prev.outputChunks?.length ?? 0) - (next.outputChunks?.length ?? 0)) < 10 &&
    prevProps.isLast === nextProps.isLast &&
    prevProps.defaultExpanded === nextProps.defaultExpanded
  )
})
```

**注意**：`runningSeconds` 不加入 memo deps，因为计时器更新频繁，由 interval 自己驱动，不触发父组件 re-render，所以不需要参与 memo 比较。

- [ ] **Step 6: Run visual verification**

```bash
cd web-ui && npm run build 2>&1 | head -30
```
Expected: 无 TypeScript 编译错误。

- [ ] **Step 7: Commit**

```bash
git add web-ui/src/components/ToolStepCard.tsx
git commit -m "feat(ui): add running timer and done/error badges to tool card"
```

---

### Task 3: ToolStepCard — 输出折叠预览 + 操作按钮

**Files:**
- Modify: `web-ui/src/components/ToolStepCard.tsx`

- [ ] **Step 1: Add outputExpanded state and PREVIEW_LINES constant**

在文件顶部 `MAX_OUTPUT_CHUNKS` 附近添加：

```typescript
const PREVIEW_LINES = 10
```

在 `const [isExpanded, setIsExpanded] = useState(defaultExpanded)` 之后添加：

```typescript
  const [outputExpanded, setOutputExpanded] = useState(false)
  const [copied, setCopied] = useState(false)
```

- [ ] **Step 2: Add handleCopy function inside component body**

在 `useMemo` 块之后（约第 107 行）添加：

```typescript
  const handleCopy = async () => {
    const text = step.result || ''
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard not available
    }
  }

  // Reset outputExpanded when step.result changes (new result)
  React.useEffect(() => {
    setOutputExpanded(false)
  }, [step.result])
```

- [ ] **Step 3: Compute output preview values**

在 `const showProgress = status === 'running' && step.progress` 之后添加：

```typescript
  const resultLines = (step.result || '').split('\n')
  const needsOutputCollapse = resultLines.length > PREVIEW_LINES
  const outputPreview = needsOutputCollapse && !outputExpanded
    ? resultLines.slice(0, PREVIEW_LINES).join('\n')
    : step.result || ''
  const hiddenCount = resultLines.length - PREVIEW_LINES
```

- [ ] **Step 4: Replace result section with collapsible output**

找到 `{step.result && status === 'completed' && (` 区块（约第 189-194 行），替换为：

```tsx
          {/* 结果展示 — 折叠预览 + 操作按钮 */}
          {step.result && status === 'completed' && (
            <div className="tool-step-section">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>执行结果</Text>
                <div className="output-actions">
                  {needsOutputCollapse && !outputExpanded && (
                    <button className="output-action-btn" onClick={() => setOutputExpanded(true)}>
                      [展开全部 {resultLines.length} 行]
                    </button>
                  )}
                  {outputExpanded && (
                    <button className="output-action-btn" onClick={() => setOutputExpanded(false)}>
                      [收起]
                    </button>
                  )}
                  <button className={`output-action-btn ${copied ? 'copied' : ''}`} onClick={handleCopy}>
                    {copied ? '[已复制 ✓]' : '[复制结果]'}
                  </button>
                </div>
              </div>
              <div className="tool-output-wrap">
                <pre className={outputExpanded ? 'tool-output-expanded' : 'tool-output-collapsed'}>
                  {outputPreview}
                </pre>
                {needsOutputCollapse && !outputExpanded && (
                  <div
                    className="output-hidden-anchor"
                    onClick={() => setOutputExpanded(true)}
                  >
                    ⚠ {hiddenCount} hidden rows — click to expand
                  </div>
                )}
              </div>
            </div>
          )}
```

- [ ] **Step 5: Also update outputChunks streaming section for consistency**

找到 `{hasOutputChunks && status === 'running'` 区块（约第 172-186 行），在该 section 的 `<pre>` 外包一个 `.tool-output-wrap`：

```tsx
              <div className="tool-output-wrap">
                <pre className="tool-output-collapsed">
                  {(step.outputChunks || []).slice(-MAX_OUTPUT_CHUNKS).map((chunk, i) => (
                    <span
                      key={i}
                      className={chunk.isError ? 'output-error' : 'output-normal'}
                    >
                      {chunk.chunk}
                    </span>
                  ))}
                </pre>
              </div>
```

- [ ] **Step 6: Run build verification**

```bash
cd web-ui && npm run build 2>&1 | head -30
```
Expected: 无 TypeScript 编译错误。

- [ ] **Step 7: Commit**

```bash
git add web-ui/src/components/ToolStepCard.tsx
git commit -m "feat(ui): add collapsible output preview with copy and expand buttons"
```

---

### Task 4: ToolStepCard — 参数折叠展开

**Files:**
- Modify: `web-ui/src/components/ToolStepCard.tsx`

- [ ] **Step 1: Add paramsExpanded state**

在已有的 `const [outputExpanded, setOutputExpanded] = useState(false)` 下方添加：

```typescript
  const [paramsExpanded, setParamsExpanded] = useState(false)
```

- [ ] **Step 2: Compute params preview values**

在 `const hasOutputChunks` 之后添加：

```typescript
  const PREVIEW_PARAMS = 5
  const paramEntries = Object.entries(args)
  const needsParamsCollapse = paramEntries.length > PREVIEW_PARAMS
  const visibleParams = paramsExpanded || !needsParamsCollapse
    ? paramEntries
    : paramEntries.slice(0, PREVIEW_PARAMS)
  const hiddenParamsCount = paramEntries.length - PREVIEW_PARAMS
```

- [ ] **Step 3: Add truncate utility function at top of file**

在 `formatDuration` 函数之前（约第 219 行）添加：

```typescript
function truncate(str: string, maxLen: number): string {
  return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
}
```

- [ ] **Step 4: Replace args display section with collapsible version**

找到参数显示区块（约第 147-154 行）：

```tsx
// 原来：
          {Object.keys(args).length > 0 && (
            <div className="tool-step-section">
              <Text type="secondary" style={{ fontSize: 12 }}>参数</Text>
              <pre className="tool-step-code">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          )}
```

替换为：

```tsx
// 替换为：
          {Object.keys(args).length > 0 && (
            <div className="tool-step-section">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>参数</Text>
                {!paramsExpanded && needsParamsCollapse && (
                  <button
                    className="params-toggle-btn"
                    onClick={(e) => { e.stopPropagation(); setParamsExpanded(true) }}
                  >
                    [▼ {paramEntries.length}]
                  </button>
                )}
                {paramsExpanded && (
                  <button
                    className="params-toggle-btn"
                    onClick={(e) => { e.stopPropagation(); setParamsExpanded(false) }}
                  >
                    [▲ 隐藏]
                  </button>
                )}
              </div>
              <div className="tool-params-body">
                {visibleParams.map(([k, v]) => {
                  const strV = typeof v === 'object' ? JSON.stringify(v) : String(v)
                  return (
                    <div key={k} className="param-row">
                      <span className="param-key">{k}:</span>
                      {strV.length > 80
                        ? <Tooltip title={strV} mouseEnterDelay={0.5}><span className="param-value overflow">{truncate(strV, 80)}</span></Tooltip>
                        : <span className="param-value">{strV}</span>
                      }
                    </div>
                  )
                })}
                {paramsExpanded && needsParamsCollapse && (
                  <button
                    className="params-toggle-btn"
                    style={{ marginTop: 4 }}
                    onClick={(e) => { e.stopPropagation(); setParamsExpanded(false) }}
                  >
                    [▲ 收起 {hiddenParamsCount} 项]
                  </button>
                )}
              </div>
            </div>
          )}
```

需要确保 `Tooltip` 从 antd 已导入。如未导入，在 import 语句中添加。

- [ ] **Step 5: Run build verification**

```bash
cd web-ui && npm run build 2>&1 | head -30
```
Expected: 无 TypeScript 编译错误。如果 `Tooltip` 未导入，报错信息会指明，补充 import 即可。

- [ ] **Step 6: Commit**

```bash
git add web-ui/src/components/ToolStepCard.tsx
git commit -m "feat(ui): add collapsible params section with truncation"
```

---

### Task 5: ChatPage — 脉冲思考动画 + 闪烁光标 + 行数徽章 CSS

**Files:**
- Modify: `web-ui/src/pages/ChatPage.css`

- [ ] **Step 1: Append animations and streaming styles to ChatPage.css**

在 `ChatPage.css` 文件末尾追加：

```css
/* ── Streaming feedback enhancements ───────────────────── */

/* Pulse dot for thinking state */
.pulse-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #6366f1;
  animation: pulse-dot 1.2s ease-in-out infinite;
  vertical-align: middle;
  margin-right: 6px;
  flex-shrink: 0;
}

@keyframes pulse-dot {
  0%, 100% { opacity: 0.4; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}

/* Streaming cursor */
@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.stream-cursor {
  display: inline-block;
  color: #3b82f6;
  font-weight: 700;
  font-size: 14px;
  line-height: 1;
  animation: blink-cursor 0.6s step-end infinite;
  vertical-align: baseline;
  margin-left: 1px;
}
.stream-cursor.idle {
  animation: blink-cursor 1.2s ease-in-out infinite;
}

/* Claude Code output lines badge */
.claude-code-output-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.claude-code-lines-badge {
  font-size: 10px;
  color: #6b7280;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  background: #1e1e1e;
  padding: 1px 5px;
  border-radius: 3px;
}
.claude-code-progress-inner {
  animation: output-grow 0.15s ease-out;
}
```

- [ ] **Step 2: Commit**

```bash
git add web-ui/src/pages/ChatPage.css
git commit -m "feat(ui): add pulse dot, streaming cursor and line badge CSS"
```

---

### Task 6: ChatPage — 脉冲圆点、光标、行数徽章 JSX

**Files:**
- Modify: `web-ui/src/pages/ChatPage.tsx`

- [ ] **Step 1: Add streamingCursor state**

在 `streamingThinking` 等 state 声明附近（约第 106 行）添加：

```typescript
  const [streamingCursorFast, setStreamingCursorFast] = useState(true)
```

- [ ] **Step 2: Add cursor timing logic via useEffect**

在组件 body 末尾（`refreshStreamDoneRef` 附近，约第 976 行）添加：

```typescript
  // Cursor blink: fast while streaming, slow after done
  const [cursorVisible, setCursorVisible] = useState(false)
  const cursorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (loading && streamingThinking) {
      setCursorVisible(true)
      setStreamingCursorFast(true)
    } else if (loading) {
      // text streaming, start with fast
      setCursorVisible(true)
      setStreamingCursorFast(true)
    } else {
      // done, wait 300ms then switch to slow blink then hide
      if (cursorTimerRef.current) clearTimeout(cursorTimerRef.current)
      cursorTimerRef.current = setTimeout(() => {
        setStreamingCursorFast(false)
        setTimeout(() => setCursorVisible(false), 1500)
      }, 300)
    }
    return () => { if (cursorTimerRef.current) clearTimeout(cursorTimerRef.current) }
  }, [loading, streamingThinking])
```

- [ ] **Step 3: Compute Claude Code line count**

在 `const claudeCodeProgress` 附近（约第 532 行，在 `setClaudeCodeProgress` 之前）添加：

```typescript
  const claudeCodeLineCount = claudeCodeProgress
    ? claudeCodeProgress.split('\n').length
    : 0
```

- [ ] **Step 4: Replace thinking spinner with pulse dot in loading status**

找到 loading 状态渲染区（约第 1421-1425 行）：

```tsx
// 原来：
                        <div className="loading-status">
                          <Spin size="small" />
                          <span>{streamingThinking ? t('chat.thinking') : streamingToolSteps.length > 0 ? t('chat.callingTool') : t('chat.thinkingOrTool')}</span>
                        </div>
```

替换为：

```tsx
// 替换为：
                        <div className="loading-status">
                          {streamingThinking ? (
                            <span className="pulse-dot" />
                          ) : (
                            <Spin size="small" />
                          )}
                          <span>{streamingThinking ? t('chat.thinking') : streamingToolSteps.length > 0 ? t('chat.callingTool') : t('chat.thinkingOrTool')}</span>
                        </div>
```

- [ ] **Step 5: Add blinking cursor after ReactMarkdown in assistant message**

找到 assistant message 渲染区（约第 1366-1379 行 `ReactMarkdown` 区块）：

```tsx
// 原来：
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          rehypePlugins={[rehypeHighlight]}
                          className="markdown-body"
                          components={{
                            table: ({ children }) => (
                              <div className="markdown-table-wrapper">
                                <table>{children}</table>
                              </div>
                            ),
                          }}
                        >
                          {message.content}
                        </ReactMarkdown>
```

替换为（在 `{message.content}` 之后、`</ReactMarkdown>` 之前添加光标）：

```tsx
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          rehypePlugins={[rehypeHighlight]}
                          className="markdown-body"
                          components={{
                            table: ({ children }) => (
                              <div className="markdown-table-wrapper">
                                <table>{children}</table>
                              </div>
                            ),
                          }}
                        >
                          {message.content}
                        </ReactMarkdown>
                        {cursorVisible && message.role === 'assistant' && loading && (
                          <span className={`stream-cursor ${streamingCursorFast ? '' : 'idle'}`}>|</span>
                        )}
```

- [ ] **Step 6: Add line count badge to Claude Code progress panel**

找到 Claude Code 进度区（约第 1429-1433 行）：

```tsx
// 原来：
                        {claudeCodeProgress && (
                          <div className="claude-code-progress">
                            <pre>{claudeCodeProgress}</pre>
                          </div>
                        )}
```

替换为：

```tsx
// 替换为：
                        {claudeCodeProgress && (
                          <div className="claude-code-progress">
                            <div className="claude-code-output-header">
                              <span />
                              <span className="claude-code-lines-badge">
                                [{claudeCodeLineCount} lines]
                              </span>
                            </div>
                            <div className="claude-code-progress-inner">
                              <pre>{claudeCodeProgress}</pre>
                            </div>
                          </div>
                        )}
```

- [ ] **Step 7: Run build verification**

```bash
cd web-ui && npm run build 2>&1 | head -30
```
Expected: 无 TypeScript 编译错误。

- [ ] **Step 8: Commit**

```bash
git add web-ui/src/pages/ChatPage.tsx
git commit -m "feat(ui): add pulse dot thinking, blinking cursor and Claude Code line badge"
```

---

## 自查清单

- [x] **Spec coverage**: 设计中所有 6 个改进点（脉冲动画、闪烁光标、行数徽章、运行计时、完成徽章、输出折叠、参数折叠）均有对应 Task
- [x] **Placeholder scan**: 无 TBD/TODO/placeholder
- [x] **Type consistency**: `runningSeconds.toFixed(1)` 统一为小数点后1位；`durationStr` 使用 `toFixed(1)`；`truncate` 截断80字符
- [x] **Dependencies**: memo 比较函数已更新包含 `startTime/endTime`；`timerRef` 正确清理；`useEffect` deps 正确
- [x] **Import 检查**: `Tooltip` 需从 antd 导入（在 Task 4 处理）
