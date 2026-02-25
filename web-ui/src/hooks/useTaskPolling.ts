import { useEffect, useRef, useState, useCallback } from 'react'
import { api } from '../api'

export interface TaskStatus {
  taskId: string
  status: 'pending' | 'running' | 'done' | 'error' | 'timeout' | 'cancelled'
  prompt: string
  startTime: string | null
  endTime: string | null
  result: string | null
}

interface UseTaskPollingOptions {
  taskId: string | null
  enabled?: boolean
  interval?: number  // 默认 3000ms
  onComplete?: (result: TaskStatus) => void
  onError?: (error: Error) => void
}

export function useTaskPolling({
  taskId,
  enabled = true,
  interval = 3000,
  onComplete,
  onError,
}: UseTaskPollingOptions) {
  const [status, setStatus] = useState<TaskStatus | null>(null)
  const [isPolling, setIsPolling] = useState(false)
  const abortRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const stopPolling = useCallback(() => {
    abortRef.current = true
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    setIsPolling(false)
  }, [])

  const startPolling = useCallback(() => {
    abortRef.current = false
    setIsPolling(true)
  }, [])

  useEffect(() => {
    if (!taskId || !enabled) {
      stopPolling()
      setStatus(null)
      return
    }

    abortRef.current = false
    setIsPolling(true)

    const poll = async () => {
      if (abortRef.current) return

      try {
        const result = await api.getTaskStatus(taskId)
        setStatus(result)

        // 任务完成或失败，停止轮询
        if (result.status === 'done' || result.status === 'error' || result.status === 'timeout' || result.status === 'cancelled') {
          stopPolling()
          onComplete?.(result)
          return
        }

        // 继续轮询
        if (!abortRef.current) {
          timerRef.current = setTimeout(poll, interval)
        }
      } catch (err) {
        if (!abortRef.current) {
          onError?.(err as Error)
          // 错误时继续轮询，但增加间隔
          timerRef.current = setTimeout(poll, interval * 2)
        }
      }
    }

    // 立即执行一次
    poll()

    return () => {
      stopPolling()
    }
  }, [taskId, enabled, interval, onComplete, onError, stopPolling])

  return {
    status,
    isPolling,
    stopPolling,
    startPolling,
  }
}
