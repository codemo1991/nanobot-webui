import i18n from '../i18n'

/**
 * Format uptime in seconds to human-readable string.
 * Uses i18n for localized output.
 */
export function formatUptime(seconds: number): string {
  const t = i18n.t.bind(i18n)

  if (seconds < 60) {
    return t('formatUptime.seconds', { count: seconds })
  }

  const minutes = Math.floor(seconds / 60)

  if (minutes < 60) {
    const secs = seconds % 60
    return t('formatUptime.minutesSeconds', { minutes, seconds: secs })
  }

  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60

  if (hours < 24) {
    return t('formatUptime.hoursMinutes', { hours, minutes: mins })
  }

  const days = Math.floor(hours / 24)
  const hrs = hours % 24
  return t('formatUptime.daysHoursMinutes', { days, hours: hrs, minutes: mins })
}
