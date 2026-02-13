import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Input, Spin, Empty, Avatar, Typography } from 'antd'
import { ThunderboltOutlined, PlusOutlined, SendOutlined, UserOutlined, RobotOutlined, LockOutlined, SyncOutlined, EditOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { formatMessageTime } from '../../utils/format'
import { useMirrorChat } from '../../hooks/useMirrorChat'
import type { AttackLevel } from '../../types'

const { TextArea } = Input
const { Text } = Typography

const ATTACK_LEVELS: { key: AttackLevel; nameKey: string; descKey: string }[] = [
  { key: 'light', nameKey: 'mirror.attackLight', descKey: 'mirror.attackLightDesc' },
  { key: 'medium', nameKey: 'mirror.attackMedium', descKey: 'mirror.attackMediumDesc' },
  { key: 'heavy', nameKey: 'mirror.attackHeavy', descKey: 'mirror.attackHeavyDesc' },
]

const BIAN_TOPIC_PRESET_KEYS = ['bianPreset1', 'bianPreset2', 'bianPreset3', 'bianPreset4', 'bianPreset5'] as const

function BianTab() {
  const { t } = useTranslation()
  const [attackLevel, setAttackLevel] = useState<AttackLevel>('medium')
  const [customTopic, setCustomTopic] = useState('')

  const chat = useMirrorChat('bian', {
    attackLevel,
    customTopic,
    firstReplyPlaceholder: t('mirror.bianGeneratingTopics'),
    onStartComplete: () => setCustomTopic(''),
  })

  return (
    <div className="mirror-split-layout">
      <div className="mirror-sidebar">
        <div className="mirror-sidebar-header">
          <h3>{t('mirror.bian')}</h3>
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
            <ThunderboltOutlined className="mirror-logo" />
            <div className="mirror-empty-title">{t('mirror.bianStart')}</div>
            <div className="mirror-empty-hint">{t('mirror.bianStartHint')}</div>

            <div className="mirror-config-panel">
              <div style={{ fontSize: 14, fontWeight: 600, color: '#333' }}>
                {t('mirror.attackLevel')}
              </div>
              {ATTACK_LEVELS.map((level) => (
                <div
                  key={level.key}
                  className={`attack-level-option ${attackLevel === level.key ? 'selected' : ''}`}
                  onClick={() => setAttackLevel(level.key)}
                >
                  <div className="attack-level-name">{t(level.nameKey)}</div>
                  <div className="attack-level-desc">{t(level.descKey)}</div>
                </div>
              ))}
            </div>

            <div style={{ width: '100%', maxWidth: 480 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: '#333', marginBottom: 8 }}>
                {t('mirror.bianTopicLabel')}
              </div>
              <Input
                value={customTopic}
                onChange={(e) => setCustomTopic(e.target.value)}
                placeholder={t('mirror.bianTopicPlaceholder')}
                style={{ width: '100%', marginBottom: 8 }}
              />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {BIAN_TOPIC_PRESET_KEYS.map((key) => {
                  const presetText = t(`mirror.${key}`)
                  return (
                    <Button
                      key={key}
                      size="small"
                      type={presetText === customTopic ? 'primary' : 'default'}
                      onClick={() => setCustomTopic(presetText === customTopic ? '' : presetText)}
                    >
                      {presetText}
                    </Button>
                  )
                })}
              </div>
            </div>

            <Button
              type="primary"
              size="large"
              className="mirror-start-btn"
              icon={<ThunderboltOutlined />}
              onClick={chat.handleStartSession}
              style={{ marginTop: 20 }}
            >
              {t('mirror.bianStart')}
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
                    onClick={() => chat.handleSeal('mirror.endBian')}
                    className="mirror-end-button"
                    title={t('mirror.endBian')}
                  >
                    {t('mirror.endBian')}
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

export default BianTab
