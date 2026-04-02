import React, { useState, useMemo, memo } from 'react'
import {
  Progress,
  Space,
  Typography,
  Button,
  Tooltip,
} from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ClockCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined,
  ToolOutlined,
  DownOutlined,
  RightOutlined,
} from '@ant-design/icons'
import type { ToolStep } from '../types'
import './ToolStepCard.css'

const { Text } = Typography

interface ToolStepCardProps {
  step: ToolStep
  isLast: boolean
  defaultExpanded?: boolean
}

// Max output chunks to prevent memory bloat
const MAX_OUTPUT_CHUNKS = 100
const PREVIEW_LINES = 10

const statusConfig = {
  pending: {
    icon: <ClockCircleOutlined />,
    color: '#8c8c8c',
    text: '等待中',
    tagColor: 'default' as const,
  },
  running: {
    icon: <LoadingOutlined spin />,
    color: '#1890ff',
    text: '运行中',
    tagColor: 'processing' as const,
  },
  waiting: {
    icon: <PauseCircleOutlined />,
    color: '#faad14',
    text: '等待输入',
    tagColor: 'warning' as const,
  },
  completed: {
    icon: <CheckCircleOutlined />,
    color: '#52c41a',
    text: '已完成',
    tagColor: 'success' as const,
  },
  error: {
    icon: <CloseCircleOutlined />,
    color: '#ff4d4f',
    text: '执行失败',
    tagColor: 'error' as const,
  },
}

// Memoized component to prevent unnecessary re-renders
// Only re-render when step data, expanded state, or isLast actually changes
export const ToolStepCard: React.FC<ToolStepCardProps> = memo(({
  step,
  isLast,
  defaultExpanded = false,
}) => {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded)
  const [outputExpanded, setOutputExpanded] = useState(false)
  const [paramsExpanded, setParamsExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const [runningSeconds, setRunningSeconds] = useState(0)
  const timerRef = React.useRef<ReturnType<typeof setInterval> | null>(null)

  React.useEffect(() => {
    if (step.status === 'running') {
      const start = step.startTime || Date.now()
      setRunningSeconds(0)
      timerRef.current = setInterval(() => {
        setRunningSeconds((Date.now() - start) / 1000)
      }, 100)
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [step.status, step.startTime])

  const status = step.status || 'pending'
  const config = statusConfig[status]

  const durationMs = step.endTime && step.startTime ? step.endTime - step.startTime : 0
  const durationStr = durationMs > 0
    ? durationMs < 1000
      ? `${durationMs}ms`
      : durationMs < 60000
        ? `${(durationMs / 1000).toFixed(1)}s`
        : `${(durationMs / 60000).toFixed(1)}min`
    : null

  const StatusBadge = () => {
    if (status === 'running') {
      return (
        <span className="tool-status-badge running">
          运行中 {runningSeconds.toFixed(1)}s
        </span>
      )
    }
    if (status === 'completed') {
      return (
        <span className="tool-status-badge done">
          ✅ Done {durationStr ?? '--'}
        </span>
      )
    }
    if (status === 'error') {
      return (
        <span className="tool-status-badge error">
          ❌ Error {durationStr ?? '--'}
        </span>
      )
    }
    return null
  }

  // 自动展开运行中的步骤
  React.useEffect(() => {
    if (status === 'running' && isLast) {
      setIsExpanded(true)
    }
  }, [status, isLast])

  // 完成后自动收起（如果是自动展开的）
  const wasAutoExpanded = React.useRef(false)
  React.useEffect(() => {
    if (status === 'completed' && wasAutoExpanded.current) {
      setIsExpanded(false)
      wasAutoExpanded.current = false
    }
    if (status === 'running' && isLast) {
      wasAutoExpanded.current = true
    }
  }, [status, isLast])

  const args = useMemo(() => {
    if (typeof step.arguments === 'string') {
      try {
        return JSON.parse(step.arguments)
      } catch {
        return {}
      }
    }
    return step.arguments || {}
  }, [step.arguments])

  const handleCopy = async () => {
    const text = step.result || ''
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard not available
    }
  }

  // Reset outputExpanded when step.result changes (new result)
  React.useEffect(() => {
    setOutputExpanded(false)
  }, [step.result])

  const hasOutputChunks = step.outputChunks && step.outputChunks.length > 0
  const showProgress = status === 'running' && step.progress
  const resultLines = (step.result || '').split('\n')
  const needsOutputCollapse = resultLines.length > PREVIEW_LINES
  const outputPreview = needsOutputCollapse && !outputExpanded
    ? resultLines.slice(0, PREVIEW_LINES).join('\n')
    : step.result || ''
  const hiddenCount = resultLines.length - PREVIEW_LINES

  const PREVIEW_PARAMS = 5
  const paramEntries = Object.entries(args)
  const needsParamsCollapse = paramEntries.length > PREVIEW_PARAMS
  const visibleParams = paramsExpanded || !needsParamsCollapse
    ? paramEntries
    : paramEntries.slice(0, PREVIEW_PARAMS)
  const hiddenParamsCount = paramEntries.length - PREVIEW_PARAMS

  return (
    <div className={`tool-step-card ${status}`}>
      <div
        className="tool-step-header"
        onClick={() => setIsExpanded(!isExpanded)}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            setIsExpanded(!isExpanded)
          }
        }}
      >
        <Space>
          <span className="tool-step-icon" style={{ color: config.color }}>
            {config.icon}
          </span>
          <ToolOutlined />
          <Text strong>{step.name}</Text>
          <StatusBadge />
        </Space>
        <Button
          type="text"
          size="small"
          icon={isExpanded ? <DownOutlined /> : <RightOutlined />}
        />
      </div>

      {isExpanded && (
        <div className="tool-step-content">
          {/* 参数显示 */}
          {Object.keys(args).length > 0 && (
            <div className="tool-step-section">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>参数</Text>
                {!paramsExpanded && needsParamsCollapse && (
                  <button
                    className="params-toggle-btn"
                    onClick={(e) => { e.stopPropagation(); setParamsExpanded(true) }}
                  >
                    [▼ {paramEntries.length}]
                  </button>
                )}
                {paramsExpanded && (
                  <button
                    className="params-toggle-btn"
                    onClick={(e) => { e.stopPropagation(); setParamsExpanded(false) }}
                  >
                    [▲ 隐藏]
                  </button>
                )}
              </div>
              <div className="tool-params-body">
                {visibleParams.map(([k, v]) => {
                  const strV = typeof v === 'object' ? JSON.stringify(v) : String(v)
                  return (
                    <div key={k} className="param-row">
                      <span className="param-key">{k}:</span>
                      {strV.length > 80
                        ? <Tooltip title={strV} mouseEnterDelay={0.5}><span className="param-value overflow">{truncate(strV, 80)}</span></Tooltip>
                        : <span className="param-value">{strV}</span>
                      }
                    </div>
                  )
                })}
                {paramsExpanded && needsParamsCollapse && (
                  <button
                    className="params-toggle-btn"
                    style={{ marginTop: 4 }}
                    onClick={(e) => { e.stopPropagation(); setParamsExpanded(false) }}
                  >
                    [▲ 收起 {hiddenParamsCount} 项]
                  </button>
                )}
              </div>
            </div>
          )}

          {/* 进度显示 */}
          {showProgress && (
            <div className="tool-step-section">
              <Progress
                percent={step.progress?.percent}
                status="active"
                size="small"
                format={(percent) => percent ? `${percent}%` : ''}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                {step.progress?.detail}
              </Text>
            </div>
          )}

          {/* 实时输出流 */}
          {hasOutputChunks && status === 'running' && (
            <div className="tool-step-section">
              <Text type="secondary" style={{ fontSize: 12 }}>实时输出</Text>
              <div className="tool-output-wrap">
                <pre className="tool-output-collapsed">
                  {(step.outputChunks || []).slice(-MAX_OUTPUT_CHUNKS).map((chunk, i) => (
                    <span
                      key={i}
                      className={chunk.isError ? 'output-error' : 'output-normal'}
                    >
                      {chunk.chunk}
                    </span>
                  ))}
                </pre>
              </div>
            </div>
          )}

          {/* 结果展示 — 折叠预览 + 操作按钮 */}
          {step.result && status === 'completed' && (
            <div className="tool-step-section">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>执行结果</Text>
                <div className="output-actions">
                  {needsOutputCollapse && !outputExpanded && (
                    <button className="output-action-btn" onClick={() => setOutputExpanded(true)}>
                      [展开全部 {resultLines.length} 行]
                    </button>
                  )}
                  {outputExpanded && (
                    <button className="output-action-btn" onClick={() => setOutputExpanded(false)}>
                      [收起]
                    </button>
                  )}
                  <button className={`output-action-btn ${copied ? 'copied' : ''}`} onClick={handleCopy}>
                    {copied ? '[已复制 ✓]' : '[复制结果]'}
                  </button>
                </div>
              </div>
              <div className="tool-output-wrap">
                <pre className={outputExpanded ? 'tool-output-expanded' : 'tool-output-collapsed'}>
                  {outputPreview}
                </pre>
                {needsOutputCollapse && !outputExpanded && (
                  <div
                    className="output-hidden-anchor"
                    onClick={() => setOutputExpanded(true)}
                  >
                    ⚠ {hiddenCount} hidden rows — click to expand
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}, (prevProps, nextProps) => {
  const prev = prevProps.step
  const next = nextProps.step
  return (
    prev.id === next.id &&
    prev.status === next.status &&
    prev.result === next.result &&
    prev.name === next.name &&
    prev.durationMs === next.durationMs &&
    prev.startTime === next.startTime &&
    prev.endTime === next.endTime &&
    prev.progress?.detail === next.progress?.detail &&
    prev.progress?.percent === next.progress?.percent &&
    Math.abs((prev.outputChunks?.length ?? 0) - (next.outputChunks?.length ?? 0)) < 10 &&
    prevProps.isLast === nextProps.isLast &&
    prevProps.defaultExpanded === nextProps.defaultExpanded
  )
})

function truncate(str: string, maxLen: number): string {
  return str.length > maxLen ? str.slice(0, maxLen) + '…' : str
}

export default ToolStepCard
