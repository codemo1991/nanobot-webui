import { Empty } from 'antd'
import { useTranslation } from 'react-i18next'
import { useCalendarStore } from '../../store/calendarStore'
import { priorityColors } from '../../types'
import { format, parseISO, isToday } from 'date-fns'
import type { CalendarEvent } from '../../types'

interface TodayEventItemProps {
  event: CalendarEvent
  onClick: (event: CalendarEvent) => void
}

function TodayEventItem({ event, onClick }: TodayEventItemProps) {
  const priorityColor = priorityColors[event.priority]

  const formatTime = (dateStr: string) => {
    return format(parseISO(dateStr), 'HH:mm')
  }

  const getTimeDisplay = () => {
    if (event.isAllDay) {
      return '全天'
    }
    return `${formatTime(event.start)} - ${formatTime(event.end)}`
  }

  return (
    <div
      className="today-event-item"
      onClick={() => onClick(event)}
      style={{
        borderLeftColor: priorityColor.dot,
        backgroundColor: priorityColor.bg,
      }}
    >
      <div className="today-event-priority" style={{ backgroundColor: priorityColor.dot }} />
      <div className="today-event-content">
        <div className="today-event-title">{event.title}</div>
        <div className="today-event-time">{getTimeDisplay()}</div>
        {event.description && (
          <div className="today-event-description">{event.description}</div>
        )}
      </div>
    </div>
  )
}

function TodayEvents() {
  const { t } = useTranslation()
  const { events, setSelectedEvent, setEditingEventId, setEventModalOpen } = useCalendarStore()

  // 确保 events 是数组
  const safeEvents = Array.isArray(events) ? events : []

  // 筛选今日事件：开始时间是今天，或跨天事件的时间范围包含今天
  const todayEvents = safeEvents.filter((event) => {
    const now = new Date()
    const start = parseISO(event.start)
    const end = parseISO(event.end)
    return isToday(start) || (start <= now && end >= now)
  })

  // Sort by start time
  const sortedEvents = [...todayEvents].sort((a, b) => {
    return new Date(a.start).getTime() - new Date(b.start).getTime()
  })

  const handleEventClick = (event: CalendarEvent) => {
    setSelectedEvent(event)
    setEditingEventId(event.id)
    setEventModalOpen(true)
  }

  if (sortedEvents.length === 0) {
    return (
      <div className="today-events-empty">
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('calendar.noEventsToday')}
        />
      </div>
    )
  }

  return (
    <div className="today-events-list">
      {sortedEvents.map((event) => (
        <TodayEventItem
          key={event.id}
          event={event}
          onClick={handleEventClick}
        />
      ))}
    </div>
  )
}

export default TodayEvents
