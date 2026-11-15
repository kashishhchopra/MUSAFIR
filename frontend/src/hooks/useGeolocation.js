import { useCallback, useEffect, useRef, useState } from 'react'
import { registerPlugin } from '@capacitor/core'
import { isNativeApp } from '../lib/platform'
import { haversineKm } from '../components/geo'

const BackgroundGeolocation = registerPlugin('BackgroundGeolocation')

// How often the "is there a pending fix to emit?" check runs on a timer. Only
// matters where fixes stop arriving on their own (a browser that goes quiet
// while the device is still); the native plugin delivers a fix every second.
const RECHECK_MS = 5000

// Wraps navigator.geolocation.watchPosition with permission-state handling.
// `enabled` gates the whole thing (the app also has its own tracking toggle,
// which now controls this hook instead of a fake random walk).
// In the Android app, watchPosition stops when the screen locks, so it uses
// the background-geolocation plugin instead: a foreground service with a
// persistent notification keeps fixes coming while the phone is in a pocket.
//
// `position` only changes when a fix is worth acting on:
//  - the first fix;
//  - a fix at least `minDistanceM` from the last emitted one, once
//    `minIntervalMs` has passed since that emission (a move made inside the
//    interval is emitted when the interval ends, not dropped);
//  - with `heartbeatMs`, the latest fix again after that long without an
//    emission, so a tourist standing still still shows as alive.
// The plugin runs GPS at 1 Hz whatever its distanceFilter (that filter only
// hides fixes from JS), so asking it for every fix costs no extra GPS power
// and lets this gating -- and the heartbeat -- keep working with the screen
// locked, where JS timers may be throttled but plugin callbacks still arrive.
export default function useGeolocation({
  enabled = true, minDistanceM = 25, minIntervalMs = 0, heartbeatMs = 0,
} = {}) {
  const [position, setPosition] = useState(null)
  const [error, setError] = useState(null)
  const [permissionState, setPermissionState] = useState('unknown') // granted|denied|prompt|unsupported|unknown
  const watchIdRef = useRef(null)
  const latestRef = useRef(null) // newest fix seen, emitted or not
  const emittedRef = useRef(null) // { fix, at } of the last emission

  const consider = useCallback((fix) => {
    if (!fix) return
    const now = Date.now()
    const last = emittedRef.current
    let emit = !last
    if (last) {
      const elapsed = now - last.at
      const movedM = haversineKm(last.fix.lat, last.fix.lng, fix.lat, fix.lng) * 1000
      emit = (movedM >= minDistanceM && elapsed >= minIntervalMs)
        || (heartbeatMs > 0 && elapsed >= heartbeatMs)
    }
    if (!emit) return
    emittedRef.current = { fix, at: now }
    setPosition({ ...fix }) // fresh object: a heartbeat must register as a change
  }, [minDistanceM, minIntervalMs, heartbeatMs])

  const onFix = useCallback((fix) => {
    latestRef.current = fix
    consider(fix)
  }, [consider])

  // Timer half of the gating: emits a move that arrived inside minIntervalMs,
  // or a heartbeat, even if no further fix comes in to trigger it.
  useEffect(() => {
    if (!enabled || (!minIntervalMs && !heartbeatMs)) return undefined
    const iv = setInterval(() => consider(latestRef.current), RECHECK_MS)
    return () => clearInterval(iv)
  }, [enabled, minIntervalMs, heartbeatMs, consider])

  useEffect(() => {
    if (enabled) return
    latestRef.current = null
    emittedRef.current = null
  }, [enabled])

  useEffect(() => {
    if (!isNativeApp() || !enabled) return
    let watcherId = null
    let cancelled = false
    BackgroundGeolocation.addWatcher(
      {
        backgroundTitle: 'MUSAFIR is protecting you',
        backgroundMessage: 'Sharing your location for your safety.',
        requestPermissions: true,
        stale: false,
        distanceFilter: 0, // every fix; consider() does the filtering
      },
      (loc, err) => {
        if (err) {
          setError(err.message)
          if (err.code === 'NOT_AUTHORIZED') setPermissionState('denied')
          return
        }
        setError(null)
        setPermissionState('granted')
        onFix({
          lat: loc.latitude,
          lng: loc.longitude,
          accuracy: loc.accuracy,
          speedKmh: loc.speed != null ? Math.max(0, loc.speed) * 3.6 : 0,
          heading: loc.bearing ?? null,
          timestamp: loc.time,
        })
      },
    ).then((id) => {
      if (cancelled) BackgroundGeolocation.removeWatcher({ id })
      else watcherId = id
    })
    return () => {
      cancelled = true
      if (watcherId != null) BackgroundGeolocation.removeWatcher({ id: watcherId })
    }
  }, [enabled, onFix])

  useEffect(() => {
    if (isNativeApp()) return
    if (!('geolocation' in navigator)) {
      setPermissionState('unsupported')
      return
    }
    if (!enabled) {
      if (watchIdRef.current != null) {
        navigator.geolocation.clearWatch(watchIdRef.current)
        watchIdRef.current = null
      }
      return
    }

    // navigator.permissions is not universally supported (notably older
    // Safari); watchPosition below still works without it, this just gives
    // an earlier, more specific UI message when denied.
    if (navigator.permissions?.query) {
      navigator.permissions
        .query({ name: 'geolocation' })
        .then((status) => {
          setPermissionState(status.state)
          status.onchange = () => setPermissionState(status.state)
        })
        .catch(() => {})
    }

    watchIdRef.current = navigator.geolocation.watchPosition(
      (pos) => {
        setError(null)
        setPermissionState('granted')
        onFix({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
          speedKmh: pos.coords.speed != null ? Math.max(0, pos.coords.speed) * 3.6 : 0,
          heading: pos.coords.heading ?? null,
          timestamp: pos.timestamp,
        })
      },
      (err) => {
        setError(err.message)
        if (err.code === err.PERMISSION_DENIED) setPermissionState('denied')
      },
      { enableHighAccuracy: true, maximumAge: 10_000, timeout: 15_000 },
    )

    return () => {
      if (watchIdRef.current != null) {
        navigator.geolocation.clearWatch(watchIdRef.current)
        watchIdRef.current = null
      }
    }
  }, [enabled, onFix])

  return { position, error, permissionState }
}
