# SSE 自动重连实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在前端 SSE 方法中添加自动重连机制，提升连接稳定性

**Architecture:** 在 `api.subscribeToChatStream` 和 `api.subscribeTraceStream` 中添加重试逻辑，最多重试 3 次，指数递增间隔

**Tech Stack:** React + TypeScript (前端)

---

## Task 1: 添加 SSE 重连逻辑到 `subscribeToChatStream`

**Files:**
- Modify: `web-ui/src/api.ts:168-214`

- [x] **Step 1: 读取现有代码**

确认 `subscribeToChatStream` 方法的当前实现。

- [x] **Step 2: 添加重连逻辑**

将现有的 `subscribeToChatStream` 方法替换为带有重连功能的版本：

```typescript
/** 重连 Chat SSE 流（刷新/切换 tab 后继续接收推送） */
async subscribeToChatStream(
  sessionId: string,
  onEvent: (evt: StreamEvent) => void,
  signal?: AbortSignal
): Promise<ChatResponse | null> {
  const MAX_RETRIES = 3
  const RETRY_DELAYS = [1000, 2000, 3000] // 指数递增间隔

  const doSubscribe = async (): Promise<ChatResponse | null> => {
    const res = await fetch(`${API_BASE}/chat/sessions/${sessionId}/stream`, { signal })
    if (!res.ok) {
      // HTTP 4xx 不重试
      if (res.status >= 400 && res.status < 500) {
        throw new Error(`Stream reconnect failed: HTTP ${res.status}`)
      }
      throw new Error(`Stream reconnect failed: HTTP ${res.status}`)
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('Stream not supported')
    const dec = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (value) buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = done ? '' : (lines.pop() ?? '')
        for (const block of lines) {
          const dataParts = block.split('\n')
            .filter(line => line.startsWith('data:'))
            .map(line => line.slice(5).trimStart())
          const dataStr = dataParts.join('\n')
          if (!dataStr || dataStr === ': heartbeat') continue
          try {
            const evt = JSON.parse(dataStr) as StreamEvent
            onEvent(evt)
            if (evt.type === 'done') {
              return {
                content: 'content' in evt ? evt.content ?? '' : '',
                assistantMessage: 'assistantMessage' in evt ? evt.assistantMessage ?? null : null,
              }
            }
            if (evt.type === 'error') {
              throw new Error('message' in evt ? evt.message : 'Stream error')
            }
            if (evt.type === 'timeout') return null
          } catch (e) {
            if (e instanceof Error && e.message === 'Stream error') throw e
          }
        }
        if (done) break
      }
    } finally {
      reader.releaseLock()
    }
    return null
  }

  // 重连循环
  let lastError: Error | null = null
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    // 检查是否已取消
    if (signal?.aborted) throw new Error('Stream cancelled')

    try {
      return await doSubscribe()
    } catch (e) {
      lastError = e instanceof Error ? e : new Error(String(e))

      // 不重连的情况
      if (signal?.aborted) throw lastError
      if (lastError.message.includes('HTTP 4') || lastError.message.includes('Stream cancelled')) {
        throw lastError
      }

      // 还有重试机会，等待后重试
      if (attempt < MAX_RETRIES - 1) {
        console.warn(`[ChatStream] Connection failed (attempt ${attempt + 1}/${MAX_RETRIES}), retrying in ${RETRY_DELAYS[attempt]}ms...`, lastError.message)
        await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS[attempt]))
      }
    }
  }

  throw lastError || new Error('Stream failed after retries')
}
```

- [x] **Step 3: 验证 TypeScript 编译**

```bash
cd web-ui && npx tsc --noEmit
```

- [x] **Step 4: 提交**

```bash
git add web-ui/src/api.ts
git commit -m "feat(api): add auto-retry to subscribeToChatStream"
```

---

## Task 2: 添加 SSE 重连逻辑到 `subscribeTraceStream`

**Files:**
- Modify: `web-ui/src/api.ts:830-865`

- [x] **Step 1: 读取现有代码**

确认 `subscribeTraceStream` 方法的当前实现。

- [x] **Step 2: 添加重连逻辑**

```typescript
/** 订阅 Trace SSE 流 */
async subscribeTraceStream(
  onEvent: (evt: { type: string; data: any }) => void,
  signal?: AbortSignal
): Promise<void> {
  const MAX_RETRIES = 3
  const RETRY_DELAYS = [1000, 2000, 3000]

  const doSubscribe = async () => {
    const res = await fetch(`${API_BASE}/traces/stream`, {
      headers: { Accept: 'text/event-stream' },
      signal,
    })
    if (!res.ok) {
      if (res.status >= 400 && res.status < 500) {
        throw new Error(`Trace stream failed: HTTP ${res.status}`)
      }
      throw new Error(`Trace stream failed: HTTP ${res.status}`)
    }
    const reader = res.body?.getReader()
    if (!reader) throw new Error('Stream not supported')
    const dec = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          const dataLine = line.split('\n').find(l => l.startsWith('data:'))
          if (!dataLine) continue
          const dataStr = dataLine.slice(5).trim()
          if (!dataStr) continue
          try {
            const evt = JSON.parse(dataStr)
            onEvent(evt)
          } catch { /* skip */ }
        }
      }
    } finally {
      reader.releaseLock()
    }
  }

  // 重连循环
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    if (signal?.aborted) throw new Error('Stream cancelled')
    try {
      await doSubscribe()
      return
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e))
      if (signal?.aborted) throw err
      if (err.message.includes('HTTP 4') || err.message.includes('Stream cancelled')) {
        throw err
      }
      if (attempt < MAX_RETRIES - 1) {
        console.warn(`[TraceStream] Connection failed (attempt ${attempt + 1}/${MAX_RETRIES}), retrying...`)
        await new Promise(resolve => setTimeout(resolve, RETRY_DELAYS[attempt]))
      }
    }
  }
}
```

- [x] **Step 3: 验证 TypeScript 编译**

```bash
cd web-ui && npx tsc --noEmit
```

- [x] **Step 4: 提交**

```bash
git add web-ui/src/api.ts
git commit -m "feat(api): add auto-retry to subscribeTraceStream"
```

---

## Task 3: 验证

- [x] **Step 1: 测试编译**

```bash
cd web-ui && npx tsc --noEmit
```

- [x] **Step 2: 提交所有变更**

```bash
git status
git log --oneline -5
```
