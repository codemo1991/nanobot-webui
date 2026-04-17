/**
 * 从助手消息正文中剥离「思考 / reasoning」片段（多种模型标签兼容）。
 * 主体会去掉这些片段后交给 Markdown 渲染。
 */

export interface ParsedAssistantMessage {
  /** 正文（已移除思考块，可能含首尾空白） */
  body: string
  /** 提取的思考片段（按出现顺序） */
  thinkingParts: string[]
}

/**
 * 模型输出或存储过程中可能出现的噪声：闭合标签前多出的 `、全角尖括号、数字实体等。
 */
function normalizeForThinkingExtract(s: string): string {
  let t = s.replace(/\uFEFF/g, '').replace(/\r\n/g, '\n')
  // 全角尖括号 → 半角（部分环境复制会变全角）
  t = t.replace(/＜/g, '<').replace(/＞/g, '>')
  // 闭合标签前误带的反引号 / 代码围栏：`</redacted_thinking>` → </redacted_thinking>
  t = t.replace(/`+\s*(<\/redacted_thinking>)/gi, '$1')
  t = t.replace(/`+\s*(<\/think>)/gi, '$1')
  t = t.replace(/`+\s*(<\/thinking>)/gi, '$1')
  t = t.replace(/(<redacted_thinking[^>]*>)`+/gi, '$1')
  return t
}

/** 消息经存储或 Markdown 转义后可能出现 &lt;redacted_thinking&gt; */
function decodeHtmlAngleEntities(s: string): string {
  if (!s.includes('&')) {
    return s
  }
  let t = s
    .replace(/&#x3c;/gi, '<')
    .replace(/&#x3e;/gi, '>')
    .replace(/&#60;/g, '<')
    .replace(/&#62;/g, '>')
  if (t.includes('&lt;') || t.includes('&gt;')) {
    t = t.replace(/&lt;/gi, '<').replace(/&gt;/gi, '>')
  }
  // 最后处理 &amp;，避免破坏已展开的实体序列
  if (t.includes('&amp;')) {
    t = t.replace(/&amp;/g, '&')
  }
  return t
}

const THINK_EXTRACTORS: Array<{ regex: RegExp }> = [
  /* Qwen 等：<think>…</think>（短标签，须先于 <redacted_thinking> 模式） */
  { regex: new RegExp('<' + 'think' + '>([\\s\\S]*?)<\\/think>', 'gi') },
  /* 标准：<redacted_thinking>...</redacted_thinking> */
  { regex: /<redacted_thinking>([\s\S]*?)<\/redacted_thinking>/gi },
  /* 闭合简写为 </think> */
  { regex: /<redacted_thinking>([\s\S]*?)<\/think>/gi },
  /* 带属性：<redacted_thinking ...> */
  { regex: /<redacted_thinking[^>]*>([\s\S]*?)<\/redacted_thinking>/gi },
  { regex: /<redacted_thinking[^>]*>([\s\S]*?)<\/think>/gi },
  { regex: /<thinking>([\s\S]*?)<\/thinking>/gi },
  { regex: /\[think\]([\s\S]*?)\[\/think\]/gi },
  /* 行内反引号围栏（`think` 包裹） */
  { regex: /\x60think\x60([\s\S]*?)\x60/gi },
]

/**
 * 依次用各标签模式抽取内容；同一正文可命中多种标签（少见），均并入 thinkingParts。
 */
export function parseAssistantThinking(raw: string): ParsedAssistantMessage {
  if (!raw) {
    return { body: '', thinkingParts: [] }
  }

  let body = normalizeForThinkingExtract(decodeHtmlAngleEntities(raw))

  const thinkingParts: string[] = []

  for (const { regex } of THINK_EXTRACTORS) {
    body = body.replace(regex, (_full, inner: string) => {
      const t = typeof inner === 'string' ? inner.trim() : ''
      if (t) thinkingParts.push(t)
      return ''
    })
  }

  body = body.replace(/\n{3,}/g, '\n\n').trim()

  return { body, thinkingParts }
}

export function hasThinkingContent(parsed: ParsedAssistantMessage): boolean {
  return parsed.thinkingParts.length > 0
}
