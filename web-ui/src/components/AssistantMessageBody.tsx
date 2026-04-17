import { Collapse } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { parseAssistantThinking } from '../utils/parseAssistantThinking'

interface AssistantMessageBodyProps {
  content: string
}

export function AssistantMessageBody({ content }: AssistantMessageBodyProps) {
  const { t } = useTranslation()
  const parsed = parseAssistantThinking(content)
  const thinkingJoined = parsed.thinkingParts.join('\n\n---\n\n')
  const mdSource = parsed.body

  return (
    <>
      {parsed.thinkingParts.length > 0 && (
        <Collapse
          bordered={false}
          ghost
          className="assistant-thinking-collapse"
          defaultActiveKey={[]}
          items={[
            {
              key: 'reasoning',
              label: (
                <span className="assistant-thinking-header">
                  <BulbOutlined className="assistant-thinking-icon" aria-hidden />
                  <span>{t('chat.reasoningCollapsed')}</span>
                </span>
              ),
              children: (
                <div className="assistant-thinking-body">
                  <pre className="assistant-thinking-pre">{thinkingJoined}</pre>
                </div>
              ),
            },
          ]}
        />
      )}
      {mdSource.length > 0 ? (
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
          {mdSource}
        </ReactMarkdown>
      ) : parsed.thinkingParts.length > 0 ? null : (
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
          {content}
        </ReactMarkdown>
      )}
    </>
  )
}
