import { useEffect } from 'react'
import { Row, Col, Button } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { useCalendarStore } from '../store/calendarStore'
import CalendarView from '../components/calendar/CalendarView'
import CalendarHeader from '../components/calendar/CalendarHeader'
import TodayEvents from '../components/calendar/TodayEvents'
import EventModal from '../components/calendar/EventModal'
import { useNotifications } from '../hooks/useNotifications'
import '../styles/calendar.css'
import './CalendarPage.css'

function CalendarPage() {
  const { t } = useTranslation()
  const {
    setEventModalOpen,
    loadEvents,
    loadSettings,
  } = useCalendarStore()

  // Load calendar data on mount
  useEffect(() => {
    loadSettings()
    loadEvents()
  }, [loadEvents, loadSettings])

  // Initialize notifications
  useNotifications()

  // Handle keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // N - New event
      if (e.key === 'n' && !e.ctrlKey && !e.metaKey) {
        const target = e.target as HTMLElement
        if (target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
          e.preventDefault()
          setEventModalOpen(true)
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [setEventModalOpen])

  const handleAddEvent = () => {
    setEventModalOpen(true)
  }

  return (
    <div className="calendar-page">
      <div className="calendar-container">
        <CalendarHeader />

        <Row className="calendar-content">
          <Col xs={24} lg={18} className="calendar-main">
            <CalendarView />
          </Col>
          <Col xs={24} lg={6} className="calendar-sidebar">
            <div className="today-events-container">
              <div className="today-events-header">
                <h3>{t('calendar.todayEvents')}</h3>
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  size="small"
                  onClick={handleAddEvent}
                >
                  {t('calendar.addEvent')}
                </Button>
              </div>
              <TodayEvents />
            </div>
          </Col>
        </Row>
      </div>

      <EventModal />
    </div>
  )
}

export default CalendarPage
