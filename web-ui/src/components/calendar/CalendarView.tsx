import { useCallback, useEffect, useMemo, useRef } from 'react'
import FullCalendar from '@fullcalendar/react'
import dayGridPlugin from '@fullcalendar/daygrid'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import { DateSelectArg, EventClickArg, EventInput, EventDropArg } from '@fullcalendar/core'
import zhCnLocale from '@fullcalendar/core/locales/zh-cn'
import { useCalendarStore } from '../../store/calendarStore'
import { priorityColors } from '../../types/calendar'

function CalendarView() {
  const calendarRef = useRef<FullCalendar>(null)

  const {
    events,
    currentView,
    currentDate,
    setCurrentDate,
    setCurrentView,
    setSelectedEvent,
    setEditingEventId,
    setEventModalOpen,
    updateEvent,
  } = useCalendarStore()

  // 当外部切换视图时（如 CalendarHeader 的下拉框），同步到 FullCalendar
  useEffect(() => {
    const api = calendarRef.current?.getApi()
    if (api && api.view.type !== currentView) {
      api.changeView(currentView)
    }
  }, [currentView])

  // 当外部导航日期时（如 CalendarHeader 的前/后/今天按钮），同步到 FullCalendar
  useEffect(() => {
    const api = calendarRef.current?.getApi()
    if (api) {
      api.gotoDate(new Date(currentDate))
    }
  }, [currentDate])

  // Convert events to FullCalendar format
  const calendarEvents: EventInput[] = useMemo(() => {
    return events.map((event) => {
      const color = priorityColors[event.priority].css
      return {
        id: event.id,
        title: event.title,
        start: event.start,
        end: event.end,
        allDay: event.isAllDay,
        backgroundColor: color,
        borderColor: color,
        textColor: '#fff',
        extendedProps: {
          description: event.description,
          priority: event.priority,
          reminders: event.reminders,
        },
      }
    })
  }, [events])

  // Handle date click (quick add event)
  const handleDateSelect = useCallback((selectInfo: DateSelectArg) => {
    const { start, end } = selectInfo
    setSelectedEvent({
      id: '',
      title: '',
      start: start.toISOString(),
      end: end.toISOString(),
      priority: 'medium',
      reminders: [],
      isAllDay: selectInfo.allDay,
      createdAt: '',
      updatedAt: '',
    })
    setEditingEventId(null)
    setEventModalOpen(true)

    // Clear selection
    const calendarApi = selectInfo.view.calendar
    calendarApi.unselect()
  }, [setSelectedEvent, setEditingEventId, setEventModalOpen])

  // Handle event click (edit event)
  const handleEventClick = useCallback((clickInfo: EventClickArg) => {
    const event = clickInfo.event
    const existingEvent = events.find((e) => e.id === event.id)

    if (existingEvent) {
      setSelectedEvent(existingEvent)
      setEditingEventId(event.id)
      setEventModalOpen(true)
    }
  }, [events, setSelectedEvent, setEditingEventId, setEventModalOpen])

  // Handle event drag and drop
  const handleEventDrop = useCallback((dropInfo: EventDropArg) => {
    const { event } = dropInfo
    updateEvent(event.id, {
      start: event.startStr,
      end: event.endStr || event.startStr,
      isAllDay: event.allDay,
    })
  }, [updateEvent])

  // Handle dates set (update current date in store)
  const handleDatesSet = useCallback((dateInfo: { view: { type: string }, start: Date }) => {
    setCurrentDate(dateInfo.start.toISOString())

    // Map FullCalendar view types to our view types
    const viewMap: Record<string, typeof currentView> = {
      dayGridMonth: 'dayGridMonth',
      timeGridWeek: 'timeGridWeek',
      timeGridDay: 'timeGridDay',
    }
    const newView = viewMap[dateInfo.view.type]
    if (newView && newView !== currentView) {
      setCurrentView(newView)
    }
  }, [setCurrentDate, setCurrentView, currentView])

  return (
    <div className="calendar-view">
      <FullCalendar
        ref={calendarRef}
        plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
        initialView={currentView}
        locale={zhCnLocale}
        headerToolbar={false}
        events={calendarEvents}
        editable={true}
        droppable={true}
        selectable={true}
        selectMirror={true}
        dayMaxEvents={3}
        weekends={true}
        select={handleDateSelect}
        eventClick={handleEventClick}
        eventDrop={handleEventDrop}
        datesSet={handleDatesSet}
        height="auto"
        nowIndicator={true}
        eventDisplay="block"
        eventTimeFormat={{
          hour: '2-digit',
          minute: '2-digit',
          meridiem: false,
          hour12: false,
        }}
      />
    </div>
  )
}

export default CalendarView
