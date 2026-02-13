import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Spin, Button, Alert, message as antMessage } from 'antd'
import { UserOutlined, ThunderboltOutlined, WarningOutlined, CheckCircleOutlined, LineChartOutlined, SyncOutlined, DownloadOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { MirrorProfile } from '../../types'

const BIG_FIVE_COLORS: Record<string, string> = {
  openness: '#1890ff',
  conscientiousness: '#52c41a',
  extraversion: '#faad14',
  agreeableness: '#13c2c2',
  neuroticism: '#f5222d',
}

function WoTab() {
  const { t } = useTranslation()
  const [profile, setProfile] = useState<MirrorProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [noDataError, setNoDataError] = useState(false)

  useEffect(() => {
    loadProfile()
  }, [])

  const loadProfile = async () => {
    setLoading(true)
    try {
      const data = await api.getMirrorProfile()
      setProfile(data)
    } catch {
      // Profile may not exist yet
      setProfile(null)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="mirror-empty-state">
        <Spin size="large" />
      </div>
    )
  }

  const handleGenerateProfile = async () => {
    setGenerating(true)
    setNoDataError(false)
    try {
      const data = await api.generateMirrorProfile()
      setProfile(data)
      antMessage.success(t('mirror.profileGenerated'))
    } catch (e: unknown) {
      const err = e as Error & { code?: string }
      const code = err?.code
      const msg = err?.message || ''
      const isNoData = code === 'NO_DATA' || /无数据|no data/i.test(msg)
      setNoDataError(isNoData)
      antMessage.error(msg || t('mirror.loadFailed'))
    } finally {
      setGenerating(false)
    }
  }

  const handleExportProfile = () => {
    if (!profile) return
    const blob = new Blob([JSON.stringify(profile, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `profile-${profile.updateTime?.replace(/[^0-9]/g, '') || Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
    antMessage.success(t('mirror.exportProfileSuccess'))
  }

  if (!profile || typeof profile !== 'object') {
    return (
      <div className="mirror-empty-state">
        <UserOutlined className="mirror-logo" />
        <div className="mirror-empty-title">{t('mirror.woEmpty')}</div>
        <div className="mirror-empty-hint">{t('mirror.woEmptyHint')}</div>
        {noDataError && (
          <Alert
            type="info"
            message={t('mirror.woNoDataHint')}
            showIcon
            style={{ marginTop: 16, maxWidth: 420, textAlign: 'left' }}
          />
        )}
        <Button
          type="primary"
          size="large"
          className="mirror-start-btn"
          icon={<SyncOutlined spin={generating} />}
          onClick={handleGenerateProfile}
          loading={generating}
          style={{ marginTop: 16 }}
        >
          {t('mirror.generateProfile')}
        </Button>
      </div>
    )
  }

  const bigFive = profile.bigFive ?? {}
  const jungArchetype = profile.jungArchetype ?? { primary: '-', secondary: '-' }
  const drivers = Array.isArray(profile.drivers) ? profile.drivers : []
  const conflicts = Array.isArray(profile.conflicts) ? profile.conflicts : []
  const suggestions = Array.isArray(profile.suggestions) ? profile.suggestions : []

  const bigFiveKeys = ['openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism'] as const

  return (
    <div className="wo-profile">
      {/* 大五人格 */}
      <div className="wo-profile-section">
        <h3><LineChartOutlined /> {t('mirror.bigFive')}</h3>
        {bigFiveKeys.map((key) => {
          const score = typeof bigFive[key] === 'number' ? Math.min(100, Math.max(0, bigFive[key])) : 0
          return (
            <div className="big-five-item" key={key}>
              <span className="big-five-label">{t(`mirror.${key}`)}</span>
              <div className="big-five-bar">
                <div
                  className="big-five-fill"
                  style={{
                    width: `${score}%`,
                    background: BIG_FIVE_COLORS[key],
                  }}
                />
              </div>
              <span className="big-five-score">{score}</span>
            </div>
          )
        })}
      </div>

      {/* 荣格原型 */}
      <div className="wo-profile-section">
        <h3><UserOutlined /> {t('mirror.jungArchetype')}</h3>
        <p><strong>{t('mirror.primaryArchetype')}:</strong> {String(jungArchetype.primary || '-')}</p>
        <p><strong>{t('mirror.secondaryArchetype')}:</strong> {String(jungArchetype.secondary || '-')}</p>
      </div>

      {/* 深层驱动力 */}
      {drivers.length > 0 && (
        <div className="wo-profile-section">
          <h3><ThunderboltOutlined /> {t('mirror.drivers')}</h3>
          {drivers.map((d, i) => (
            <div key={i} style={{ marginBottom: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{d?.need ?? '-'}</div>
              <div style={{ fontSize: 13, color: '#666' }}>{d?.evidence ?? '-'}</div>
              <div style={{ fontSize: 13, color: '#1890ff', marginTop: 4 }}>{d?.suggestion ?? '-'}</div>
            </div>
          ))}
        </div>
      )}

      {/* 核心矛盾 */}
      {conflicts.length > 0 && (
        <div className="wo-profile-section">
          <h3><WarningOutlined /> {t('mirror.conflicts')}</h3>
          {conflicts.map((c, i) => (
            <div key={i} style={{ marginBottom: 12, padding: '8px 12px', background: '#fff7e6', borderRadius: 8 }}>
              <div style={{ fontSize: 13 }}><strong>显性:</strong> {c?.explicit ?? '-'}</div>
              <div style={{ fontSize: 13 }}><strong>隐性:</strong> {c?.implicit ?? '-'}</div>
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>{c?.type ?? ''}</div>
            </div>
          ))}
        </div>
      )}

      {/* 行动建议 */}
      {suggestions.length > 0 && (
        <div className="wo-profile-section">
          <h3><CheckCircleOutlined /> {t('mirror.suggestions')}</h3>
          <ol style={{ paddingLeft: 20, margin: 0 }}>
            {suggestions.map((s, i) => (
              <li key={i} style={{ marginBottom: 8, fontSize: 14, lineHeight: 1.6 }}>{s}</li>
            ))}
          </ol>
        </div>
      )}

      {/* 更新时间与刷新 */}
      <div style={{ textAlign: 'center', color: '#999', fontSize: 12, padding: '12px 0' }}>
        {t('mirror.profileUpdatedAt')}: {profile.updateTime ?? '-'}
      </div>
      <div style={{ textAlign: 'center', padding: '8px 0', display: 'flex', gap: 8, justifyContent: 'center' }}>
        <Button
          size="small"
          icon={<SyncOutlined spin={generating} />}
          onClick={handleGenerateProfile}
          loading={generating}
        >
          {t('mirror.refreshProfile')}
        </Button>
        <Button
          size="small"
          icon={<DownloadOutlined />}
          onClick={handleExportProfile}
        >
          {t('mirror.exportProfile')}
        </Button>
      </div>
    </div>
  )
}

export default WoTab
