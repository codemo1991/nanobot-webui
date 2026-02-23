import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import { ChatErrorBoundary } from './components/ChatErrorBoundary'
import ChatPage from './pages/ChatPage'
import MirrorPage from './pages/MirrorPage'
import CalendarPage from './pages/CalendarPage'
import CronPage from './pages/CronPage'
import ConfigPage from './pages/ConfigPage'
import SystemPage from './pages/SystemPage'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatErrorBoundary><ChatPage /></ChatErrorBoundary>} />
          <Route path="mirror" element={<MirrorPage />} />
          <Route path="calendar" element={<CalendarPage />} />
          <Route path="cron" element={<CronPage />} />
          <Route path="config" element={<ConfigPage />} />
          <Route path="system" element={<SystemPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
