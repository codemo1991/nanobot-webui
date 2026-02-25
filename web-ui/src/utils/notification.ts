/**
 * 浏览器通知工具
 * 用于在后台任务完成时提醒用户
 */

// 请求通知权限
export async function requestNotificationPermission(): Promise<boolean> {
  if (!('Notification' in window)) {
    console.warn('Browser does not support notifications')
    return false
  }

  if (Notification.permission === 'granted') {
    return true
  }

  if (Notification.permission === 'denied') {
    return false
  }

  const permission = await Notification.requestPermission()
  return permission === 'granted'
}

// 检查是否有通知权限
export function hasNotificationPermission(): boolean {
  return 'Notification' in window && Notification.permission === 'granted'
}

// 发送任务完成通知
export function notifyTaskComplete(taskId: string, result?: string) {
  if (!hasNotificationPermission()) {
    return
  }

  const title = 'Claude Code 任务完成'
  const body = result
    ? result.slice(0, 100) + (result.length > 100 ? '...' : '')
    : '任务已完成，点击查看详情'

  const notification = new Notification(title, {
    body,
    icon: '/favicon.ico',
    tag: `task-${taskId}`,
    requireInteraction: false,
    silent: false,
  })

  notification.onclick = () => {
    window.focus()
    notification.close()
  }

  // 5秒后自动关闭
  setTimeout(() => notification.close(), 5000)
}

// 发送通用通知
export function sendNotification(title: string, options?: NotificationOptions) {
  if (!hasNotificationPermission()) {
    return null
  }

  const notification = new Notification(title, {
    icon: '/favicon.ico',
    ...options,
  })

  notification.onclick = () => {
    window.focus()
    notification.close()
  }

  setTimeout(() => notification.close(), 5000)
  return notification
}
