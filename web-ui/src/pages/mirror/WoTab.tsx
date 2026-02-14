import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Spin, Button, Alert, message as antMessage, Progress, Popover } from 'antd'
import { UserOutlined, ThunderboltOutlined, WarningOutlined, CheckCircleOutlined, LineChartOutlined, SyncOutlined, DownloadOutlined, ExperimentOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { MirrorProfile, MbtiAnalysis } from '../../types'

const BIG_FIVE_COLORS: Record<string, string> = {
  openness: '#1890ff',
  conscientiousness: '#52c41a',
  extraversion: '#faad14',
  agreeableness: '#13c2c2',
  neuroticism: '#f5222d',
}

const MBTI_COLORS: Record<string, string> = {
  EI: '#722ed1',
  SN: '#13c2c2',
  TF: '#fa8c16',
  JP: '#eb2f96',
}

const MBTI_DESCRIPTIONS: Record<string, { name: string; desc: string }> = {
  INTJ: { name: '建筑师', desc: '富有想象力和战略性的思想家，一切皆在计划之中。具有独特的视角和强烈的内在驱动力，善于规划长期目标并坚定执行。' },
  INTP: { name: '逻辑学家', desc: '富有创造力的发明家，对知识有着永不满足的渴望。喜欢分析复杂系统，追求逻辑一致性，常常沉浸在抽象思考中。' },
  ENTJ: { name: '指挥官', desc: '大胆、富有想象力的领导者，总能找到解决方法。天生的组织者，善于制定战略并激励他人实现目标。' },
  ENTP: { name: '辩论家', desc: '聪明好奇的思想家，无法抗拒智力挑战。喜欢探索新想法，善于发现问题的不同角度，享受思想碰撞。' },
  INFJ: { name: '提倡者', desc: '安静而神秘，但能深刻启发他人。具有强烈的直觉和同理心，追求有意义的人际关系和崇高理想。' },
  INFP: { name: '调停者', desc: '诗意、善良的利他主义者，总是渴望帮助良善之事。内心世界丰富，追求真实自我和内在和谐。' },
  ENFJ: { name: '主人公', desc: '富有魅力的领导者，能够吸引听众。天生善于理解他人，乐于帮助他人成长和实现潜能。' },
  ENFP: { name: '竞选者', desc: '热情、有创造力的社交达人，总能找到微笑的理由。充满想象力和感染力，善于发现生活中的美好可能。' },
  ISTJ: { name: '物流师', desc: '务实、注重事实的人，其可靠性不容置疑。重视传统和责任，以有条理的方式完成任务。' },
  ISFJ: { name: '守卫者', desc: '非常敬业和温暖的守护者，随时准备保护所爱之人。忠诚可靠，善于关注他人的需求。' },
  ESTJ: { name: '总经理', desc: '出色的管理者，在管理事务和人员方面无与伦比。重视秩序和效率，善于组织资源达成目标。' },
  ESFJ: { name: '执政官', desc: '极具同情心、爱交际的人，总是渴望帮助他人。重视和谐的人际关系，善于营造温馨的氛围。' },
  ISTP: { name: '鉴赏家', desc: '大胆而实际的实验家，善于使用各种工具。喜欢动手解决问题，保持冷静理性的态度。' },
  ISFP: { name: '探险家', desc: '灵活而有魅力的艺术家，随时准备探索和体验新事物。追求自由和美感，活在当下。' },
  ESTP: { name: '企业家', desc: '聪明、精力充沛、善于感知的人，真正享受生活边缘。喜欢冒险和行动，善于把握机会。' },
  ESFP: { name: '表演者', desc: '自发、精力充沛的艺人，生活永远不会无聊。热爱生活，善于带动气氛，享受与人互动。' },
}

const JUNG_ARCHETYPES: Record<string, { name: string; desc: string }> = {
  '天真者': { name: '天真者', desc: '纯真、乐观、追求幸福。相信美好，渴望简单纯粹的生活，以善良和信任面对世界。' },
  '智者': { name: '智者', desc: '追求真理、知识与理解。以洞察力和智慧为指引，不断探索生命的深层意义。' },
  '探险家': { name: '探险家', desc: '追求自由、发现自我。渴望突破边界，在未知中寻找真实的自己。' },
  '统治者': { name: '统治者', desc: '追求控制、秩序与权力。具有领导才能，善于建立结构和管理资源。' },
  '创造者': { name: '创造者', desc: '追求创新与想象力。渴望将愿景变为现实，以独特方式表达自我。' },
  '照顾者': { name: '照顾者', desc: '保护、关怀他人。以无私的爱服务他人，在给予中找到意义。' },
  '英雄': { name: '英雄', desc: '证明价值、勇敢行动。面对挑战不屈不挠，为正义和理想而战。' },
  '反叛者': { name: '反叛者', desc: '打破常规、革命。挑战现状，推动变革，追求真正的自由。' },
  '情人': { name: '情人', desc: '追求亲密与激情。珍视感官体验和情感连接，热爱生活中的美好。' },
  '小丑': { name: '小丑', desc: '快乐、活在当下。以幽默和轻松的态度面对生活，带来欢笑。' },
  '普通人': { name: '普通人', desc: '追求归属感、平易近人。脚踏实地，与他人建立真诚的连接。' },
  '魔法师': { name: '魔法师', desc: '改变世界、愿景。相信转变的力量，将梦想转化为现实。' },
  'Sage': { name: '智者', desc: '追求真理、知识与理解。以洞察力和智慧为指引，不断探索生命的深层意义。' },
  'Innocent': { name: '天真者', desc: '纯真、乐观、追求幸福。相信美好，渴望简单纯粹的生活，以善良和信任面对世界。' },
  'Explorer': { name: '探险家', desc: '追求自由、发现自我。渴望突破边界，在未知中寻找真实的自己。' },
  'Ruler': { name: '统治者', desc: '追求控制、秩序与权力。具有领导才能，善于建立结构和管理资源。' },
  'Creator': { name: '创造者', desc: '追求创新与想象力。渴望将愿景变为现实，以独特方式表达自我。' },
  'Caregiver': { name: '照顾者', desc: '保护、关怀他人。以无私的爱服务他人，在给予中找到意义。' },
  'Hero': { name: '英雄', desc: '证明价值、勇敢行动。面对挑战不屈不挠，为正义和理想而战。' },
  'Outlaw': { name: '反叛者', desc: '打破常规、革命。挑战现状，推动变革，追求真正的自由。' },
  'Lover': { name: '情人', desc: '追求亲密与激情。珍视感官体验和情感连接，热爱生活中的美好。' },
  'Jester': { name: '小丑', desc: '快乐、活在当下。以幽默和轻松的态度面对生活，带来欢笑。' },
  'Everyman': { name: '普通人', desc: '追求归属感、平易近人。脚踏实地，与他人建立真诚的连接。' },
  'Magician': { name: '魔法师', desc: '改变世界、愿景。相信转变的力量，将梦想转化为现实。' },
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
  const mbti = profile.mbti as MbtiAnalysis | undefined

  const bigFiveKeys = ['openness', 'conscientiousness', 'extraversion', 'agreeableness', 'neuroticism'] as const

  const renderConfidenceDots = (confidence: number) => {
    const full = Math.floor(confidence / 25)
    const dots = []
    for (let i = 0; i < 4; i++) {
      dots.push(<span key={i} style={{ color: i < full ? '#faad14' : '#d9d9d9' }}>●</span>)
    }
    return <span style={{ marginLeft: 8 }}>{dots} ({confidence}%)</span>
  }

  const renderFunctionBar = (strength: number) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <Progress percent={strength} size="small" showInfo={false} strokeColor="#1890ff" style={{ flex: 1, margin: 0 }} />
      <span style={{ width: 40, textAlign: 'right', fontSize: 13 }}>{strength}</span>
    </div>
  )

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
        <p>
          <strong>{t('mirror.primaryArchetype')}:</strong> {String(jungArchetype.primary || '-')}
          {jungArchetype.primary && JUNG_ARCHETYPES[String(jungArchetype.primary)] && (
            <Popover
              trigger="click"
              placement="right"
              content={
                <div style={{ maxWidth: 300 }}>
                  <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 6 }}>
                    {JUNG_ARCHETYPES[String(jungArchetype.primary)].name}
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.6, color: '#666' }}>
                    {JUNG_ARCHETYPES[String(jungArchetype.primary)].desc}
                  </div>
                </div>
              }
            >
              <InfoCircleOutlined style={{ color: '#722ed1', cursor: 'pointer', fontSize: 14, marginLeft: 6 }} />
            </Popover>
          )}
        </p>
        <p>
          <strong>{t('mirror.secondaryArchetype')}:</strong> {String(jungArchetype.secondary || '-')}
          {jungArchetype.secondary && JUNG_ARCHETYPES[String(jungArchetype.secondary)] && (
            <Popover
              trigger="click"
              placement="right"
              content={
                <div style={{ maxWidth: 300 }}>
                  <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 6 }}>
                    {JUNG_ARCHETYPES[String(jungArchetype.secondary)].name}
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.6, color: '#666' }}>
                    {JUNG_ARCHETYPES[String(jungArchetype.secondary)].desc}
                  </div>
                </div>
              }
            >
              <InfoCircleOutlined style={{ color: '#722ed1', cursor: 'pointer', fontSize: 14, marginLeft: 6 }} />
            </Popover>
          )}
        </p>
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

      {/* MBTI人格分析 */}
      {mbti && (
        <div className="wo-profile-section">
          <h3><ExperimentOutlined /> {t('mirror.mbtiAnalysis') || 'MBTI人格分析'}</h3>
          
          {/* 类型概览 */}
          <div style={{ marginBottom: 16, padding: '12px 16px', background: '#f0f5ff', borderRadius: 8, border: '1px solid #d6e4ff' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
              <span style={{ fontSize: 28, fontWeight: 'bold', color: '#1890ff' }}>{mbti.当前类型 || '-'}</span>
              {mbti.当前类型 && MBTI_DESCRIPTIONS[mbti.当前类型] && (
                <Popover
                  trigger="click"
                  placement="right"
                  content={
                    <div style={{ maxWidth: 320 }}>
                      <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 8 }}>
                        {mbti.当前类型} - {MBTI_DESCRIPTIONS[mbti.当前类型].name}
                      </div>
                      <div style={{ fontSize: 13, lineHeight: 1.6, color: '#666' }}>
                        {MBTI_DESCRIPTIONS[mbti.当前类型].desc}
                      </div>
                    </div>
                  }
                >
                  <InfoCircleOutlined style={{ color: '#1890ff', cursor: 'pointer', fontSize: 16 }} />
                </Popover>
              )}
              <span style={{ color: '#666', fontSize: 13 }}>{mbti.历史类型分布 || ''}</span>
            </div>
            {mbti.类型漂移 && <div style={{ fontSize: 12, color: '#999' }}>{mbti.类型漂移}</div>}
          </div>

          {/* 基础维度分析 */}
          <h4 style={{ marginTop: 16, marginBottom: 12 }}>{t('mirror.mbtiDimensions') || '📊 基础维度分析'}</h4>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 16 }}>
            <thead>
              <tr style={{ background: '#fafafa' }}>
                <th style={{ padding: '8px', textAlign: 'left', border: '1px solid #f0f0f0' }}>维度</th>
                <th style={{ padding: '8px', textAlign: 'center', border: '1px solid #f0f0f0' }}>倾向</th>
                <th style={{ padding: '8px', textAlign: 'center', border: '1px solid #f0f0f0' }}>得分</th>
                <th style={{ padding: '8px', textAlign: 'center', border: '1px solid #f0f0f0' }}>置信度</th>
                <th style={{ padding: '8px', textAlign: 'left', border: '1px solid #f0f0f0' }}>关键证据</th>
              </tr>
            </thead>
            <tbody>
              {mbti.维度 && Object.entries(mbti.维度).map(([key, dim]) => (
                <tr key={key}>
                  <td style={{ padding: '8px', border: '1px solid #f0f0f0', fontWeight: 600, color: MBTI_COLORS[key] || '#666' }}>
                    {key === 'EI' && '外向(E) / 内向(I)'}
                    {key === 'SN' && '感觉(S) / 直觉(N)'}
                    {key === 'TF' && '思考(T) / 情感(F)'}
                    {key === 'JP' && '判断(J) / 知觉(P)'}
                  </td>
                  <td style={{ padding: '8px', border: '1px solid #f0f0f0', textAlign: 'center', fontWeight: 'bold' }}>{dim.倾向}</td>
                  <td style={{ padding: '8px', border: '1px solid #f0f0f0', textAlign: 'center' }}>{dim.得分}</td>
                  <td style={{ padding: '8px', border: '1px solid #f0f0f0', textAlign: 'center' }}>{renderConfidenceDots(dim.置信度)}</td>
                  <td style={{ padding: '8px', border: '1px solid #f0f0f0', fontSize: 12, color: '#666' }}>
                    {dim.关键证据?.map((e, i) => <div key={i}>• {e}</div>)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* 认知功能栈分析 */}
          {mbti.认知功能栈 && (
            <>
              <h4 style={{ marginTop: 16, marginBottom: 12 }}>{t('mirror.cognitiveFunctions') || '🧠 认知功能栈分析'}</h4>
              {Object.entries(mbti.认知功能栈).map(([role, func]) => (
                <div key={role} style={{ marginBottom: 12, padding: '10px 12px', background: '#fafafa', borderRadius: 6, border: '1px solid #f0f0f0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                    <span style={{ fontWeight: 600, color: '#1890ff' }}>
                      {role === '主导' && '🔥 '}{role === '辅助' && '💡 '}{role === '第三' && '🔸 '}{role === '劣势' && '🌑 '}
                      {role}：{func.功能}
                    </span>
                    <span style={{ fontSize: 12, color: '#999' }}>强度: {func.强度}/100</span>
                  </div>
                  {renderFunctionBar(func.强度)}
                  <div style={{ fontSize: 12, color: '#666', marginTop: 6 }}>{func.表现}</div>
                </div>
              ))}
            </>
          )}

          {/* 情境面具分析 */}
          {mbti.情境面具 && mbti.情境面具.length > 0 && (
            <>
              <h4 style={{ marginTop: 16, marginBottom: 12 }}>{t('mirror.contextMasks') || '🎭 情境面具分析'}</h4>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 16 }}>
                <thead>
                  <tr style={{ background: '#fafafa' }}>
                    <th style={{ padding: '8px', textAlign: 'left', border: '1px solid #f0f0f0' }}>情境</th>
                    <th style={{ padding: '8px', textAlign: 'center', border: '1px solid #f0f0f0' }}>显现类型</th>
                    <th style={{ padding: '8px', textAlign: 'center', border: '1px solid #f0f0f0' }}>面具厚度</th>
                  </tr>
                </thead>
                <tbody>
                  {mbti.情境面具.map((mask, i) => (
                    <tr key={i}>
                      <td style={{ padding: '8px', border: '1px solid #f0f0f0' }}>{mask.情境}</td>
                      <td style={{ padding: '8px', border: '1px solid #f0f0f0', textAlign: 'center', fontWeight: 'bold', color: '#722ed1' }}>{mask.显现类型}</td>
                      <td style={{ padding: '8px', border: '1px solid #f0f0f0', textAlign: 'center' }}>
                        {renderConfidenceDots(mask.面具厚度)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {/* 成长建议 */}
          {mbti.成长建议 && mbti.成长建议.length > 0 && (
            <>
              <h4 style={{ marginTop: 16, marginBottom: 12 }}>{t('mirror.growthSuggestions') || '📈 成长建议'}</h4>
              {mbti.成长建议.map((suggestion, i) => (
                <div key={i} style={{ marginBottom: 12, padding: '12px', background: '#f6ffed', borderRadius: 8, border: '1px solid #b7eb8f' }}>
                  <div style={{ fontWeight: 600, marginBottom: 6, color: '#52c41a' }}>💪 {suggestion.挑战}</div>
                  <div style={{ fontSize: 13, color: '#666', marginBottom: 4 }}>📝 练习: {suggestion.练习}</div>
                  <div style={{ fontSize: 12, color: '#1890ff' }}>🎯 预期: {suggestion.预期}</div>
                </div>
              ))}
            </>
          )}
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
