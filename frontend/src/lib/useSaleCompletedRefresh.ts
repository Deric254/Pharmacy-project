import { useEffect, useState } from 'react'
import { getAccessToken } from '../api/client'

export function useSaleCompletedRefresh(enabled: boolean): number {
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (!enabled) return

    const token = getAccessToken()
    if (!token) return

    const origin = window.location.origin === 'null' || window.location.origin.startsWith('file:')
      ? 'http://127.0.0.1:8000'
      : window.location.origin
    const websocketOrigin = origin.replace(/^http/, 'ws')
    const socket = new WebSocket(
      `${websocketOrigin}/api/v1/ws/notifications?token=${encodeURIComponent(token)}`,
    )

    function handleMessage(event: MessageEvent<string>) {
      try {
        const message = JSON.parse(event.data) as { event_type?: string }
        if (message.event_type === 'sale.completed') setVersion((current) => current + 1)
      } catch {
        // Ignore malformed notifications; report requests remain usable.
      }
    }

    socket.addEventListener('message', handleMessage)
    return () => {
      socket.removeEventListener('message', handleMessage)
      socket.close()
    }
  }, [enabled])

  return version
}