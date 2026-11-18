import { useEffect, useRef, useState } from 'react'
import api from '../../api'
import { pointInPoly, haversineKm } from '../../components/geo'
import { HEARTBEAT_MS, TRACK_INTERVAL_MS, SIMULATE_GPS } from '../../config'
import useGeolocation from '../../hooks/useGeolocation'
import useOnlineStatus from '../../hooks/useOnlineStatus'
import useWebSocket from '../../useWebSocket'
import { enqueueSOS, flushQueue, queueLength } from '../../lib/offlineQueue'
import { useRoutePicker } from '../../components/RoutePicker.jsx'

// All the tourist app's live data + mutations, extracted out of the screen
// component so it's testable without mounting a Leaflet map (there was no
// TouristApp.test.jsx before this -- the whole load/track/SOS pipeline was
// unreachable from a test without a real DOM map). TouristShell and every
// tab consume this via one hook call.
const SOS_MESSAGE_MAX = 500
const SOS_GPS_WAIT_MS = 2500 // longest an SOS waits for a fresh GPS read
const SOS_RETRY_MS = 20000 // how often a saved SOS is retried while the app is open

export default function useTouristData(tid) {
  const online = useOnlineStatus()
  const [me, setMe] = useState(null)
  const [score, setScore] = useState(null)
  const [zones, setZones] = useState([])
  const [units, setUnits] = useState([])
  const [trajectory, setTrajectory] = useState([])
  const [riskForecast, setRiskForecast] = useState([])
  const [tracking, setTracking] = useState(true)
  const [toast, setToast] = useState(null)
  const [sosSent, setSosSent] = useState(null)
  const [sosQueued, setSosQueued] = useState(false)
  const [sosQueuedReason, setSosQueuedReason] = useState('offline') // offline | server
  const [sosFailed, setSosFailed] = useState(false)
  const [sosSending, setSosSending] = useState(false)
  const sosInFlight = useRef(null)
  const sosFailedTimer = useRef(null)
  useEffect(() => () => clearTimeout(sosFailedTimer.current), [])
  const [pendingCount, setPendingCount] = useState(queueLength())
  const [emergencyMessage, setEmergencyMessage] = useState('')
  // Shared across HomeTab (renders the map layer -- destination click +
  // candidate polylines) and PlanTab (renders the control panel): only one
  // tab is mounted at a time, so this state has to live above both, not in
  // either tab, or switching tabs would silently reset a route in progress.
  const [routePickerOpen, setRoutePickerOpen] = useState(false)
  const routePicker = useRoutePicker(tid)
  const posRef = useRef(null)
  // Gating lives in the hook: a 25 m move at most every TRACK_INTERVAL_MS,
  // plus a heartbeat while stationary. Every position it emits is sent.
  const geo = useGeolocation({
    enabled: tracking && !SIMULATE_GPS,
    minDistanceM: 25,
    minIntervalMs: TRACK_INTERVAL_MS,
    heartbeatMs: HEARTBEAT_MS,
  })

  const showToast = (msg, ms = 4000) => {
    setToast(msg)
    setTimeout(() => setToast(null), ms)
  }

  const load = async () => {
    const [m, s, z, u] = await Promise.all([
      api.get(`/tourists/${tid}`),
      api.get(`/tourists/${tid}/safety-score`),
      api.get('/zones'),
      api.get('/police-units'),
    ])
    setMe(m.data); setScore(s.data); setZones(z.data); setUnits(u.data)
    setTracking(m.data.tracking_enabled)
    posRef.current = [m.data.last_lat, m.data.last_lng]
  }
  useEffect(() => {
    if (tid) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per tid
  }, [tid])

  // Trajectory + dynamic risk forecast. Best-effort refresh alongside the
  // location poll -- neither failing should block the rest of the app.
  useEffect(() => {
    if (!tid) return
    const loadForecasts = () => {
      api.get(`/tourists/${tid}/trajectory-forecast`).then((r) => setTrajectory(r.data.points)).catch(() => {})
      api.get(`/tourists/${tid}/risk-forecast`).then((r) => setRiskForecast(r.data.forecast)).catch(() => {})
    }
    loadForecasts()
    const iv = setInterval(loadForecasts, TRACK_INTERVAL_MS)
    return () => clearInterval(iv)
  }, [tid])

  const pushLocation = async (lat, lng, speedKmh) => {
    posRef.current = [lat, lng]
    const { data } = await api.post(`/tourists/${tid}/location`, { lat, lng, speed_kmh: speedKmh })
    setScore((s) => ({ ...s, score: data.safety_score, band: data.band }))
    setMe((m) => ({ ...m, last_lat: lat, last_lng: lng, safety_score: data.safety_score }))
    if (data.alerts_raised?.length) {
      showToast(`⚠ ${data.alerts_raised.join(', ').replace(/_/g, ' ')}`)
    }
  }

  // Opt-in live tracking. Two sources, chosen by VITE_SIMULATE_GPS:
  //  - simulated: a random walk on a timer, for demos on a machine with no
  //    meaningful GPS (the default, matching the project's existing demo mode)
  //  - real: useGeolocation, pushed whenever it emits a position (it already
  //    applies the distance/interval gating and the stationary heartbeat)
  useEffect(() => {
    if (!tracking || !me || !SIMULATE_GPS) return
    const iv = setInterval(() => {
      const [lat, lng] = posRef.current
      const nlat = lat + (Math.random() - 0.5) * 0.002
      const nlng = lng + (Math.random() - 0.5) * 0.002
      pushLocation(nlat, nlng, 5)
    }, TRACK_INTERVAL_MS)
    return () => clearInterval(iv)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- deliberate: only re-arm on tracking/identity change
  }, [tracking, me?.id])

  useEffect(() => {
    if (!tracking || !me || SIMULATE_GPS || !geo.position) return
    pushLocation(geo.position.lat, geo.position.lng, geo.position.speedKmh)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geo.position, tracking, me?.id])

  const toggleTracking = async () => {
    const next = !tracking
    setTracking(next)
    await api.post(`/tourists/${tid}/tracking?enabled=${next}`)
  }

  const postSOS = (payload) => api.post(`/tourists/${tid}/sos`, payload)

  // Returns the real dispatch response ({queued: true} when it had to be
  // queued offline) so a caller that needs to report the outcome -- the
  // voice assistant reads it aloud -- can use the actual result rather than
  // waiting on state. Existing button callers ignore the return value.
  // The best REAL position for an SOS, never an invented one: the live GPS fix,
  // else the last position this device reported, else one quick GPS read. If
  // none exists the SOS goes out without coordinates and the server uses the
  // tourist's last known position (or marks the location unknown) -- refusing
  // or guessing is never acceptable for the one request that must not fail.
  const sosPosition = async () => {
    const known = posRef.current
    const knownOk = known && Number.isFinite(known[0]) && Number.isFinite(known[1])
    if (SIMULATE_GPS) return knownOk ? known : null // demo mode: the simulated track is the position
    if (geo.position) return [geo.position.lat, geo.position.lng]
    // No live fix (tracking off): take a fresh read before trusting the last position this
    // device reported, which can be hours old. Hard-capped -- an unanswered permission prompt
    // does not count toward getCurrentPosition's own timeout, and an SOS must never wait on it.
    let fresh = null
    if (typeof navigator !== 'undefined' && navigator.geolocation) {
      fresh = await Promise.race([
        new Promise((resolve) => {
          navigator.geolocation.getCurrentPosition(
            (r) => resolve([r.coords.latitude, r.coords.longitude]),
            () => resolve(null),
            { enableHighAccuracy: true, timeout: SOS_GPS_WAIT_MS, maximumAge: 30000 },
          )
        }),
        new Promise((resolve) => setTimeout(() => resolve(null), SOS_GPS_WAIT_MS)),
      ])
    }
    if (fresh) return fresh
    return knownOk ? known : null
  }

  // One SOS request at a time: a second tap while one is in flight shares it
  // instead of firing a duplicate.
  const sendSOS = () => {
    if (sosInFlight.current) return sosInFlight.current
    setSosSending(true)
    const run = sendSOSNow().finally(() => {
      sosInFlight.current = null
      setSosSending(false)
    })
    sosInFlight.current = run
    return run
  }

  const sendSOSNow = async () => {
    const pos = await sosPosition()
    // The server rejects notes over 500 characters (a typed note or a long
    // voice transcript would otherwise make the SOS itself fail).
    const message = (emergencyMessage.trim() || 'Emergency! Need help.').slice(0, SOS_MESSAGE_MAX)
    const payload = pos ? { lat: pos[0], lng: pos[1], message } : { message }
    let result = null
    try {
      const { data } = await postSOS(payload)
      setSosSent(data)
      setSosQueued(false)
      setSosFailed(false)
      result = data
    } catch (err) {
      const status = err.response?.status
      // No response (offline, DNS, refused), a gateway timeout / 5xx, or a rate
      // limit: the SOS may not have been processed, so keep it and retry. A
      // repeat is safe -- the server merges taps within 2 minutes into one
      // incident. Any other 4xx means the server refused this exact request, so
      // retrying would never help: tell the tourist, loudly.
      if (!err.response || status >= 500 || status === 429) {
        enqueueSOS(payload)
        setPendingCount(queueLength())
        setSosQueued(true)
        setSosQueuedReason(err.response ? 'server' : 'offline')
        setSosFailed(false)
        result = { queued: true, reason: err.response ? 'server' : 'offline' }
      } else {
        setSosFailed(true)
        setSosSent(null) // don't leave an earlier green "SOS sent" banner beside the failure
        clearTimeout(sosFailedTimer.current)
        sosFailedTimer.current = setTimeout(() => setSosFailed(false), 10000)
        return { failed: true, status }
      }
    }
    setEmergencyMessage('')
    load().catch(() => {})
    return result
  }

  // Flush any queued SOS the moment connectivity returns, and once on mount
  // in case one was queued in a previous session that never got a chance to
  // retry (the tab was closed, the app was killed, etc).
  useEffect(() => {
    if (!tid) return undefined
    let busy = false
    const tryFlush = async () => {
      if (busy) return
      busy = true
      let sent = 0
      try {
        sent = await flushQueue((payload) => postSOS(payload))
      } finally {
        busy = false
      }
      if (sent > 0) {
        setPendingCount(queueLength())
        showToast(`✅ ${sent} queued SOS alert${sent > 1 ? 's' : ''} sent`, 5000)
      }
    }
    tryFlush()
    window.addEventListener('online', tryFlush)
    // A saved SOS keeps retrying -- after a server error the device is still "online",
    // so the `online` event alone would never fire again.
    const retry = setInterval(() => { if (queueLength() > 0) tryFlush() }, SOS_RETRY_MS)
    return () => {
      window.removeEventListener('online', tryFlush)
      clearInterval(retry)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tid])

  // Live push of this tourist's own alerts (geofence/anomaly/health/fall),
  // server-side scoped to their own record -- a server-detected event (e.g.
  // from a linked IoT band) shows up immediately instead of waiting for the
  // next poll.
  useWebSocket((ev) => {
    if (ev.event === 'alert') {
      showToast(`⚠ ${ev.type?.replace(/_/g, ' ')}: ${ev.message}`, 5000)
    }
  }, tid ? `/ws/tourist/${tid}` : null)

  const inZones = me ? zones.filter((z) => pointInPoly(me.last_lat, me.last_lng, z.polygon)) : []
  const riskyZone = inZones.find((z) => ['high', 'restricted'].includes(z.risk_level))
  const nearby = me
    ? [...units]
        .map((u) => ({ ...u, dist: haversineKm(me.last_lat, me.last_lng, u.lat, u.lng) }))
        .sort((a, b) => a.dist - b.dist).slice(0, 3)
    : []

  return {
    ready: !!(me && score),
    online, me, score, zones, units, trajectory, riskForecast,
    tracking, toggleTracking, toast,
    sosSent, setSosSent, sosQueued, sosQueuedReason, sosFailed, sosSending, pendingCount, sendSOS,
    emergencyMessage, setEmergencyMessage,
    posRef, geo, load, pushLocation,
    inZones, riskyZone, nearby,
    routePicker, routePickerOpen, setRoutePickerOpen,
  }
}
