import { useState, useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Spin, Empty, Input, Modal, Popconfirm, message as antMessage } from 'antd'
import { EyeOutlined, CheckOutlined, ZoomInOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
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
  const [regenerating, setRegenerating] = useState(false)
  const [selectedChoice, setSelectedChoice] = useState<'A' | 'B' | null>(null)
  const [zoomingImage, setZoomingImage] = useState<string | null>(null)
  const [filterTopic, setFilterTopic] = useState('')
  const autoStartedRef = useRef(false)
  const hasLoadedOnceRef = useRef(false)

  useEffect(() => {
    loadData()
  }, [])

  useEffect(() => {
    if (todayRecord?.status === 'choosing') setSelectedChoice(null)
  }, [todayRecord?.id])

  // 当日未赏时自动开始生成（严格条件：历史加载完成后才触发，避免竞态）
  useEffect(() => {
    if (!hasLoadedOnceRef.current || loading || generating || autoStartedRef.current) return
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
      hasLoadedOnceRef.current = true
    }
  }

  const handleStartShang = async () => {
    setGenerating(true)
    try {
      const record = await api.startShang()
      setTodayRecord(record)
      setSelectedRecord(record)
      setTodayDone(false)
      setRecords((prev) => {
        const exists = prev.find((r) => r.id === record.id)
        return exists ? prev : [record, ...prev]
      })
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setGenerating(false)
    }
  }

  const handleRegenerate = async () => {
    if (!todayRecord || regenerating) return
    setRegenerating(true)
    try {
      const updated = await api.regenerateShangImages(todayRecord.id)
      setTodayRecord(updated)
      setSelectedRecord(updated)
      setSelectedChoice(null)
      antMessage.success(t('mirror.shangRegenerateSuccess'))
    } catch {
      antMessage.error(t('mirror.loadFailed'))
    } finally {
      setRegenerating(false)
    }
  }

  const handleChoose = async () => {
    if (!todayRecord || !selectedChoice) return
    setSubmitting(true)
    try {
      const updated = await api.submitShangChoice(todayRecord.id, selectedChoice, attribution.trim() || undefined)
      setTodayRecord(updated)
      setSelectedRecord(updated)
      setTodayDone(true)
      setSelectedChoice(null)
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

  const handleDeleteRecord = async (recordId: string) => {
    try {
      await api.deleteShangRecord(recordId)
      setRecords((prev) => prev.filter((r) => r.id !== recordId))
      if (selectedRecord?.id === recordId) {
        setSelectedRecord(null)
      }
      if (todayRecord?.id === recordId) {
        setTodayRecord(null)
      }
      antMessage.success(t('chat.sessionDeleted'))
    } catch {
      antMessage.error(t('mirror.deleteFailed'))
    }
  }

  const filteredRecords = useMemo(() => {
    return records.filter((r) => {
      if (filterTopic.trim()) {
        const kw = filterTopic.trim().toLowerCase()
        if (!r.topic?.toLowerCase().includes(kw)) return false
      }
      return true
    })
  }, [records, filterTopic])

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
          <Button
            type="primary"
            size="small"
            icon={<PlusOutlined />}
            onClick={handleStartShang}
            loading={generating}
          >
            {t('mirror.newSession')}
          </Button>
        </div>
        <div style={{ padding: '0 12px 8px' }}>
          <Input
            placeholder={t('mirror.shangFilterTopic')}
            value={filterTopic}
            onChange={(e) => setFilterTopic(e.target.value)}
            size="small"
            allowClear
          />
        </div>
        <div className="mirror-sidebar-list">
          {filteredRecords.length === 0 ? (
            <Empty
              description={records.length === 0 ? t('mirror.shangNoRecords') : t('mirror.shangFilterNoMatch')}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            filteredRecords.map((r) => (
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
                <Popconfirm
                  title={t('mirror.deleteConfirm')}
                  onConfirm={(e) => { e?.stopPropagation(); handleDeleteRecord(r.id) }}
                  onCancel={(e) => e?.stopPropagation()}
                  okText={t('mirror.deleteOk')}
                  cancelText={t('mirror.deleteCancel')}
                >
                  <Button
                    type="text"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    className="mirror-session-edit-btn"
                    onClick={(e) => e.stopPropagation()}
                  />
                </Popconfirm>
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
            <div style={{ fontSize: 16, fontWeight: 600, width: '100%', maxWidth: 680 }}>{todayRecord.topic}</div>
            <div className="shang-images">
              {/* A 图片 */}
              <div
                className={`shang-image-card shang-image-card-selectable ${selectedChoice === 'A' ? 'selected' : ''}`}
                onClick={() => setSelectedChoice('A')}
              >
                <div className="shang-image-wrapper">
                  {todayRecord.imageA ? (
                    <>
                      <img src={todayRecord.imageA} alt="A" loading="lazy" />
                      <Button
                        type="text"
                        size="small"
                        className="shang-zoom-btn"
                        icon={<ZoomInOutlined />}
                        onClick={(e) => {
                          e.stopPropagation()
                          const fullUrl = todayRecord.imageA!.startsWith('/') ? `${window.location.origin}${todayRecord.imageA}` : todayRecord.imageA!
                          setZoomingImage(fullUrl)
                        }}
                        title={t('mirror.shangZoomHint')}
                      />
                    </>
                  ) : (
                    <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f0f0f0', fontSize: 24, fontWeight: 700, color: '#999' }}>A</div>
                  )}
                </div>
                <div className="shang-image-desc">{todayRecord.descriptionA}</div>
              </div>
              {/* B 图片 */}
              <div
                className={`shang-image-card shang-image-card-selectable ${selectedChoice === 'B' ? 'selected' : ''}`}
                onClick={() => setSelectedChoice('B')}
              >
                <div className="shang-image-wrapper">
                  {todayRecord.imageB ? (
                    <>
                      <img src={todayRecord.imageB} alt="B" loading="lazy" />
                      <Button
                        type="text"
                        size="small"
                        className="shang-zoom-btn"
                        icon={<ZoomInOutlined />}
                        onClick={(e) => {
                          e.stopPropagation()
                          const fullUrl = todayRecord.imageB!.startsWith('/') ? `${window.location.origin}${todayRecord.imageB}` : todayRecord.imageB!
                          setZoomingImage(fullUrl)
                        }}
                        title={t('mirror.shangZoomHint')}
                      />
                    </>
                  ) : (
                    <div style={{ height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f0f0f0', fontSize: 24, fontWeight: 700, color: '#999' }}>B</div>
                  )}
                </div>
                <div className="shang-image-desc">{todayRecord.descriptionB}</div>
              </div>
            </div>

            <div style={{ marginTop: 8, width: '100%', maxWidth: 600 }}>
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

            <div className="shang-submit-row">
              <Button
                type="primary"
                shape="circle"
                size="large"
                className="shang-submit-round-btn"
                onClick={handleChoose}
                loading={submitting}
                disabled={!selectedChoice}
              >
                {t('mirror.shangSubmitBtn')}
              </Button>
              <Button
                shape="circle"
                size="large"
                className="shang-regenerate-round-btn"
                onClick={handleRegenerate}
                loading={regenerating}
                title={(!todayRecord.imageA || !todayRecord.imageB) ? t('mirror.shangRetryGenerate') : t('mirror.shangRegenerateImages')}
              >
                {t('mirror.shangRegenerateBtn')}
              </Button>
            </div>
            <Modal
              open={!!zoomingImage}
              footer={null}
              onCancel={() => setZoomingImage(null)}
              width="90vw"
              keyboard
              styles={{ body: { textAlign: 'center', padding: 24 } }}
            >
              {zoomingImage && <img src={zoomingImage} alt="放大" style={{ maxWidth: '100%', maxHeight: '80vh', objectFit: 'contain' }} />}
            </Modal>
          </div>
        )}

        {/* 已完成 或 查看历史 */}
        {selectedRecord && (selectedRecord.status === 'done' || todayDone) && (
          <div className="shang-record-detail">
            <h2 style={{ marginBottom: 16 }}>{selectedRecord.topic}</h2>
            <div className="shang-images" style={{ marginBottom: 24 }}>
              <div className={`shang-image-card ${selectedRecord.choice === 'A' ? 'selected' : ''}`}>
                {selectedRecord.imageA ? (
                  <img src={selectedRecord.imageA} alt="A" loading="lazy" />
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
                  <img src={selectedRecord.imageB} alt="B" loading="lazy" />
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
