import { useEffect } from 'react'
import { Modal, Form, Input, DatePicker, Select, Radio, Switch, Row, Col, Button, message } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import dayjs from 'dayjs'
import { useCalendarStore } from '../../store/calendarStore'
import { priorityColors, reminderOptions } from '../../types/calendar'
import type { Priority, Reminder, RecurrenceRule } from '../../types/calendar'
import { v4 as uuidv4 } from 'uuid'

const { TextArea } = Input

// Recurrence frequency options
const recurrenceOptions = [
  { label: '不重复', value: 'none' },
  { label: '每天', value: 'daily' },
  { label: '每周', value: 'weekly' },
  { label: '每月', value: 'monthly' },
  { label: '每年', value: 'yearly' },
]

// Recurrence end type options
const endTypeOptions = [
  { label: '永不', value: 'never' },
  { label: '重复次数', value: 'count' },
  { label: '结束日期', value: 'until' },
]

function EventModal() {
  const { t } = useTranslation()
  const [form] = Form.useForm()
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

  // Reset form when modal opens/closes or selectedEvent changes
  useEffect(() => {
    if (isEventModalOpen) {
      if (selectedEvent && editingEventId) {
        // Editing existing event
        const recurrence = selectedEvent.recurrence
        form.setFieldsValue({
          title: selectedEvent.title,
          description: selectedEvent.description,
          start: selectedEvent.start ? dayjs(selectedEvent.start) : null,
          end: selectedEvent.end ? dayjs(selectedEvent.end) : null,
          priority: selectedEvent.priority,
          isAllDay: selectedEvent.isAllDay,
          reminders: selectedEvent.reminders.map((r) => r.time),
          recurrence: recurrence ? recurrence.frequency : 'none',
          recurrenceInterval: recurrence?.interval || 1,
          recurrenceEndType: recurrence?.endType || 'never',
          recurrenceEndCount: recurrence?.endCount,
          recurrenceEndDate: recurrence?.endDate ? dayjs(recurrence.endDate) : null,
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
          reminders: [],
          recurrence: 'none',
          recurrenceInterval: 1,
          recurrenceEndType: 'never',
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

      // Build recurrence rule if needed
      let recurrence: RecurrenceRule | undefined
      if (values.recurrence && values.recurrence !== 'none') {
        recurrence = {
          frequency: values.recurrence as RecurrenceRule['frequency'],
          interval: values.recurrenceInterval || 1,
          endType: values.recurrenceEndType,
        }

        if (values.recurrenceEndType === 'count') {
          recurrence.endCount = values.recurrenceEndCount
        } else if (values.recurrenceEndType === 'until' && values.recurrenceEndDate) {
          recurrence.endDate = values.recurrenceEndDate.toISOString()
        }
      }

      const eventData = {
        title: values.title,
        description: values.description || '',
        start: values.start.toISOString(),
        end: values.end.toISOString(),
        priority: values.priority as Priority,
        isAllDay: values.isAllDay || false,
        reminders: (values.reminders || []).map((time: number): Reminder => ({
          id: uuidv4(),
          time,
          notified: false,
        })),
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

  // Custom reminder option to show "No reminder"
  const reminderSelectOptions = [
    { label: t('calendar.noReminder'), value: -1 },
    ...reminderOptions.map((opt) => ({
      label: opt.label,
      value: opt.value,
    })),
  ]

  // Filter out -1 from selected values
  const handleReminderChange = (values: number[]) => {
    const filtered = values.filter((v) => v !== -1)
    form.setFieldValue('reminders', filtered)
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

        <Form.Item
          name="reminders"
          label={t('calendar.reminder')}
        >
          <Select
            mode="multiple"
            placeholder={t('calendar.noReminder')}
            options={reminderSelectOptions}
            onChange={handleReminderChange}
            allowClear
          />
        </Form.Item>

        <Form.Item
          name="description"
          label={t('calendar.description')}
        >
          <TextArea
            rows={3}
            placeholder={t('calendar.descriptionPlaceholder')}
          />
        </Form.Item>

        {/* Recurrence Section */}
        <Form.Item
          name="recurrence"
          label="重复"
        >
          <Select
            options={recurrenceOptions}
            onChange={(value) => {
              // Show/hide recurrence options based on selection
              form.setFieldsValue({ recurrence: value })
            }}
          />
        </Form.Item>

        <Form.Item noStyle shouldUpdate={(prev, curr) => prev.recurrence !== curr.recurrence}>
          {() => {
            const recurrence = form.getFieldValue('recurrence')
            if (recurrence && recurrence !== 'none') {
              return (
                <>
                  <Form.Item
                    name="recurrenceInterval"
                    label="重复间隔"
                    style={{ marginBottom: 12 }}
                  >
                    <Select
                      style={{ width: 100 }}
                      options={[
                        { label: '1', value: 1 },
                        { label: '2', value: 2 },
                        { label: '3', value: 3 },
                        { label: '4', value: 4 },
                        { label: '5', value: 5 },
                      ]}
                    />
                  </Form.Item>

                  <Form.Item
                    name="recurrenceEndType"
                    label="结束重复"
                    style={{ marginBottom: 12 }}
                  >
                    <Select
                      options={endTypeOptions}
                    />
                  </Form.Item>

                  <Form.Item noStyle shouldUpdate={(prev, curr) => prev.recurrenceEndType !== curr.recurrenceEndType}>
                    {() => {
                      const endType = form.getFieldValue('recurrenceEndType')
                      if (endType === 'count') {
                        return (
                          <Form.Item
                            name="recurrenceEndCount"
                            label="重复次数"
                            style={{ marginBottom: 12 }}
                          >
                            <Input type="number" min={1} style={{ width: 100 }} />
                          </Form.Item>
                        )
                      } else if (endType === 'until') {
                        return (
                          <Form.Item
                            name="recurrenceEndDate"
                            label="结束日期"
                            style={{ marginBottom: 12 }}
                          >
                            <DatePicker />
                          </Form.Item>
                        )
                      }
                      return null
                    }}
                  </Form.Item>
                </>
              )
            }
            return null
          }}
        </Form.Item>
      </Form>
    </Modal>
  )
}

export default EventModal
