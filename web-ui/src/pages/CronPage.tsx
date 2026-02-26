import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Input, InputNumber, Switch, Button, Modal, Select, Card, Space, Tag, List, message, Radio, Spin, Tooltip, Popconfirm } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, PlayCircleOutlined, PauseCircleOutlined, SearchOutlined } from '@ant-design/icons'
import { cronApi } from '../api/cron'
import type { CronJob } from '../types/cron'
import './CronPage.css'

type FilterType = 'all' | 'enabled' | 'disabled'

export default function CronPage() {
  const [jobs, setJobs] = useState<CronJob[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<FilterType>('all')
  const [searchText, setSearchText] = useState('')
  const [showCalendarJobs, setShowCalendarJobs] = useState(false)  // 是否显示日历任务
  const [modalVisible, setModalVisible] = useState(false)
  const [editingJob, setEditingJob] = useState<CronJob | null>(null)
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const [runningJobId, setRunningJobId] = useState<string | null>(null)

  const { t } = useTranslation()

  useEffect(() => {
    loadJobs()
  }, [])

  const loadJobs = async () => {
    try {
      setLoading(true)
      const data = await cronApi.getJobs(true)
      setJobs(data.jobs)
    } catch (error) {
      console.error('Failed to load cron jobs:', error)
      message.error(t('cron.loadFailed'))
    } finally {
      setLoading(false)
    }
  }

  const filteredJobs = jobs.filter(job => {
    // Filter by calendar jobs (默认隐藏日历任务)
    if (!showCalendarJobs && job.source === 'calendar') return false
    // Filter by status
    if (filter === 'enabled' && !job.enabled) return false
    if (filter === 'disabled' && job.enabled) return false
    // Filter by search text
    if (searchText && !job.name.toLowerCase().includes(searchText.toLowerCase())) return false
    return true
  })

  const handleCreate = () => {
    setEditingJob(null)
    form.resetFields()
    form.setFieldsValue({
      triggerType: 'cron',
      tz: 'Asia/Shanghai',
      payloadKind: 'agent_turn',
      deliver: false,
      deleteAfterRun: false,
    })
    setModalVisible(true)
  }

  const handleEdit = (job: CronJob) => {
    setEditingJob(job)
    form.setFieldsValue({
      name: job.name,
      triggerType: job.trigger.type,
      triggerDateMs: job.trigger.dateMs,
      triggerIntervalSeconds: job.trigger.intervalSeconds,
      triggerCronExpr: job.trigger.cronExpr,
      tz: job.trigger.tz || 'Asia/Shanghai',
      payloadKind: job.payload.kind,
      payloadMessage: job.payload.message,
      deliver: job.payload.deliver,
      payloadChannel: job.payload.channel,
      payloadTo: job.payload.to,
      deleteAfterRun: job.deleteAfterRun,
    })
    setModalVisible(true)
  }

  const handleDelete = async (jobId: string) => {
    try {
      await cronApi.deleteJob(jobId)
      message.success(t('cron.deleted'))
      loadJobs()
    } catch (error) {
      console.error('Failed to delete cron job:', error)
      message.error(t('cron.deleteFailed'))
    }
  }

  const handleToggleEnabled = async (job: CronJob) => {
    try {
      await cronApi.updateJob(job.id, { enabled: !job.enabled })
      message.success(job.enabled ? t('cron.paused') : t('cron.enabled'))
      loadJobs()
    } catch (error) {
      console.error('Failed to toggle cron job:', error)
      message.error(t('cron.updateFailed'))
    }
  }

  const handleRun = async (jobId: string) => {
    try {
      setRunningJobId(jobId)
      await cronApi.runJob(jobId)
      message.success(t('cron.triggered'))
      loadJobs()
    } catch (error) {
      console.error('Failed to run cron job:', error)
      message.error(t('cron.runFailed'))
    } finally {
      setRunningJobId(null)
    }
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSubmitting(true)

      // Convert datetime-local string to milliseconds
      let triggerDateMs: number | undefined
      if (values.triggerType === 'at' && values.triggerDateMs) {
        triggerDateMs = new Date(values.triggerDateMs).getTime()
      }

      // Build flat API payload (backend expects flat fields, not nested objects)
      const jobData = {
        name: values.name,
        triggerType: values.triggerType,
        triggerDateMs: values.triggerType === 'at' ? triggerDateMs : undefined,
        triggerIntervalSeconds: values.triggerType === 'every' ? values.triggerIntervalSeconds : undefined,
        triggerCronExpr: values.triggerType === 'cron' ? values.triggerCronExpr : undefined,
        triggerTz: values.tz,
        payloadKind: values.payloadKind,
        payloadMessage: values.payloadMessage || '',
        payloadDeliver: values.deliver || false,
        payloadChannel: values.payloadChannel,
        payloadTo: values.payloadTo,
        deleteAfterRun: values.deleteAfterRun,
      }

      if (editingJob) {
        await cronApi.updateJob(editingJob.id, jobData)
        message.success(t('cron.updated'))
      } else {
        await cronApi.createJob(jobData)
        message.success(t('cron.created'))
      }

      setModalVisible(false)
      loadJobs()
    } catch (error) {
      console.error('Failed to save cron job:', error)
      message.error(t('cron.saveFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  const formatNextRunTime = (timestamp?: number) => {
    if (!timestamp) return '-'
    return new Date(timestamp).toLocaleString()
  }

  const formatLastRunTime = (timestamp?: number) => {
    if (!timestamp) return '-'
    return new Date(timestamp).toLocaleString()
  }

  const getStatusTag = (job: CronJob) => {
    if (!job.enabled) {
      return <Tag>{t('cron.status.disabled')}</Tag>
    }
    if (job.lastStatus === 'running') {
      return <Tag color="processing">{t('cron.status.running')}</Tag>
    }
    if (job.lastStatus === 'success') {
      return <Tag color="success">{t('cron.status.success')}</Tag>
    }
    if (job.lastStatus === 'failed') {
      return <Tag color="error">{t('cron.status.failed')}</Tag>
    }
    return <Tag color="default">{t('cron.status.pending')}</Tag>
  }

  const getTriggerText = (job: CronJob) => {
    const { trigger } = job
    if (trigger.type === 'at') {
      return formatNextRunTime(trigger.dateMs)
    }
    if (trigger.type === 'every') {
      const seconds = trigger.intervalSeconds || 0
      if (seconds >= 3600) {
        return t('cron.triggerEveryHours', { hours: Math.floor(seconds / 3600) })
      }
      if (seconds >= 60) {
        return t('cron.triggerEveryMinutes', { minutes: Math.floor(seconds / 60) })
      }
      return t('cron.triggerEverySeconds', { seconds })
    }
    if (trigger.type === 'cron') {
      return trigger.cronExpr || '-'
    }
    return '-'
  }

  return (
    <div className="cron-page">
      <div className="page-header">
        <div className="page-header-content">
          <h1>⏰ {t('cron.title')}</h1>
          <p className="page-description">{t('cron.description')}</p>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          {t('cron.addJob')}
        </Button>
      </div>

      <div className="cron-toolbar">
        <Input
          placeholder={t('cron.searchPlaceholder')}
          prefix={<SearchOutlined />}
          value={searchText}
          onChange={e => setSearchText(e.target.value)}
          style={{ width: 240 }}
          allowClear
        />
        <Radio.Group value={filter} onChange={e => setFilter(e.target.value)} buttonStyle="solid">
          <Radio.Button value="all">{t('cron.filterAll')}</Radio.Button>
          <Radio.Button value="enabled">{t('cron.filterEnabled')}</Radio.Button>
          <Radio.Button value="disabled">{t('cron.filterDisabled')}</Radio.Button>
        </Radio.Group>
        <Switch
          checked={showCalendarJobs}
          onChange={setShowCalendarJobs}
          checkedChildren="显示日历任务"
          unCheckedChildren="隐藏日历任务"
        />
      </div>

      <div className="cron-content">
        {loading ? (
          <div className="loading-container"><Spin size="large" /></div>
        ) : filteredJobs.length === 0 ? (
          <div className="empty-state">
            <p>{t('cron.empty')}</p>
          </div>
        ) : (
          <List
            grid={{ gutter: 16, column: 2 }}
            dataSource={filteredJobs}
            renderItem={job => (
              <List.Item>
                <Card
                  className={`cron-job-card ${!job.enabled ? 'disabled' : ''}`}
                  title={
                    <Space>
                      <span className="job-name">{job.name}</span>
                      {job.is_system && <Tag color="purple">{t('cron.systemJob')}</Tag>}
                      {job.source === 'calendar' && <Tag color="cyan">{t('cron.calendarJob')}</Tag>}
                      {getStatusTag(job)}
                    </Space>
                  }
                  extra={
                    <Space>
                      <Tooltip title={job.enabled ? t('cron.pause') : t('cron.enable')}>
                        <Switch
                          checked={job.enabled}
                          onChange={() => handleToggleEnabled(job)}
                          checkedChildren={<PauseCircleOutlined />}
                          unCheckedChildren={<PlayCircleOutlined />}
                        />
                      </Tooltip>
                      <Tooltip title={t('cron.runNow')}>
                        <Button
                          type="text"
                          icon={<PlayCircleOutlined />}
                          onClick={() => handleRun(job.id)}
                          loading={runningJobId === job.id}
                          disabled={!job.enabled}
                        />
                      </Tooltip>
                      <Tooltip title={t('cron.edit')}>
                        <Button
                          type="text"
                          icon={<EditOutlined />}
                          onClick={() => handleEdit(job)}
                        />
                      </Tooltip>
                      {job.is_system ? (
                        <Tooltip title={t('cron.cannotDeleteSystemJob')}>
                          <Button type="text" danger icon={<DeleteOutlined />} disabled />
                        </Tooltip>
                      ) : (
                        <Popconfirm
                          title={t('cron.confirmDelete')}
                          onConfirm={() => handleDelete(job.id)}
                          okText={t('cron.yes')}
                          cancelText={t('cron.no')}
                        >
                          <Tooltip title={t('cron.delete')}>
                            <Button type="text" danger icon={<DeleteOutlined />} />
                          </Tooltip>
                        </Popconfirm>
                      )}
                    </Space>
                  }
                >
                  <Space direction="vertical" style={{ width: '100%' }} size="small">
                    <div className="job-info">
                      <span className="job-label">{t('cron.trigger')}:</span>
                      <span className="job-value">{getTriggerText(job)}</span>
                    </div>
                    {job.payload.message && (
                      <div className="job-info">
                        <span className="job-label">{t('cron.message')}:</span>
                        <span className="job-value">{job.payload.message.slice(0, 50)}{job.payload.message.length > 50 ? '...' : ''}</span>
                      </div>
                    )}
                    <div className="job-info">
                      <span className="job-label">{t('cron.nextRun')}:</span>
                      <span className="job-value">{formatNextRunTime(job.nextRunAtMs)}</span>
                    </div>
                    <div className="job-info">
                      <span className="job-label">{t('cron.lastRun')}:</span>
                      <span className="job-value">{formatLastRunTime(job.lastRunAtMs)}</span>
                    </div>
                    {job.lastError && (
                      <div className="job-error">
                        <span className="job-label">{t('cron.lastError')}:</span>
                        <span className="job-value error">{job.lastError}</span>
                      </div>
                    )}
                  </Space>
                </Card>
              </List.Item>
            )}
          />
        )}
      </div>

      <Modal
        title={editingJob ? t('cron.editJob') : t('cron.createJob')}
        open={modalVisible}
        onOk={handleSave}
        onCancel={() => setModalVisible(false)}
        width={600}
        confirmLoading={submitting}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t('cron.jobName')}
            rules={[{ required: true, message: t('cron.jobNameRequired') }]}
            help={editingJob?.is_system ? t('cron.systemJobTip') : undefined}
          >
            <Input placeholder={t('cron.jobNamePlaceholder')} disabled={editingJob?.is_system} />
          </Form.Item>

          <Form.Item name="triggerType" label={t('cron.triggerType')} rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'cron', label: t('cron.triggerCron') },
                { value: 'every', label: t('cron.triggerInterval') },
                { value: 'at', label: t('cron.triggerOnce') },
              ]}
              disabled={editingJob?.is_system}
            />
          </Form.Item>

          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.triggerType !== curr.triggerType}>
            {({ getFieldValue }) => {
              const triggerType = getFieldValue('triggerType')
              return (
                <>
                  {triggerType === 'cron' && (
                    <Form.Item name="triggerCronExpr" label={t('cron.cronExpr')} rules={[{ required: true, message: t('cron.cronExprRequired') }]}>
                      <Input placeholder="0 * * * *" />
                    </Form.Item>
                  )}
                  {triggerType === 'every' && (
                    <Form.Item name="triggerIntervalSeconds" label={t('cron.interval')} rules={[{ required: true, message: t('cron.intervalRequired') }]}>
                      <InputNumber min={1} style={{ width: '100%' }} placeholder={t('cron.intervalPlaceholder')} />
                    </Form.Item>
                  )}
                  {triggerType === 'at' && (
                    <Form.Item name="triggerDateMs" label={t('cron.triggerTime')}>
                      <Input type="datetime-local" />
                    </Form.Item>
                  )}
                  {triggerType && (
                    <Form.Item name="tz" label={t('cron.timezone')}>
                      <Select
                        options={[
                          { value: 'Asia/Shanghai', label: 'Asia/Shanghai (UTC+8)' },
                          { value: 'UTC', label: 'UTC' },
                          { value: 'America/New_York', label: 'America/New_York (UTC-5)' },
                          { value: 'Europe/London', label: 'Europe/London (UTC+0)' },
                        ]}
                      />
                    </Form.Item>
                  )}
                </>
              )
            }}
          </Form.Item>

          <Form.Item name="payloadKind" label={t('cron.payloadKind')}>
            <Select
              options={[
                { value: 'agent_turn', label: t('cron.payloadAgentTurn') },
                { value: 'system_event', label: t('cron.payloadSystemEvent') },
              ]}
            />
          </Form.Item>

          <Form.Item name="payloadMessage" label={t('cron.message')}>
            <Input.TextArea rows={3} placeholder={t('cron.messagePlaceholder')} />
          </Form.Item>

          <Form.Item name="deliver" valuePropName="checked" label={t('cron.deliver')}>
            <Switch />
          </Form.Item>

          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.deliver !== curr.deliver}>
            {({ getFieldValue }) => getFieldValue('deliver') && (
              <>
                <Form.Item name="payloadChannel" label={t('cron.channel')}>
                  <Select
                    options={[
                      { value: 'telegram', label: 'Telegram' },
                      { value: 'feishu', label: 'Feishu' },
                      { value: 'whatsapp', label: 'WhatsApp' },
                      { value: 'discord', label: 'Discord' },
                    ]}
                    placeholder={t('cron.channelPlaceholder')}
                  />
                </Form.Item>
                <Form.Item name="payloadTo" label={t('cron.recipient')}>
                  <Input placeholder={t('cron.recipientPlaceholder')} />
                </Form.Item>
              </>
            )}
          </Form.Item>

          <Form.Item name="deleteAfterRun" valuePropName="checked" label={t('cron.deleteAfterRun')}>
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
