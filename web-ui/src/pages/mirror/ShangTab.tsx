import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Spin, Empty, Input, message as antMessage } from 'antd'
import { EyeOutlined, CheckOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { ShangRecord } from '../../types'

const { TextArea } = Input

function ShangTab() {
  const { t } = useTranslation()
  const [records, setRecords] = useState<ShangRecord[]>([])
  const [todayRecord, setTodayRecord] = useState<ShangRecord | null>(null)
  const [todayDone, setTodayDone] = useState(false)
  const [selectedRecord, setSelectedRecord] = useState<ShangRecord | null>(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [attribution, setAttribution] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const autoStartedRef = useRef(false)

  useEffect(() => {
    loadData()
  }, [])

  // 当日未赏时自动开始生成（需求：系统自动开始生成）
  useEffect(() => {
    if (loading || generating || autoStartedRef.current) return
    if (!todayDone && !todayRecord) {
      autoStartedRef.current = true
      setGenerating(true)
      api
        .startShang()
        .then((record) => {
          setTodayRecord(record)
          setSelectedRecord(record)
          setTodayDone(false)
          setRecords((prev) => {
            const exists = prev.find((r) => r.id === record.id)
            return exists ? prev : [record, ...prev]
          })
        })
        .catch(() => antMessage.error(t('mirror.loadFailed')))
        .finally(() => setGenerating(false))
    }
  }, [loading, todayDone, todayRecord, generating, t])

  const loadData = async () => {
    setLoading(true)
    try {
      const [todayRes, recordsRes] = await Promise.all([
        api.getShangToday(),
        api.getShangRecords(),
      ])
      setTodayDone(todayRes.done)
      setTodayRecord(todayRes.record)
      setRecords(recordsRes.items)
      if (todayRes.record) {
        setSelectedRecord(todayRes.record)
      }
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const handleStartShang = async () => {
    setGenerating(true)
    try {
      const record = await api.startShang()
      setTodayRecord(record)
      setSelectedRecord(record)
      setTodayDone(false) // still in choosing phase
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setGenerating(false)
    }
  }

  const handleChoose = async (choice: 'A' | 'B') => {
    if (!todayRecord || !attribution.trim()) return
    setSubmitting(true)
    try {
      const updated = await api.submitShangChoice(todayRecord.id, choice, attribution.trim())
      setTodayRecord(updated)
      setSelectedRecord(updated)
      setTodayDone(true)
      setRecords((prev) => {
        const exists = prev.find((r) => r.id === updated.id)
        if (exists) return prev.map((r) => (r.id === updated.id ? updated : r))
        return [updated, ...prev]
      })
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="mirror-empty-state">
        <Spin size="large" />
      </div>
    )
  }

  return (
    <div className="mirror-split-layout">
      {/* 左栏：历史记录 */}
      <div className="mirror-sidebar">
        <div className="mirror-sidebar-header">
          <h3>{t('mirror.shangHistory')}</h3>
        </div>
        <div className="mirror-sidebar-list">
          {records.length === 0 ? (
            <Empty
              description={t('mirror.shangNoRecords')}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            records.map((r) => (
              <div
                key={r.id}
                className={`mirror-session-item ${selectedRecord?.id === r.id ? 'active' : ''}`}
                onClick={() => setSelectedRecord(r)}
              >
                <div className="session-title">{r.topic}</div>
                <div className="session-meta">
                  <span>{r.date}</span>
                  <span>选{r.choice || '?'}</span>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 右栏：内容区 */}
      <div className="mirror-main">
        {/* 当日未赏 + 未生成 */}
        {!todayDone && !todayRecord && !generating && (
          <div className="mirror-empty-state">
            <EyeOutlined className="mirror-logo" />
            <div className="mirror-empty-title">{t('mirror.shangToday')}</div>
            <div className="mirror-empty-hint">{t('mirror.shangDesc')}</div>
            <Button
              type="primary"
              size="large"
              className="mirror-start-btn"
              icon={<EyeOutlined />}
              onClick={handleStartShang}
            >
              {t('mirror.shangToday')}
            </Button>
          </div>
        )}

        {/* 生成中 */}
        {generating && (
          <div className="mirror-empty-state">
            <Spin size="large" />
            <div className="mirror-empty-title">{t('mirror.shangLoading')}</div>
          </div>
        )}

        {/* 选择阶段 */}
        {todayRecord && todayRecord.status === 'choosing' && (
          <div className="shang-content">
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>{todayRecord.topic}</div>
            <div className="shang-images">
              <div className="shang-image-card">
                {todayRecord.imageA ? (
                  <img src={todayRecord.imageA} alt="A" />
                ) : (
                  <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f0f0f0', fontSize: 24, fontWeight: 700, color: '#999' }}>A</div>
                )}
                <div className="shang-image-desc">{todayRecord.descriptionA}</div>
              </div>
              <div className="shang-image-card">
                {todayRecord.imageB ? (
                  <img src={todayRecord.imageB} alt="B" />
                ) : (
                  <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f0f0f0', fontSize: 24, fontWeight: 700, color: '#999' }}>B</div>
                )}
                <div className="shang-image-desc">{todayRecord.descriptionB}</div>
              </div>
            </div>

            <div style={{ marginTop: 16, width: '100%', maxWidth: 600 }}>
              <div style={{ marginBottom: 8, fontSize: 14, color: '#666' }}>
                {t('mirror.shangWhyChoose')}
              </div>
              <TextArea
                value={attribution}
                onChange={(e) => setAttribution(e.target.value)}
                placeholder={t('mirror.shangInputPlaceholder')}
                autoSize={{ minRows: 2, maxRows: 4 }}
              />
            </div>

            <div className="shang-choose-buttons">
              <Button
                type="primary"
                size="large"
                onClick={() => handleChoose('A')}
                loading={submitting}
                disabled={!attribution.trim()}
              >
                {t('mirror.shangChooseA')}
              </Button>
              <Button
                type="primary"
                size="large"
                onClick={() => handleChoose('B')}
                loading={submitting}
                disabled={!attribution.trim()}
              >
                {t('mirror.shangChooseB')}
              </Button>
            </div>
          </div>
        )}

        {/* 已完成 或 查看历史 */}
        {selectedRecord && (selectedRecord.status === 'done' || todayDone) && (
          <div className="shang-record-detail">
            <h2 style={{ marginBottom: 16 }}>{selectedRecord.topic}</h2>
            <div className="shang-images" style={{ marginBottom: 24 }}>
              <div className={`shang-image-card ${selectedRecord.choice === 'A' ? 'selected' : ''}`}>
                {selectedRecord.imageA ? (
                  <img src={selectedRecord.imageA} alt="A" />
                ) : (
                  <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f0f0f0', fontSize: 24, fontWeight: 700, color: '#999' }}>A</div>
                )}
                <div className="shang-image-desc">
                  {selectedRecord.choice === 'A' && <CheckOutlined style={{ color: '#1890ff', marginRight: 4 }} />}
                  {selectedRecord.descriptionA}
                </div>
              </div>
              <div className={`shang-image-card ${selectedRecord.choice === 'B' ? 'selected' : ''}`}>
                {selectedRecord.imageB ? (
                  <img src={selectedRecord.imageB} alt="B" />
                ) : (
                  <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f0f0f0', fontSize: 24, fontWeight: 700, color: '#999' }}>B</div>
                )}
                <div className="shang-image-desc">
                  {selectedRecord.choice === 'B' && <CheckOutlined style={{ color: '#1890ff', marginRight: 4 }} />}
                  {selectedRecord.descriptionB}
                </div>
              </div>
            </div>

            {selectedRecord.attribution && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>归因</div>
                <div style={{ color: '#666', fontSize: 14 }}>{selectedRecord.attribution}</div>
              </div>
            )}

            {selectedRecord.analysis && (
              <div style={{ marginBottom: 16 }}>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>分析结果</div>
                {selectedRecord.analysis.jungType && (
                  <div style={{ marginBottom: 8 }}>
                    <span style={{ fontWeight: 500 }}>荣格类型:</span> {selectedRecord.analysis.jungType.typeCode} - {selectedRecord.analysis.jungType.description}
                  </div>
                )}
                {selectedRecord.analysis.archetype && (
                  <div style={{ marginBottom: 8 }}>
                    <span style={{ fontWeight: 500 }}>原型:</span> {selectedRecord.analysis.archetype.primary} / {selectedRecord.analysis.archetype.secondary}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* 纯空态：没有选中记录 + 今日已完成 */}
        {todayDone && !selectedRecord && (
          <div className="mirror-empty-state">
            <CheckOutlined className="mirror-logo" style={{ color: '#52c41a' }} />
            <div className="mirror-empty-title">{t('mirror.shangDone')}</div>
          </div>
        )}
      </div>
    </div>
  )
}

export default ShangTab
