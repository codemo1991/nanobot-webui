import { useTranslation } from 'react-i18next'
import { Button, Input, Spin, Empty, Avatar, Typography } from 'antd'
import { BulbOutlined, PlusOutlined, SendOutlined, UserOutlined, RobotOutlined, LockOutlined, SyncOutlined, EditOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { formatMessageTime } from '../../utils/format'
import { useMirrorChat } from '../../hooks/useMirrorChat'

const { TextArea } = Input
const { Text } = Typography

function WuTab() {
  const { t } = useTranslation()
  const chat = useMirrorChat('wu', { firstReplyPlaceholder: '...' })

  return (
    <div className="mirror-split-layout">
      <div className="mirror-sidebar">
        <div className="mirror-sidebar-header">
          <h3>{t('mirror.wu')}</h3>
          <Button
            type="primary"
            size="small"
            icon={<PlusOutlined />}
            onClick={chat.handleStartSession}
          >
            {t('mirror.newSession')}
          </Button>
        </div>
        <div className="mirror-sidebar-list">
          {chat.loadingSessions ? (
            <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>
          ) : chat.sessions.length === 0 ? (
            <Empty
              description={t('mirror.noSessions')}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            chat.sessions.map((s) => (
              <div
                key={s.id}
                className={`mirror-session-item ${chat.currentSession?.id === s.id ? 'active' : ''} ${s.status === 'sealed' ? 'sealed' : ''}`}
                onClick={() => chat.setCurrentSession(s)}
              >
                {chat.editingSessionId === s.id ? (
                  <Input
                    value={chat.editTitle}
                    onChange={(e) => chat.setEditTitle(e.target.value)}
                    onPressEnter={() => chat.handleRenameSession(s.id)}
                    onBlur={() => chat.handleRenameSession(s.id)}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    size="small"
                  />
                ) : (
                  <>
                    <div className="session-title">
                      {s.title || s.topic || t('chat.defaultTitle')}
                    </div>
                    <div className="session-meta">
                      <span>{new Date(s.createdAt).toLocaleDateString()}</span>
                      <span className={`session-status ${s.status}`}>
                        {s.status === 'sealed' ? (
                          <><LockOutlined /> {t('mirror.sessionSealed')}</>
                        ) : (
                          t('mirror.sessionActive')
                        )}
                      </span>
                    </div>
                    {s.insight && (
                      <div style={{ fontSize: 11, color: '#999', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {s.insight}
                      </div>
                    )}
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      className="mirror-session-edit-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        chat.setEditingSessionId(s.id)
                        chat.setEditTitle(s.title || s.topic || '')
                      }}
                    />
                  </>
                )}
              </div>
            ))
          )}
        </div>
      </div>

      <div className="mirror-main">
        {!chat.currentSession ? (
          <div className="mirror-empty-state">
            <BulbOutlined className="mirror-logo" />
            <div className="mirror-empty-title">{t('mirror.wuStart')}</div>
            <div className="mirror-empty-hint">{t('mirror.wuStartHint')}</div>
            <Button
              type="primary"
              size="large"
              className="mirror-start-btn"
              icon={<BulbOutlined />}
              onClick={chat.handleStartSession}
            >
              {t('mirror.wuStart')}
            </Button>
          </div>
        ) : (
          <div className="mirror-chat-area">
            <div className="mirror-chat-messages">
              {chat.loading ? (
                <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
              ) : (
                chat.messages.map((msg) => (
                  <div key={msg.id} className={`mirror-message-wrapper ${msg.role}`}>
                    <div className="mirror-message-bubble">
                      <div className="mirror-message-avatar-row">
                        <Avatar
                          icon={msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                          className={`mirror-message-avatar ${msg.role}`}
                        />
                        <div className="mirror-message-header">
                          <Text strong>{msg.role === 'user' ? t('chat.you') : 'Nanobot'}</Text>
                          {msg.createdAt && (
                            <span className="mirror-message-time">{formatMessageTime(msg.createdAt)}</span>
                          )}
                        </div>
                      </div>
                      <div className="mirror-message-content">
                        <div className="mirror-message-text">
                          {msg.role === 'assistant' ? (
                            <ReactMarkdown remarkPlugins={[remarkGfm]} className="markdown-body">{msg.content}</ReactMarkdown>
                          ) : (
                            <Text>{msg.content}</Text>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                ))
              )}
              <div ref={chat.messagesEndRef} />
            </div>

            {!chat.isSealed ? (
              <div className="mirror-chat-input-container">
                <TextArea
                  value={chat.input}
                  onChange={(e) => chat.setInput(e.target.value)}
                  onKeyDown={chat.handleKeyDown}
                  placeholder={t('chat.inputPlaceholder')}
                  autoSize={{ minRows: 1, maxRows: 4 }}
                  disabled={chat.sending}
                  className="mirror-chat-input"
                />
                <div className="mirror-chat-input-actions">
                  <Button
                    type="primary"
                    icon={<SendOutlined />}
                    onClick={chat.handleSend}
                    loading={chat.sending}
                    className="mirror-send-button"
                  />
                  <Button
                    danger
                    icon={<LockOutlined />}
                    onClick={() => chat.handleSeal('mirror.endWu')}
                    className="mirror-end-button"
                    title={t('mirror.endWu')}
                  >
                    {t('mirror.endWu')}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="mirror-chat-sealed-footer">
                <span style={{ color: '#999', display: 'flex', alignItems: 'center', gap: 6 }}>
                  <LockOutlined /> {t('mirror.sessionSealed')}
                </span>
                <Button
                  size="small"
                  icon={<SyncOutlined spin={chat.retrying} />}
                  onClick={chat.handleRetryAnalysis}
                  loading={chat.retrying}
                >
                  {t('mirror.retryAnalysis')}
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default WuTab
