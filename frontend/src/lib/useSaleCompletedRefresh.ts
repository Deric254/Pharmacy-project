import { useEffect, useState } from 'react'
import { apiRequest, getAccessToken } from '../api/client'

const TOKEN_EXPIRED_CLOSE_CODE = 4001

export function useSaleCompletedRefresh(enabled: boolean): number {
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!enabled) return

    const origin = window.location.origin === 'null' || window.location.origin.startsWith('file:')
      ? 'http://127.0.0.1:8000'
      : window.location.origin
    const websocketOrigin = origin.replace(/^http/, 'ws')
    let socket: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | undefined
    let stopped = false
    let retryDelay = 1000

    function connect() {
      const token = getAccessToken()
      if (stopped || !token) return
      socket = new WebSocket(
        `${websocketOrigin}/api/v1/ws/notifications?token=${encodeURIComponent(token)}`,
      )

      function handleMessage(event: MessageEvent<string>) {
        try {
          const message = JSON.parse(event.data) as { event_type?: string }
          if (message.event_type === 'sale.completed') setVersion((current) => current + 1)
        } catch {}
      }

      socket.addEventListener('open', () => {
        retryDelay = 1000
      })
      socket.addEventListener('message', handleMessage)
      socket.addEventListener('close', (event) => {
        socket?.removeEventListener('message', handleMessage)
        if (!stopped) {
          const tokenExpired = event.code === TOKEN_EXPIRED_CLOSE_CODE
          retryTimer = setTimeout(async () => {
            if (tokenExpired) await apiRequest('/auth/me').catch(() => undefined)
            connect()
          }, retryDelay)
          retryDelay = Math.min(retryDelay * 2, 30000)
        }
      })
    }

    connect()
    return () => {
      stopped = true
      if (retryTimer) clearTimeout(retryTimer)
      socket?.close()
    }
  }, [enabled])

  return version
}