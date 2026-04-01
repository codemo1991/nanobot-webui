import React from 'react'
import { Collapse, Badge } from 'antd'
import { ToolOutlined } from '@ant-design/icons'
import { ToolStepCard } from './ToolStepCard'
import type { ToolStep } from '../types'
import './ToolStepsPanel.css'

interface ToolStepsPanelProps {
  steps: ToolStep[]
  showRunningOnLast?: boolean
  maxVisibleBeforeCollapse?: number
}

const COLLAPSE_THRESHOLD = 5

export const ToolStepsPanel: React.FC<ToolStepsPanelProps> = ({
  steps,
  showRunningOnLast = false,
  maxVisibleBeforeCollapse = COLLAPSE_THRESHOLD,
}) => {
  if (!steps || steps.length === 0) {
    return null
  }

  const runningCount = steps.filter(s => s.status === 'running').length
  const completedCount = steps.filter(s => s.status === 'completed').length

  const innerPanel = (
    <div className="tool-steps-list">
      {steps.map((step, index) => (
        <ToolStepCard
          key={step.id || index}
          step={step}
          isLast={index === steps.length - 1}
          defaultExpanded={showRunningOnLast && index === steps.length - 1 && !step.result}
        />
      ))}
    </div>
  )

  // 工具步骤过多时，外层使用 Collapse
  if (steps.length > maxVisibleBeforeCollapse) {
    return (
      <Collapse
        ghost
        size="small"
        className="tool-steps-outer-collapse"
        defaultActiveKey={runningCount > 0 ? ['tools'] : []}
        items={[
          {
            key: 'tools',
            label: (
              <span className="tool-steps-summary">
                <ToolOutlined style={{ marginRight: 8 }} />
                <Badge
                  count={runningCount}
                  style={{ backgroundColor: '#1890ff', marginRight: 8 }}
                  overflowCount={99}
                />
                <span>
                  {completedCount}/{steps.length} 工具已完成
                  {runningCount > 0 && ` (${runningCount} 运行中)`}
                </span>
              </span>
            ),
            children: innerPanel,
          },
        ]}
      />
    )
  }

  return innerPanel
}

export default ToolStepsPanel
