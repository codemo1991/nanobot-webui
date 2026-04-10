import { Collapse } from 'antd'
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

/**
 * 助手消息：将 &lt;think&gt; / thinking / [think] 等块单独折叠展示，正文走 Markdown。
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
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[rehypeHighlight]}
          className="markdown-body"
          components={mdComponents}
        >
          {mainMarkdown}
        </ReactMarkdown>
      )}
    </>
  )
}
