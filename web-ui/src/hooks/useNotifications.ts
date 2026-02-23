import { useEffect, useRef, useCallback } from 'react'
import { useCalendarStore } from '../store/calendarStore'
import type { CalendarEvent } from '../types/calendar'

// Check if browser supports notifications
const isNotificationSupported = typeof window !== 'undefined' && 'Notification' in window

// Request notification permission
export const requestNotificationPermission = async (): Promise<NotificationPermission> => {
  if (!isNotificationSupported) {
    return 'denied'
  }

  if (Notification.permission === 'granted') {
    return 'granted'
  }

  if (Notification.permission !== 'denied') {
    const permission = await Notification.requestPermission()
    return permission
  }

  return Notification.permission
}

// Show browser notification
const showNotification = (title: string, options?: NotificationOptions): Notification | null => {
  if (!isNotificationSupported || Notification.permission !== 'granted') {
    return null
  }

  return new Notification(title, {
    icon: '/favicon.ico',
    tag: 'nanobot-calendar',
    ...options,
  })
}

// Get reminder message
const getReminderMessage = (event: CalendarEvent, minutesBefore: number): string => {
  if (minutesBefore === 0) {
    return `事件 "${event.title}" 即将开始`
  }
  return `事件 "${event.title}" 将在 ${minutesBefore} 分钟后开始`
}

export function useNotifications() {
  const { events, settings, markReminderNotified } = useCalendarStore()
  const intervalRef = useRef<number | null>(null)

  // Check for due reminders
  const checkReminders = useCallback(() => {
    if (!settings.notificationEnabled) return

    const now = new Date()

    events.forEach((event) => {
      const eventStart = new Date(event.start)

      event.reminders.forEach((reminder) => {
        if (reminder.notified) return

        // Calculate when to notify
        const notifyTime = new Date(eventStart.getTime() - reminder.time * 60 * 1000)

        // Check if it's time to notify (within 1 minute window)
        if (now >= notifyTime && now < new Date(notifyTime.getTime() + 60000)) {
          // Show notification
          const message = getReminderMessage(event, reminder.time)
          showNotification('日历提醒', {
            body: message,
            requireInteraction: true,
          })

          // Play sound if enabled
          if (settings.soundEnabled) {
            playNotificationSound()
          }

          // Mark as notified
          markReminderNotified(event.id, reminder.id)
        }
      })
    })
  }, [events, settings.notificationEnabled, settings.soundEnabled, markReminderNotified])

  // Start checking reminders
  useEffect(() => {
    // Request permission on mount
    requestNotificationPermission()

    // Check immediately
    checkReminders()

    // Check every 30 seconds
    intervalRef.current = window.setInterval(checkReminders, 30000)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
      }
    }
  }, [checkReminders])

  // Expose methods
  return {
    requestPermission: requestNotificationPermission,
    isSupported: isNotificationSupported,
  }
}

// Play notification sound
const playNotificationSound = () => {
  try {
    const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)()
    const oscillator = audioContext.createOscillator()
    const gainNode = audioContext.createGain()

    oscillator.connect(gainNode)
    gainNode.connect(audioContext.destination)

    oscillator.frequency.value = 800
    oscillator.type = 'sine'

    gainNode.gain.setValueAtTime(0.3, audioContext.currentTime)
    gainNode.gain.exponentialRampToValueAtTime(0.01, audioContext.currentTime + 0.5)

    oscillator.start(audioContext.currentTime)
    oscillator.stop(audioContext.currentTime + 0.5)
  } catch (e) {
    console.warn('Could not play notification sound:', e)
  }
}

export default useNotifications
