import { useEffect, useState } from 'react'
import { Modal, Form, Input, DatePicker, Select, Radio, Switch, Row, Col, Button, message } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import dayjs from 'dayjs'
import { useCalendarStore } from '../../store/calendarStore'
import { api } from '../../api'
import { priorityColors, reminderOptions, type Reminder, type RecurrenceRule } from '../../types'
import { v4 as uuidv4 } from 'uuid'

type Priority = 'high' | 'medium' | 'low'

const { TextArea } = Input

// 简化的重复规则选项
const recurrenceOptions = [
  { label: '不重复', value: 'none' },
  { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
]

// 提醒时间选项（单选）
const reminderTimeOptions = reminderOptions.map((opt) => ({
  label: opt.label,
  value: opt.value,
}))

function EventModal() {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [channels, setChannels] = useState<{ id: string; name: string }[]>([])
  const {
    isEventModalOpen,
    editingEventId,
    selectedEvent,
    settings,
    setEventModalOpen,
    addEvent,
    updateEvent,
    deleteEvent,
  } = useCalendarStore()

  const isEditing = !!editingEventId

  // 加载渠道列表
  useEffect(() => {
    api.getEnabledChannels().then((data) => {
      // 防御性处理：确保 data 是数组
      if (Array.isArray(data)) {
        setChannels(data)
      } else if (data && typeof data === 'object') {
        // 如果返回的是对象（如 {feishu: {...}, telegram: {...}}），转换为数组
        const channelList: { id: string; name: string }[] = []
        for (const [key, value] of Object.entries(data)) {
          if (value && typeof value === 'object' && (value as any).enabled) {
            const nameMap: Record<string, string> = {
              feishu: '飞书',
              whatsapp: 'WhatsApp',
              telegram: 'Telegram',
              discord: 'Discord',
              qq: 'QQ',
              dingtalk: '钉钉',
            }
            channelList.push({ id: key, name: nameMap[key] || key })
          }
        }
        setChannels(channelList)
      } else {
        setChannels([])
      }
    }).catch((err) => {
      console.error('Failed to load channels:', err)
      setChannels([])
    })
  }, [])

  // Reset form when modal opens/closes or selectedEvent changes
  useEffect(() => {
    if (isEventModalOpen) {
      if (selectedEvent && editingEventId) {
        // Editing existing event
        const recurrence = selectedEvent.recurrence
        const reminder = (selectedEvent.reminders || [])[0]  // 只取第一个提醒
        form.setFieldsValue({
          title: selectedEvent.title,
          description: selectedEvent.description,
          start: selectedEvent.start ? dayjs(selectedEvent.start) : null,
          end: selectedEvent.end ? dayjs(selectedEvent.end) : null,
          priority: selectedEvent.priority,
          isAllDay: selectedEvent.isAllDay,
          reminderTime: reminder?.time ?? -1,  // 单选提醒时间
          reminderChannel: reminder?.channel ?? '',  // 推送渠道
          recurrence: recurrence ? recurrence.frequency : 'none',
        })
      } else {
        // Creating new event
        const defaultStart = selectedEvent?.start ? dayjs(selectedEvent.start) : dayjs()
        const defaultEnd = selectedEvent?.end ? dayjs(selectedEvent.end) : dayjs().add(1, 'hour')

        form.setFieldsValue({
          title: '',
          description: '',
          start: defaultStart,
          end: defaultEnd,
          priority: settings.defaultPriority,
          isAllDay: selectedEvent?.isAllDay ?? false,
          reminderTime: -1,
          reminderChannel: '',
          recurrence: 'none',
        })
      }
    }
  }, [isEventModalOpen, selectedEvent, editingEventId, form, settings.defaultPriority])

  // Handle modal close
  const handleCancel = () => {
    setEventModalOpen(false)
    form.resetFields()
  }

  // Handle form submit
  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()

      // 构建简化的重复规则
      let recurrence: RecurrenceRule | undefined
      if (values.recurrence && values.recurrence !== 'none') {
        recurrence = {
          frequency: values.recurrence as RecurrenceRule['frequency'],
          interval: 1,
          endType: 'never',
        }
      }

      // 构建提醒配置（只支持1个提醒）
      const reminders: Reminder[] = []
      if (values.reminderTime !== undefined && values.reminderTime !== -1) {
        reminders.push({
          id: uuidv4(),
          time: values.reminderTime,
          notified: false,
          channel: values.reminderChannel || undefined,
          target: undefined,  // TODO: 后续可以添加目标选择
        })
      }

      const eventData = {
        title: values.title,
        description: values.description || '',
        start: values.start.toISOString(),
        end: values.end.toISOString(),
        priority: values.priority as Priority,
        isAllDay: values.isAllDay || false,
        reminders,
        recurrence,
      }

      // Validate end time is after start time
      if (new Date(eventData.end) <= new Date(eventData.start)) {
        message.error(t('calendar.endTimeError') || '结束时间必须晚于开始时间')
        return
      }

      if (isEditing && editingEventId) {
        updateEvent(editingEventId, eventData)
        message.success(t('calendar.eventUpdated') || '事件已更新')
      } else {
        addEvent(eventData)
        message.success(t('calendar.eventAdded') || '事件已添加')
      }

      handleCancel()
    } catch (error) {
      console.error('Form validation failed:', error)
    }
  }

  // Handle event deletion
  const handleDelete = () => {
    if (editingEventId) {
      deleteEvent(editingEventId)
      message.success(t('calendar.eventDeleted') || '事件已删除')
      handleCancel()
    }
  }

  return (
    <Modal
      title={isEditing ? t('calendar.editEvent') : t('calendar.addEvent')}
      open={isEventModalOpen}
      onCancel={handleCancel}
      footer={[
        isEditing && (
          <Button
            key="delete"
            danger
            icon={<DeleteOutlined />}
            onClick={handleDelete}
          >
            {t('calendar.delete')}
          </Button>
        ),
        <Button key="cancel" onClick={handleCancel}>
          {t('calendar.cancel')}
        </Button>,
        <Button key="submit" type="primary" onClick={handleSubmit}>
          {t('calendar.save')}
        </Button>,
      ].filter(Boolean)}
      width={600}
      destroyOnClose
    >
      <Form
        form={form}
        layout="vertical"
        requiredMark="optional"
      >
        <Form.Item
          name="title"
          label={t('calendar.eventTitle')}
          rules={[{ required: true, message: t('calendar.titleRequired') || '请输入事件标题' }]}
        >
          <Input placeholder={t('calendar.eventTitlePlaceholder')} />
        </Form.Item>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              name="start"
              label={t('calendar.startTime')}
              rules={[{ required: true, message: t('calendar.startTimeRequired') || '请选择开始时间' }]}
            >
              <DatePicker
                showTime={{ format: 'HH:mm' }}
                format="YYYY-MM-DD HH:mm"
                style={{ width: '100%' }}
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="end"
              label={t('calendar.endTime')}
              rules={[{ required: true, message: t('calendar.endTimeRequired') || '请选择结束时间' }]}
            >
              <DatePicker
                showTime={{ format: 'HH:mm' }}
                format="YYYY-MM-DD HH:mm"
                style={{ width: '100%' }}
              />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item name="isAllDay" valuePropName="checked">
          <Switch checkedChildren={t('calendar.allDay')} unCheckedChildren="" />
        </Form.Item>

        <Form.Item
          name="priority"
          label={t('calendar.priority')}
        >
          <Radio.Group>
            <Radio.Button value="high">
              <span style={{ color: priorityColors.high.text }}>
                {t('calendar.priorityHigh')}
              </span>
            </Radio.Button>
            <Radio.Button value="medium">
              <span style={{ color: priorityColors.medium.text }}>
                {t('calendar.priorityMedium')}
              </span>
            </Radio.Button>
            <Radio.Button value="low">
              <span style={{ color: priorityColors.low.text }}>
                {t('calendar.priorityLow')}
              </span>
            </Radio.Button>
          </Radio.Group>
        </Form.Item>

        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              name="reminderTime"
              label="提醒时间"
            >
              <Select
                placeholder="不提醒"
                options={reminderTimeOptions}
                allowClear
              />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              name="reminderChannel"
              label="推送渠道"
            >
              <Select
                placeholder="选择渠道（可选）"
                allowClear
                options={channels.map((ch) => ({ label: ch.name, value: ch.id }))}
              />
            </Form.Item>
          </Col>
        </Row>

        <Form.Item
          name="description"
          label={t('calendar.description')}
        >
          <TextArea
            rows={3}
            placeholder={t('calendar.descriptionPlaceholder')}
          />
        </Form.Item>

        {/* 简化后的重复规则 */}
        <Form.Item
          name="recurrence"
          label="重复"
        >
          <Select
            options={recurrenceOptions}
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default EventModal
