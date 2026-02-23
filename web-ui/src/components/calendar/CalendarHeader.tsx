import { Button, Space, Select, Typography } from 'antd'
import { LeftOutlined, RightOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { useCalendarStore } from '../../store/calendarStore'
import { format, parseISO } from 'date-fns'

const { Text } = Typography

function CalendarHeader() {
  const { t } = useTranslation()
  const { currentView, currentDate, setCurrentDate, setCurrentView } = useCalendarStore()

  const parsedDate = parseISO(currentDate)

  // Format current date display
  const getDateDisplay = () => {
    if (currentView === 'dayGridMonth') {
      return format(parsedDate, 'yyyy年M月')
    } else if (currentView === 'timeGridWeek') {
      return format(parsedDate, 'yyyy年M月')
    } else {
      return format(parsedDate, 'yyyy年M月d日')
    }
  }

  // Navigate to previous period
  const handlePrev = () => {
    const newDate = new Date(parsedDate)
    if (currentView === 'dayGridMonth') {
      newDate.setMonth(newDate.getMonth() - 1)
    } else if (currentView === 'timeGridWeek') {
      newDate.setDate(newDate.getDate() - 7)
    } else {
      newDate.setDate(newDate.getDate() - 1)
    }
    setCurrentDate(newDate.toISOString())
  }

  // Navigate to next period
  const handleNext = () => {
    const newDate = new Date(parsedDate)
    if (currentView === 'dayGridMonth') {
      newDate.setMonth(newDate.getMonth() + 1)
    } else if (currentView === 'timeGridWeek') {
      newDate.setDate(newDate.getDate() + 7)
    } else {
      newDate.setDate(newDate.getDate() + 1)
    }
    setCurrentDate(newDate.toISOString())
  }

  // Navigate to today
  const handleToday = () => {
    setCurrentDate(new Date().toISOString())
  }

  // Handle view change
  const handleViewChange = (value: string) => {
    setCurrentView(value as typeof currentView)
  }

  return (
    <div className="calendar-header">
      <Space className="calendar-header-left">
        <Text strong className="calendar-date-display">
          {getDateDisplay()}
        </Text>
      </Space>

      <Space className="calendar-header-center">
        <Button icon={<LeftOutlined />} onClick={handlePrev} />
        <Button onClick={handleToday}>
          {t('calendar.today')}
        </Button>
        <Button icon={<RightOutlined />} onClick={handleNext} />
      </Space>

      <Space className="calendar-header-right">
        <Select
          value={currentView}
          onChange={handleViewChange}
          style={{ width: 120 }}
          options={[
            { value: 'dayGridMonth', label: t('calendar.month') },
            { value: 'timeGridWeek', label: t('calendar.week') },
            { value: 'timeGridDay', label: t('calendar.day') },
          ]}
        />
      </Space>
    </div>
  )
}

export default CalendarHeader
