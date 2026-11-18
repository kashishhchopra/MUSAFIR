import { useEffect, useRef, useState } from 'react'
import useGeolocation from './useGeolocation'
import useWebSocket from '../useWebSocket'
import { SIMULATE_GPS } from '../config'
import { postEmergencyLocation } from '../lib/emergencyLocationService'

const POST_INTERVAL_MS = 7000 // spec: every 5-10 seconds
const LIVE_S = 15
const STALE_S = 45

// SOS live-location sharing, tourist side: once an incident is active, this
// posts the device's real GPS position (navigator.geolocation) to the
// backend every ~7s until the tourist stops sharing or police resolve the
// incident. On a device/browser with no real GPS (matching this project's
// existing SIMULATE_GPS convention for ordinary tracking), it falls back to
// a small simulated walk instead -- clearly marked `demo: true` on every
// ping this sends, never presented as a real fix.
export default function useEmergencyTracking({ tid, incidentId, active, startPos, resetKey }) {
  const [status, setStatus] = useState('connecting') // connecting | live | stale | offline | stopped
  const [lastSentAt, setLastSentAt] = useState(null)
  const [demo, setDemo] = useState(SIMULATE_GPS)
  const [error, setError] = useState('')
  const simPosRef = useRef(startPos ? { ...startPos } : null)
  // A new incident -- or a new SOS press on the same open incident -- starts
  // fresh: a "stopped" left over from before must not block tracking now.
  // (Adjusting state during render is React's documented way to reset state
  // when a prop changes.)
  const key = resetKey ?? incidentId
  const [statusKey, setStatusKey] = useState(key)
  if (key !== statusKey) {
    setStatusKey(key)
    setStatus('connecting')
    setLastSentAt(null)
    setError('')
  }
  // Once stopped (the control room resolved the case or ended sharing, or the server says 409),
  // nothing more is sent and GPS is released -- even if the card stays mounted.
  const stopped = status === 'stopped'
  const stoppedRef = useRef(false)
  stoppedRef.current = stopped
  // Tighter than ordinary tracking: during an SOS a 5 m move matters, and
  // re-emitting at most every POST_INTERVAL_MS keeps posts on the ~7 s rhythm
  // (each new position triggers an immediate send below).
  const geo = useGeolocation({
    enabled: active && !stopped && !SIMULATE_GPS, minDistanceM: 5, minIntervalMs: POST_INTERVAL_MS,
  })

  // The tourist's own channel tells us the moment police/operator resolve
  // the incident (or the tourist stops sharing from another tab/device) --
  // so this stops posting immediately rather than on the next poll.
  useWebSocket((msg) => {
    if (msg.event === 'emergency_tracking_stopped' && msg.incident_id === incidentId) {
      setStatus('stopped')
    }
  }, tid ? `/ws/tourist/${tid}` : null)

  useEffect(() => {
    if (!active || !incidentId || stoppedRef.current) return undefined
    setStatus('connecting')

    const send = async () => {
      if (stoppedRef.current) return
      let lat, lng, accuracyM, speedKmh, headingDeg, isDemo
      if (!SIMULATE_GPS && geo.position) {
        ({ lat, lng, accuracy: accuracyM, speedKmh, heading: headingDeg } = geo.position)
        isDemo = false
      } else if (!SIMULATE_GPS) {
        // Real device but no fix yet (or GPS failing): wait. Never fall through to
        // the simulated walk -- a made-up position would overwrite the incident's
        // real location in the control room. The SOS itself already carried the
        // best known position.
        if (geo.error) setError('Location temporarily unavailable')
        return
      } else {
        // Demo/simulation fallback: a small deterministic drift, clearly
        // flagged -- never a claim about the tourist's real position.
        const p = simPosRef.current || { lat: 26.1445, lng: 91.7362 }
        p.lat += (Math.random() - 0.5) * 0.0006
        p.lng += (Math.random() - 0.5) * 0.0006
        simPosRef.current = p
        lat = p.lat; lng = p.lng; accuracyM = 12; speedKmh = 4; headingDeg = Math.round(Math.random() * 359)
        isDemo = true
      }
      setDemo(isDemo)
      try {
        await postEmergencyLocation(incidentId, { lat, lng, accuracyM, speedKmh, headingDeg, demo: isDemo })
        setLastSentAt(Date.now())
        setStatus('live')
        setError('')
      } catch (err) {
        if (err.response?.status === 409) {
          setStatus('stopped') // incident resolved / sharing already stopped server-side
        } else {
          setError('Connection interrupted')
        }
      }
    }

    send()
    const iv = setInterval(() => {
      if (stoppedRef.current) clearInterval(iv)
      else send()
    }, POST_INTERVAL_MS)
    return () => clearInterval(iv)
  }, [active, incidentId, key, geo.position, geo.error])

  // Degrade LIVE -> STALE -> OFFLINE purely from recency, same thresholds
  // the police dashboard uses -- never claim LIVE once updates have
  // actually stopped arriving.
  useEffect(() => {
    if (!active || status === 'stopped') return undefined
    const tick = () => {
      if (!lastSentAt) return
      const ageS = (Date.now() - lastSentAt) / 1000
      setStatus(ageS <= LIVE_S ? 'live' : ageS <= STALE_S ? 'stale' : 'offline')
    }
    const iv = setInterval(tick, 3000)
    return () => clearInterval(iv)
  }, [active, status, lastSentAt])

  return {
    status, lastSentAt, demo, error,
    // The case was closed (by the tourist): stop sharing and show "resolved" at once,
    // without waiting for the server's websocket event.
    markStopped: () => setStatus('stopped'),
    secondsSinceUpdate: lastSentAt ? Math.round((Date.now() - lastSentAt) / 1000) : null,
  }
}
