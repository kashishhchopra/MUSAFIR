import { useEffect, useRef, useState } from 'react'
import { registerPlugin } from '@capacitor/core'
import { isNativeApp } from '../lib/platform'

const BackgroundGeolocation = registerPlugin('BackgroundGeolocation')

// Wraps navigator.geolocation.watchPosition with permission-state handling.
// `enabled` gates the whole thing (the app also has its own tracking toggle,
// which now controls this hook instead of a fake random walk).
// In the Android app, watchPosition stops when the screen locks, so it uses
// the background-geolocation plugin instead: a foreground service with a
// persistent notification keeps fixes coming while the phone is in a pocket.
export default function useGeolocation({ enabled = true } = {}) {
  const [position, setPosition] = useState(null)
  const [error, setError] = useState(null)
  const [permissionState, setPermissionState] = useState('unknown') // granted|denied|prompt|unsupported|unknown
  const watchIdRef = useRef(null)

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
        distanceFilter: 25,
      },
      (loc, err) => {
        if (err) {
          setError(err.message)
          if (err.code === 'NOT_AUTHORIZED') setPermissionState('denied')
          return
        }
        setError(null)
        setPermissionState('granted')
        setPosition({
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
  }, [enabled])

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
        setPosition({
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
  }, [enabled])

  return { position, error, permissionState }
}
