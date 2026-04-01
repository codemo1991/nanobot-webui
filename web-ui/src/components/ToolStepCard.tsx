import React, { useState, useMemo } from 'react'
import {
  Tag,
  Progress,
  Space,
  Typography,
  Button,
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

export const ToolStepCard: React.FC<ToolStepCardProps> = ({
  step,
  isLast,
  defaultExpanded = false,
}) => {
  const [isExpanded, setIsExpanded] = useState(defaultExpanded)

  const status = step.status || 'pending'
  const config = statusConfig[status]

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

  const hasOutputChunks = step.outputChunks && step.outputChunks.length > 0
  const showProgress = status === 'running' && step.progress

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
          <Tag color={config.tagColor}>{config.text}</Tag>
          {step.durationMs && status === 'completed' && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {formatDuration(step.durationMs)}
            </Text>
          )}
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
              <Text type="secondary" style={{ fontSize: 12 }}>参数</Text>
              <pre className="tool-step-code">
                {JSON.stringify(args, null, 2)}
              </pre>
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
              <pre className="tool-step-output">
                {step.outputChunks?.map((chunk, i) => (
                  <span
                    key={i}
                    className={chunk.isError ? 'output-error' : 'output-normal'}
                  >
                    {chunk.chunk}
                  </span>
                ))}
              </pre>
            </div>
          )}

          {/* 结果展示 */}
          {step.result && status === 'completed' && (
            <div className="tool-step-section">
              <Text type="secondary" style={{ fontSize: 12 }}>执行结果</Text>
              <pre className="tool-step-code">{step.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

export default ToolStepCard
