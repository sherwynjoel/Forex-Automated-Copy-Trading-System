import { useEffect, useRef } from 'react'
import { eventsSocket } from '../lib/api'

// Event categories that mean positions/orders/copies may have changed.
const REFRESH_CATEGORIES = new Set(['master_event', 'slave_action', 'drift', 'control'])

/**
 * Refetch page state the moment a relevant event streams in over the
 * events WebSocket, instead of waiting for the next 5s poll.
 *
 * Refetches twice per burst: once right away (mappings/logs are written
 * before the event is emitted, so copies show immediately) and once after
 * the copier's own debounced resync (~0.5s + broker round trip) has
 * refreshed its position list. Bursts of events collapse into one such
 * pair. The regular polling stays as the fallback when the socket is down.
 */
export function useLiveRefresh(refetch: () => void) {
  const refetchRef = useRef(refetch)
  refetchRef.current = refetch

  useEffect(() => {
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let burstTimers: ReturnType<typeof setTimeout>[] = []
    let unmounted = false

    const scheduleRefetch = () => {
      if (burstTimers.length > 0) return // burst already scheduled
      burstTimers = [
        setTimeout(() => refetchRef.current(), 250),
        setTimeout(() => {
          burstTimers = []
          refetchRef.current()
        }, 2000),
      ]
    }

    const connect = () => {
      try {
        ws = eventsSocket()
      } catch {
        // No WebSocket support (or construction failed): the 5s poll
        // still keeps the page current, just without instant refresh.
        return
      }
      ws.onmessage = (event) => {
        try {
          const evt = JSON.parse(event.data) as { category?: string }
          if (evt.category && REFRESH_CATEGORIES.has(evt.category)) {
            scheduleRefetch()
          }
        } catch {
          // Not JSON we understand; the poll fallback still covers us.
        }
      }
      ws.onclose = () => {
        ws = null
        if (!unmounted) {
          reconnectTimer = setTimeout(connect, 3000)
        }
      }
      ws.onerror = () => {
        ws?.close()
      }
    }

    connect()

    return () => {
      unmounted = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      burstTimers.forEach(clearTimeout)
      ws?.close()
    }
  }, [])
}
