# WebUI 聊天交互体验增强设计

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强聊天流式反馈的"可感知性" + 工具调用过程的透明化，让 AI 工作过程清晰可见。

**Architecture:** 渐进增强，不重构现有 SSE 流式基础设施。在现有 `ToolStepCard`、`ToolStepsPanel`、`makeStreamEventHandler` 基础上增量改进 CSS 和 UI 状态。动画优先用 CSS，避免 React re-render 负担。

**Tech Stack:** React 18 + TypeScript + Ant Design + CSS animations + SSE

---

## 一、流式反馈增强

### 1.1 思考状态：脉冲动画

**文件:** `web-ui/src/pages/ChatPage.css`（新建样式区）

- 思考中状态：在 spinner 旁显示一个脉冲圆点，替代纯文字 "Thinking..."
- 圆点 `8px × 8px`，颜色 `#6366f1`（indigo），居中对齐
- 动画：`opacity 0.4 → 1.0`，`scale 0.8 → 1.2`，1.2s ease-in-out infinite

```css
@keyframes pulse-dot {
  0%, 100% { opacity: 0.4; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
.pulse-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #6366f1;
  animation: pulse-dot 1.2s ease-in-out infinite;
  vertical-align: middle;
  margin-right: 6px;
}
```

**影响点:** `ChatPage.tsx` 第 1421-1425 行 loading 状态渲染区域，替换 `{streamingThinking ? t('chat.thinking') : ...}` 为 `<span className="pulse-dot" /> + 文字`。

### 1.2 文字流式：闪烁光标

**文件:** `web-ui/src/components/ChatPage.css`

- AI 流式输出时，在 Markdown 渲染区域末尾显示闪烁竖线光标 `|`
- 光标颜色 `#3b82f6`（blue，与 AI 品牌色一致）
- 快速闪烁：流式进行中，0.6s step-end infinite
- 慢速闪烁：流式结束后 300ms，切换为 1.2s ease-in-out

```css
@keyframes blink-cursor {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.stream-cursor {
  display: inline-block;
  color: #3b82f6;
  font-weight: 700;
  animation: blink-cursor 0.6s step-end infinite;
}
.stream-cursor.idle {
  animation: blink-cursor 1.2s ease-in-out infinite;
}
```

**影响点:** `ChatPage.tsx` 第 1366-1379 行 `ReactMarkdown` 区域，在 `{message.content}` 后追加光标（仅在 `loading && streamingThinking` 时）。

### 1.3 Claude Code 实时输出：增长动画

**文件:** `web-ui/src/pages/ChatPage.css`

- 输出面板新增 `[N lines]` 行数徽章，跟随输出增长
- 输出区域向上滚动，新行加入顶部，旧行超出 30 行后淡出移除（而非一次性截断）
- 面板底部固定显示：`[24 lines]`，字号 11px，颜色 `#6b7280`

```css
.claude-code-output-lines {
  font-size: 11px;
  color: #6b7280;
  position: absolute;
  bottom: 4px;
  right: 8px;
}
.claude-code-progress pre {
  /* 向上滚动而非替换 */
  animation: output-grow 0.15s ease-out;
}
@keyframes output-grow {
  from { opacity: 0.5; }
  to { opacity: 1; }
}
```

**影响点:** `ChatPage.tsx` 第 1429-1433 行 Claude Code 进度区域，新增行数徽章。

---

## 二、工具调用透明化

### 2.1 工具卡片：运行时计时 + 完成后徽章

**文件:** `web-ui/src/components/ToolStepCard.tsx`

**工具开始时（running 状态）:**
- 卡片右上角显示 `[tool_name] 运行中 N.Ns`
- 运行时长从 0 开始，每 100ms 更新一次（用 `setInterval`，避免 setState 过频）
- 完成后 interval 清除

**工具完成时（completed 状态）:**
- 右上角徽章改为 `✅ Done N.Ns`，绿色 `#10b981`
- 错误时：`❌ Error N.Ns`，红色 `#ef4444`

```typescript
// ToolStepCard.tsx 新增逻辑
const [runningSeconds, setRunningSeconds] = useState(0)
const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

useEffect(() => {
  if (step.status === 'running') {
    const start = step.startTime || Date.now()
    timerRef.current = setInterval(() => {
      setRunningSeconds((Date.now() - start) / 1000)
    }, 100)
  } else if (timerRef.current) {
    clearInterval(timerRef.current)
    timerRef.current = null
  }
  return () => { if (timerRef.current) clearInterval(timerRef.current) }
}, [step.status])

const durationMs = step.endTime && step.startTime ? step.endTime - step.startTime : 0
const durationStr = (durationMs / 1000).toFixed(1) + 's'
const statusBadge = step.status === 'running'
  ? <span className="tool-status running">运行中 {runningSeconds.toFixed(1)}s</span>
  : step.status === 'completed'
  ? <span className="tool-status done">✅ Done {durationStr}</span>
  : <span className="tool-status error">❌ Error {durationStr}</span>
```

### 2.2 工具输出预览：折叠 + 操作按钮

**文件:** `web-ui/src/components/ToolStepCard.tsx`

**默认折叠（10 行）:**
- 输出限制为 10 行，超出显示 `[⚠ N hidden rows]` 锚点行
- 锚点行颜色 `#fbbf24`（amber），吸引点击

**操作按钮（始终可见）:**
- `[展开全部]` — 点击展开全部输出
- `[复制结果]` — 复制到剪贴板，显示 `已复制 ✓` toast
- `[收起]` — 折叠回 10 行

```typescript
const PREVIEW_LINES = 10

const outputLines = (step.result || '').split('\n')
const needsCollapse = outputLines.length > PREVIEW_LINES
const preview = needsCollapse ? outputLines.slice(0, PREVIEW_LINES).join('\n') : step.result
const hiddenCount = outputLines.length - PREVIEW_LINES

// 折叠/展开状态
const [outputExpanded, setOutputExpanded] = useState(false)

// 输出区域渲染
{outputExpanded ? (
  <pre className="tool-output">{step.result}</pre>
) : (
  <>
    <pre className="tool-output">{preview}</pre>
    {needsCollapse && (
      <div className="output-hidden-anchor">⚠ {hiddenCount} hidden rows</div>
    )}
  </>
)}
<div className="output-actions">
  {needsCollapse && (
    <button onClick={() => setOutputExpanded(true)}>[展开全部]</button>
  )}
  {outputExpanded && (
    <button onClick={() => setOutputExpanded(false)}>[收起]</button>
  )}
  <button onClick={() => navigator.clipboard.writeText(step.result || '')}>[复制结果]</button>
</div>
```

### 2.3 工具参数展开

**文件:** `web-ui/src/components/ToolStepCard.tsx`

- 默认折叠，点击 `[▼ 参数]` 展开参数键值对
- 参数超过 5 个时，显示前 5 个，剩余折叠
- 长值（如 command）截断显示 80 字符，悬浮显示完整内容

```typescript
const [paramsExpanded, setParamsExpanded] = useState(false)
const params = step.arguments || {}
const paramEntries = Object.entries(params)
const PREVIEW_PARAMS = 5
const needsParamsCollapse = paramEntries.length > PREVIEW_PARAMS

{paramsExpanded ? (
  <div className="tool-params">
    {paramEntries.map(([k, v]) => (
      <div key={k} className="param-row">
        <span className="param-key">{k}:</span>
        <Tooltip title={String(v)}>
          <span className="param-value">{truncate(String(v), 80)}</span>
        </Tooltip>
      </div>
    ))}
    {needsParamsCollapse && (
      <button className="params-toggle" onClick={() => setParamsExpanded(false)}>
        [▲ 隐藏参数]
      </button>
    )}
  </div>
) : (
  <button className="params-toggle" onClick={() => setParamsExpanded(true)}>
    [▼ 参数 {paramEntries.length}]
  </button>
)}
```

### 2.4 工具卡片 CSS 样式

**文件:** `web-ui/src/components/ToolStepCard.css`（新建）

```css
.tool-card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.tool-status {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
  margin-left: auto;
}
.tool-status.running {
  background: #eff6ff;
  color: #3b82f6;
}
.tool-status.done {
  background: #ecfdf5;
  color: #10b981;
}
.tool-status.error {
  background: #fef2f2;
  color: #ef4444;
}
.tool-output {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 300px;
  overflow-y: auto;
}
.output-hidden-anchor {
  background: #fffbeb;
  color: #92400e;
  font-size: 11px;
  padding: 2px 12px;
  cursor: pointer;
}
.output-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
}
.output-actions button {
  background: none;
  border: none;
  color: #3b82f6;
  cursor: pointer;
  padding: 0;
  font-size: 11px;
}
.output-actions button:hover {
  text-decoration: underline;
}
.tool-params {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  padding: 6px 10px;
  margin-top: 6px;
  font-size: 11px;
}
.param-row {
  display: flex;
  gap: 4px;
  line-height: 1.6;
}
.param-key {
  color: #6b7280;
  font-family: monospace;
  flex-shrink: 0;
}
.param-value {
  color: #111827;
  font-family: monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}
.params-toggle {
  background: none;
  border: none;
  color: #9ca3af;
  cursor: pointer;
  font-size: 11px;
  padding: 2px 0;
}
.params-toggle:hover {
  color: #6b7280;
}
```

---

## 三、受影响文件清单

| 文件 | 改动类型 | 改动内容 |
|------|---------|---------|
| `web-ui/src/components/ToolStepCard.tsx` | 修改 | 运行时计时、完成徽章、输出折叠、参数展开 |
| `web-ui/src/components/ToolStepCard.css` | 新建 | 工具卡片相关所有样式 |
| `web-ui/src/pages/ChatPage.tsx` | 修改 | 脉冲思考动画、光标、Claude Code 行数徽章 |
| `web-ui/src/pages/ChatPage.css` | 修改 | 脉冲圆点、光标、Claude Code 面板样式 |

**不改动:** `ToolStepsPanel.tsx`、`makeStreamEventHandler`（SSE 事件处理逻辑）、`api.ts`、后端任何文件。

---

## 四、后端配合（如需要）

当前 `tool_progress` SSE 事件已支持 `progress_percent`，但设计决定不使用进度条。

若未来需要运行时长以外的可感知反馈（如下一项工具的名称预告），后端需在 `tool_start` 后立即发送一个 `tool_planning` 事件。当前设计不依赖后端改动。

---

## 五、自查清单

- [x] placeholder scan：无 TBD/TODO
- [x] 内部一致性：所有状态（running/completed/error）与 UI 徽章一一对应
- [x] 范围检查：聚焦 A(聊天体验) + B3(工具过程)，不含会话侧边栏
- [x] 歧义检查：输出预览行数（10行）、参数截断（80字符）、计时精度（0.1s）均已明确
