import { useCallback, useEffect, useRef, useState } from 'react'
import {
  addGuardian, cancelJourney, checkInJourney, endJourney, errorText, extendJourney,
  getActiveJourney, getWalkHomeConfig, isNetworkError, listGuardians, listJourneys,
  shareJourneyLocation, startJourney,
} from '../lib/walkHomeService'
import '../lib/walkHomeStrings'
import useWebSocket from '../useWebSocket'

const GEO_OPTS = { enableHighAccuracy: true, maximumAge: 10_000, timeout: 20_000 }
const POLL_MS = 30_000
const msg = (e, fb) => (isNetworkError(e) ? null : errorText(e, fb))

// The whole Walk-Me-Home flow for the signed-in tourist, mounted ONCE at the top
// of the tourist app (TouristApp) so a running journey keeps sharing location and
// counting whichever tab is open.
//
//  * The SERVER owns every deadline. This hook only displays them: remaining
//    times are computed from the server's `server_time` plus elapsed
//    performance.now(), so a wrong phone clock cannot move a deadline, and a
//    refresh/reopen simply re-reads GET .../walk-home/active.
//  * Location is read ONLY while a journey is live and is stopped the moment it
//    ends (the watch is created and torn down by the effect on `journey.id`).
//    Nothing is invented: with no fix, nothing is sent.
export default function useWalkHome({ tid }) {
  const [config, setConfig] = useState(null)
  const [journey, setJourney] = useState(null)
  const [loaded, setLoaded] = useState(false)
  const [guardians, setGuardians] = useState([])
  const [history, setHistory] = useState([])
  const [busy, setBusy] = useState(null) // start | checkin | end | cancel | extend
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [, setTick] = useState(0)
  const [sharing, setSharing] = useState({ state: 'off', sentAt: null })

  const anchor = useRef({ perf: 0, serverMs: 0 })
  const journeyRef = useRef(null)
  const latestFix = useRef(null) // { lat, lng, accuracy, recvPerf, sentFor }
  const unmounted = useRef(false)
  // StrictMode (dev) runs effect cleanups once right after mount, so the flag
  // must be re-armed by the effect body or every later update would be dropped.
  useEffect(() => {
    unmounted.current = false
    return () => { unmounted.current = true }
  }, [])

  const apply = useCallback((j) => {
    if (unmounted.current) return
    if (j) anchor.current = { perf: performance.now(), serverMs: Date.parse(j.server_time) }
    journeyRef.current = j
    setJourney(j)
  }, [])

  // ---------------------------------------------------------------- loading
  const refreshHistory = useCallback(async () => {
    if (!tid) return
    try { setHistory(await listJourneys(tid)) } catch { /* best effort */ }
  }, [tid])

  const refreshGuardians = useCallback(async () => {
    if (!tid) return
    try { setGuardians((await listGuardians(tid)).filter((g) => !g.revoked)) } catch { /* best effort */ }
  }, [tid])

  const refresh = useCallback(async () => {
    if (!tid) return
    try {
      apply(await getActiveJourney(tid))
      setError(null)
    } catch (e) {
      // 404 = the feature is switched off on this server; anything else keeps the last view.
      if (e?.response?.status === 404) setConfig((c) => (c ? { ...c, enabled: false } : c))
    } finally {
      if (!unmounted.current) setLoaded(true)
    }
  }, [tid, apply])

  useEffect(() => {
    if (!tid) return undefined
    let live = true
    getWalkHomeConfig().then((c) => { if (live) setConfig(c) }).catch(() => { if (live) setConfig({ enabled: false }) })
    refresh()
    refreshGuardians()
    refreshHistory()
    return () => { live = false }
  }, [tid, refresh, refreshGuardians, refreshHistory])

  const active = !!journey?.active

  // Live pushes on the tourist's own channel, plus a slow poll and a refresh when
  // the tab becomes visible again (a suspended browser misses pushes).
  useWebSocket((m) => {
    if (m?.event === 'walk_home' && m.journey) {
      apply(m.journey)
      if (!m.journey.active) refreshHistory()
    }
  }, tid ? `/ws/tourist/${tid}` : null)

  useEffect(() => {
    if (!active) return undefined
    const poll = setInterval(refresh, POLL_MS)
    const onVisible = () => { if (document.visibilityState === 'visible') refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { clearInterval(poll); document.removeEventListener('visibilitychange', onVisible) }
  }, [active, refresh])

  // 1 Hz re-render so the remaining times move; values are always recomputed from
  // the server anchor, never decremented, so a throttled tab cannot drift.
  useEffect(() => {
    if (!active) return undefined
    const iv = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(iv)
  }, [active])

  const serverNow = () => anchor.current.serverMs + (performance.now() - anchor.current.perf)
  const secondsUntil = (iso) => (iso ? Math.max(0, (Date.parse(iso) - serverNow()) / 1000) : 0)
  const remaining = active ? {
    toDeadline: secondsUntil(journey.next_checkin_deadline),
    toArrival: secondsUntil(journey.arrive_by),
    toEscalation: secondsUntil(journey.grace_deadline),
  } : { toDeadline: 0, toArrival: 0, toEscalation: 0 }

  // ---------------------------------------------------------------- location sharing
  const sessionId = active ? journey.id : null
  const intervalS = config?.location_interval_seconds
  useEffect(() => {
    if (!sessionId || !intervalS) {
      latestFix.current = null
      setSharing({ state: 'off', sentAt: null })
      return undefined
    }
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
      setSharing({ state: 'unsupported', sentAt: null })
      return undefined
    }
    let stopped = false
    setSharing({ state: 'starting', sentAt: null })

    const send = async () => {
      const fix = latestFix.current
      if (stopped || !fix || fix.sentFor === fix.recvPerf) return
      fix.sentFor = fix.recvPerf
      try {
        const r = await shareJourneyLocation(tid, sessionId, {
          lat: fix.lat, lng: fix.lng, accuracy_m: fix.accuracy,
          fix_age_seconds: Math.max(0, (performance.now() - fix.recvPerf) / 1000),
        })
        if (stopped) return
        apply(r.journey)
        setSharing({ state: 'sharing', sentAt: Date.now() })
      } catch (e) {
        if (stopped) return
        if (e?.response?.status === 409) { // journey ended elsewhere: stop sharing
          stopped = true
          refresh()
          return
        }
        fix.sentFor = null // resend this fix next round
        if (isNetworkError(e)) setSharing((s) => ({ ...s, state: 'offline' }))
      }
    }

    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const first = !latestFix.current
        latestFix.current = {
          lat: pos.coords.latitude, lng: pos.coords.longitude, accuracy: pos.coords.accuracy,
          recvPerf: performance.now(), sentFor: null,
        }
        if (first) send() // do not wait a whole interval to show the first position
      },
      (err) => {
        if (stopped) return
        setSharing((s) => ({ ...s, state: err?.code === 1 ? 'denied' : 'unavailable' }))
      },
      GEO_OPTS,
    )
    const iv = setInterval(send, intervalS * 1000)
    return () => {
      stopped = true
      clearInterval(iv)
      navigator.geolocation.clearWatch(watchId)
      latestFix.current = null
    }
  }, [sessionId, intervalS, tid, apply, refresh])

  // ---------------------------------------------------------------- actions
  const run = useCallback(async (kind, fn, okNotice) => {
    if (busy) return null
    setBusy(kind)
    setError(null)
    try {
      const j = await fn()
      apply(j)
      if (okNotice) setNotice(okNotice)
      return j
    } catch (e) {
      setError(msg(e, 'generic') || 'offline')
      return null
    } finally {
      if (!unmounted.current) setBusy(null)
    }
  }, [busy, apply])

  const currentPosition = () => new Promise((resolve) => {
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) return resolve(null)
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude }),
      () => resolve(null),
      { ...GEO_OPTS, timeout: 8000 },
    )
  })

  const start = useCallback(async (form) => {
    if (busy) return null
    setBusy('start')
    setError(null)
    setNotice(null)
    try {
      const here = await currentPosition() // null when denied/unavailable: simply not sent
      const body = { ...form }
      if (here) { body.start_lat = here.lat; body.start_lng = here.lng }
      const j = await startJourney(tid, body)
      apply(j)
      return j
    } catch (e) {
      setError(msg(e, 'generic') || 'offline')
      return null
    } finally {
      if (!unmounted.current) setBusy(null)
    }
  }, [busy, tid, apply])

  const checkIn = useCallback(
    () => journeyRef.current && run('checkin', () => checkInJourney(tid, journeyRef.current.id), 'checked_in'),
    [run, tid])
  const extend = useCallback(
    (minutes) => journeyRef.current && run('extend', () => extendJourney(tid, journeyRef.current.id, minutes)),
    [run, tid])
  const finish = useCallback(async (kind) => {
    const j = journeyRef.current
    if (!j) return null
    const out = await run(kind, () => (kind === 'end' ? endJourney : cancelJourney)(tid, j.id),
      kind === 'end' ? 'ended_ok' : 'ended_cancelled')
    if (out) refreshHistory()
    return out
  }, [run, tid, refreshHistory])
  const end = useCallback(() => finish('end'), [finish])
  const cancel = useCallback(() => finish('cancel'), [finish])

  const createGuardian = useCallback(async (name, contact) => {
    try {
      const g = await addGuardian(tid, { guardian_name: name, guardian_contact: contact })
      await refreshGuardians()
      return g
    } catch (e) {
      setError(msg(e, 'generic') || 'offline')
      return null
    }
  }, [tid, refreshGuardians])

  return {
    enabled: !!config?.enabled, config, loaded, journey, active, remaining, sharing,
    guardians, history, busy, error, notice, clearNotice: () => setNotice(null),
    clearError: () => setError(null),
    start, checkIn, extend, end, cancel, createGuardian, refresh, refreshGuardians,
  }
}
