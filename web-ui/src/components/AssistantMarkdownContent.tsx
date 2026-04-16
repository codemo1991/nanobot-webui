import { Collapse, Tag } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { parseAssistantThinking } from '../utils/parseAssistantThinking'

const mdComponents = {
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="markdown-table-wrapper">
      <table>{children}</table>
    </div>
  ),
}

interface AssistantMarkdownContentProps {
  content: string
}

function parseFileTags(content: string): Array<{ type: 'text' | 'file'; value: string }> {
  const segments: Array<{ type: 'text' | 'file'; value: string }> = []
  const regex = /<file>(.*?)<\/file>/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = regex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ type: 'text', value: content.slice(lastIndex, match.index) })
    }
    segments.push({ type: 'file', value: match[1] })
    lastIndex = regex.lastIndex
  }
  if (lastIndex < content.length) {
    segments.push({ type: 'text', value: content.slice(lastIndex) })
  }
  return segments
}

/**
 * 助手消息：将 &lt;think&gt; / thinking / [think] 等块单独折叠展示，正文走 Markdown。
 * 同时把 &lt;file&gt;path&lt;/file&gt; 渲染为蓝色 Tag。
 */
export function AssistantMarkdownContent({ content }: AssistantMarkdownContentProps) {
  const { t } = useTranslation()
  const { body, thinkingParts } = parseAssistantThinking(content)
  const hasThinking = thinkingParts.length > 0
  const thinkingText = thinkingParts.join('\n\n———\n\n')
  const mainMarkdown = hasThinking ? body : content
  const showMain = !hasThinking || mainMarkdown.trim().length > 0

  return (
    <>
      {hasThinking && (
        <Collapse
          bordered={false}
          ghost
          className="assistant-reasoning-collapse"
          defaultActiveKey={[]}
          items={[
            {
              key: 'reasoning',
              label: (
                <span className="assistant-reasoning-label">
                  <BulbOutlined className="assistant-reasoning-icon" aria-hidden />
                  {t('chat.reasoningCollapsed')}
                </span>
              ),
              children: (
                <pre className="assistant-reasoning-pre">{thinkingText}</pre>
              ),
            },
          ]}
        />
      )}
      {showMain && (
        <div className="markdown-body">
          {parseFileTags(mainMarkdown).map((seg, idx) =>
            seg.type === 'file' ? (
              <Tag key={idx} color="blue" className="message-file-tag">
                {seg.value}
              </Tag>
            ) : (
              <ReactMarkdown
                key={idx}
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeHighlight]}
                components={mdComponents}
              >
                {seg.value}
              </ReactMarkdown>
            )
          )}
        </div>
      )}
    </>
  )
}
