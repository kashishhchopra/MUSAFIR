import { useCallback, useEffect, useRef, useState } from 'react'
import {
  FALL_RETRY_MS, FALL_SPEED_MAX_AGE_MS, FALL_SPEED_WINDOW_MS,
} from '../config'
import { createFallDetector } from '../lib/fallDetector'
import {
  cancelFallEvent, confirmFallEvent, getActiveFallEvent, getFallConfig, isNetworkError,
  listFallEvents, newClientEventId, sosFromFallEvent, startFallEvent,
} from '../lib/fallDetectionService'
import '../lib/fallDetectionStrings'
import useWebSocket from '../useWebSocket'
import useMotionSensor from './useMotionSensor'

const PREF_KEY = 'stsFallMonitoring'
const readPref = () => { try { return localStorage.getItem(PREF_KEY) === '1' } catch { return false } }
const writePref = (on) => { try { localStorage.setItem(PREF_KEY, on ? '1' : '0') } catch { /* private mode */ } }

// phase: idle | countdown | cancelling | sending | cancelled | confirmed | sos | error
const LIVE = new Set(['countdown', 'cancelling', 'sending'])
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const hasFix = (p) => Array.isArray(p) && Number.isFinite(p[0]) && Number.isFinite(p[1])

// The whole fall & crash flow, mounted ONCE at the top of the tourist app
// (TouristApp) so it never resets when a tab or the modal re-renders:
//
//   motion sensor -> on-device detector -> POST fall-event -> 30 s countdown
//      -> cancel | SOS now | expiry -> server raises the incident
//
// The countdown is driven by an absolute deadline held in a ref (anchored on the
// SERVER's `seconds_remaining` against performance.now()), not by counting
// renders or a decrementing state, so re-renders, throttled background tabs and
// a slow response cannot make it drift. The server also owns the clock: if this
// app is closed or loses connection, the backend's sweep still escalates.
export default function useFallDetection({ tid, posRef, geo, onSosTriggered }) {
  const [config, setConfig] = useState(null)
  const [configError, setConfigError] = useState(null)
  const [phase, setPhase] = useState('idle')
  const [event, setEvent] = useState(null)
  const [remaining, setRemaining] = useState(0)
  const [history, setHistory] = useState([])
  const [offlineRetry, setOfflineRetry] = useState(false)

  const phaseRef = useRef('idle')
  const eventRef = useRef(null)
  const deadlineRef = useRef(0)
  const expiredRef = useRef(false)
  const startPromiseRef = useRef(null)
  const detectorRef = useRef(null)
  const latest = useRef({})
  const unmounted = useRef(false)

  const setPhaseBoth = (p) => { phaseRef.current = p; setPhase(p) }
  // StrictMode (dev) runs effect cleanups once right after mount, so the flag
  // must be re-armed by the effect body or every later update would be dropped.
  useEffect(() => {
    unmounted.current = false
    return () => { unmounted.current = true }
  }, [])

  // ---------------------------------------------------------------- server state
  const refreshHistory = useCallback(async () => {
    if (!tid) return
    try { setHistory(await listFallEvents(tid)) } catch { /* history is best-effort */ }
  }, [tid])

  // Apply an authoritative server record to local state.
  const adopt = (ev) => {
    eventRef.current = ev
    setEvent(ev)
    if (ev.status === 'pending') {
      deadlineRef.current = performance.now() + ev.seconds_remaining * 1000
      setRemaining(Math.ceil(ev.seconds_remaining))
      if (!LIVE.has(phaseRef.current)) { expiredRef.current = false; setPhaseBoth('countdown') }
    } else if (ev.status === 'cancelled') setPhaseBoth('cancelled')
    else if (ev.status === 'confirmed') setPhaseBoth('confirmed')
    else if (ev.status === 'sos_triggered') setPhaseBoth('sos')
    if (ev.status !== 'pending') refreshHistory()
  }

  // Retry a request while we are still in a live phase and it never reached the
  // server; surface a real server answer to the caller.
  const withRetry = async (fn) => {
    for (;;) {
      try {
        const out = await fn()
        setOfflineRetry(false)
        return out
      } catch (err) {
        if (!isNetworkError(err) || unmounted.current || !LIVE.has(phaseRef.current)) throw err
        setOfflineRetry(true)
        await sleep(FALL_RETRY_MS)
      }
    }
  }

  // ---------------------------------------------------------------- detection -> countdown
  const beginDetection = ({ type, source, metadata }) => {
    if (!tid || !config?.enabled || LIVE.has(phaseRef.current)) return false
    const pos = posRef?.current
    const body = {
      event_type: type, source, client_event_id: newClientEventId(),
      metadata: { ...metadata, detector: source === 'simulation' ? 'simulation' : 'devicemotion' },
    }
    if (hasFix(pos)) {
      body.lat = pos[0]; body.lng = pos[1]
      if (Number.isFinite(geo?.position?.accuracy)) body.accuracy_m = geo.position.accuracy
    }
    const detectedPerf = performance.now()
    const provisional = {
      id: null, event_type: type, source, simulated: source === 'simulation',
      status: 'pending', has_location: 'lat' in body,
    }
    eventRef.current = provisional
    setEvent(provisional)
    expiredRef.current = false
    // Start the visible countdown NOW; re-anchor on the server's clock once it answers.
    deadlineRef.current = performance.now() + config.countdown_seconds * 1000
    setRemaining(config.countdown_seconds)
    setPhaseBoth('countdown')
    navigator.vibrate?.([300, 150, 300])

    startPromiseRef.current = (async () => {
      try {
        // Relative age, re-measured on every attempt: a retry after an offline
        // spell carries the time already used up, and a wrong phone clock cannot
        // shorten the countdown (the server never sees our wall clock).
        const ev = await withRetry(() => startFallEvent(tid, {
          ...body, seconds_since_detection: (performance.now() - detectedPerf) / 1000,
        }))
        if (ev.deduplicated && ev.status !== 'pending') {
          // Folded into an incident that was already raised: nothing new to ask.
          eventRef.current = null; setEvent(null); setPhaseBoth('idle'); refreshHistory()
          return null
        }
        adopt(ev)
        return ev
      } catch {
        setPhaseBoth('error')
        return null
      }
    })()
    return true
  }
  latest.current.beginDetection = beginDetection

  // ---------------------------------------------------------------- expiry / cancel / SOS
  const runExpire = async () => {
    setPhaseBoth('sending')
    const ev0 = await startPromiseRef.current
    if (!ev0 || phaseRef.current !== 'sending') return
    try {
      const ev = await withRetry(() => confirmFallEvent(tid, eventRef.current.id))
      if (phaseRef.current === 'sending') adopt(ev)
    } catch (err) {
      if (err?.response?.status === 409 && phaseRef.current === 'sending') {
        // The server's clock says there is still time (or it was already settled):
        // re-sync from it rather than trusting our own timer.
        try {
          const act = await getActiveFallEvent(tid)
          if (act) { setPhaseBoth('countdown'); expiredRef.current = false; adopt(act); return }
        } catch { /* fall through */ }
        await resyncById()
      } else if (phaseRef.current === 'sending') setPhaseBoth('error')
    }
  }
  latest.current.runExpire = runExpire

  const resyncById = async () => {
    try {
      const all = await listFallEvents(tid)
      setHistory(all)
      const mine = all.find((e) => e.id === eventRef.current?.id)
      if (mine) adopt(mine)
    } catch { setPhaseBoth('error') }
  }

  const cancel = async () => {
    if (!LIVE.has(phaseRef.current) || phaseRef.current === 'cancelling') return
    setPhaseBoth('cancelling') // stops the local expiry timer immediately
    const ev0 = await startPromiseRef.current
    if (!ev0) return
    try {
      adopt(await withRetry(() => cancelFallEvent(tid, eventRef.current.id)))
    } catch (err) {
      if (err?.response?.status === 409) await resyncById() // already settled server-side
      else setPhaseBoth('error')
    }
  }

  const sosNow = async () => {
    if (!LIVE.has(phaseRef.current) || phaseRef.current === 'cancelling') return
    setPhaseBoth('sending')
    const ev0 = await startPromiseRef.current
    if (!ev0) return
    const pos = posRef?.current
    const body = hasFix(pos) ? { lat: pos[0], lng: pos[1] } : {}
    try {
      const out = await withRetry(() => sosFromFallEvent(tid, eventRef.current.id, body))
      adopt(out.event)
      if (out.sos) onSosTriggered?.(out.sos)
    } catch (err) {
      if (err?.response?.status === 409) await resyncById()
      else setPhaseBoth('error')
    }
  }

  const dismiss = () => {
    if (LIVE.has(phaseRef.current)) return
    eventRef.current = null
    setEvent(null)
    setPhaseBoth('idle')
    refreshHistory()
  }

  // ---------------------------------------------------------------- countdown timer
  // Keyed on `phase` only; reads the deadline from a ref, so a re-render of any
  // component can neither restart nor skip it.
  useEffect(() => {
    if (phase !== 'countdown') return undefined
    const tick = () => {
      const rem = Math.max(0, Math.ceil((deadlineRef.current - performance.now()) / 1000))
      setRemaining(rem)
      if (rem === 0 && !expiredRef.current) {
        expiredRef.current = true
        latest.current.runExpire()
      }
    }
    tick()
    const iv = setInterval(tick, 250)
    // Backgrounded tabs throttle timers; recompute the instant we are visible again.
    const onVis = () => { if (document.visibilityState === 'visible') tick() }
    document.addEventListener('visibilitychange', onVis)
    return () => { clearInterval(iv); document.removeEventListener('visibilitychange', onVis) }
  }, [phase])

  // ---------------------------------------------------------------- config + detector + sensor
  useEffect(() => {
    if (!tid) return
    getFallConfig().then(setConfig).catch((e) => setConfigError(e))
  }, [tid])

  const sensor = useMotionSensor({ onSample: (s) => detectorRef.current?.push(s) })

  useEffect(() => {
    if (!config?.enabled) { detectorRef.current = null; return }
    detectorRef.current = createFallDetector(
      { ...config.thresholds, cooldown_seconds: config.cooldown_seconds },
      (hit) => latest.current.beginDetection({
        type: hit.type, source: 'phone_sensor',
        metadata: {
          peak_g: hit.peak_g, freefall_ms: hit.freefall_ms, stillness_ms: hit.stillness_ms,
          ...(hit.speed_kmh != null ? { speed_kmh: hit.speed_kmh } : {}),
          ...(hit.sample_rate_hz != null ? { sample_rate_hz: hit.sample_rate_hz } : {}),
        },
      }),
      { speedWindowMs: FALL_SPEED_WINDOW_MS },
    )
  }, [config])

  // GPS speed -> detector (only a fresh fix counts; otherwise "speed unknown").
  const pos = geo?.position
  useEffect(() => {
    if (!pos || !detectorRef.current) return
    if (Date.now() - pos.timestamp <= FALL_SPEED_MAX_AGE_MS) {
      detectorRef.current.setSpeed(pos.speedKmh, performance.now())
    }
  }, [pos])

  // Resume monitoring on load if the user had it on and no gesture-gated
  // permission prompt is needed (iOS needs a tap, so it waits for the card).
  useEffect(() => {
    if (!config?.enabled || sensor.status !== 'off' || !readPref()) return
    if (typeof window.DeviceMotionEvent?.requestPermission === 'function') return
    sensor.start()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when config arrives
  }, [config])

  const enableMonitoring = () => { writePref(true); return sensor.start() }
  const disableMonitoring = () => { writePref(false); sensor.stop(); detectorRef.current?.reset() }

  // ---------------------------------------------------------------- server push + resume
  const { connected } = useWebSocket((msg) => {
    if (msg.event !== 'fall_event') return
    const mine = eventRef.current?.id != null && eventRef.current.id === msg.id
    if (msg.kind === 'started' && msg.status === 'pending' && !mine && !LIVE.has(phaseRef.current)) {
      adopt(msg) // e.g. a wearable fall: the phone shows the same countdown
    } else if (mine && msg.status !== 'pending') {
      adopt(msg) // cancelled from the band, or settled by the server's sweep
    }
    refreshHistory()
  }, tid ? `/ws/tourist/${tid}` : null)

  useEffect(() => {
    if (!tid || !config?.enabled) return
    getActiveFallEvent(tid).then((ev) => {
      if (ev && !LIVE.has(phaseRef.current)) adopt(ev)
    }).catch(() => {})
    refreshHistory()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- on load and whenever the socket (re)connects
  }, [tid, config, connected])

  const simulate = (type) => beginDetection({ type, source: 'simulation', metadata: {} })

  return {
    config, configError, enabled: !!config?.enabled,
    sensorStatus: sensor.status, monitoring: sensor.status === 'active',
    enableMonitoring, disableMonitoring,
    phase, event, remaining, history, offlineRetry,
    simulate, cancel, sosNow, dismiss,
    secureContext: typeof window !== 'undefined' ? window.isSecureContext !== false : true,
  }
}
