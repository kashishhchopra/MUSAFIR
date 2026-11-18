import { useCallback, useEffect, useRef, useState } from 'react'

// One-shot "where am I right now" from the device's real GPS -- for features
// that need the tourist's actual position at the moment they act (Fair Price,
// travel time to a destination), as opposed to useGeolocation's continuous
// safety tracking. Never falls back to a stored or default position: if the
// device can't give a fix, the caller is told why (`status`) and must show it.
//
// status:
//   idle         nothing requested yet
//   locating     waiting for a fix (permission prompt or GPS warm-up)
//   ok           `position` holds a real fix
//   denied       the user refused location permission
//   unavailable  GPS is off / no signal / position could not be determined
//   timeout      no fix within the time limit
//   unsupported  this device/browser has no geolocation at all
export const LOCATION_STATUS = {
  IDLE: 'idle', LOCATING: 'locating', OK: 'ok', DENIED: 'denied',
  UNAVAILABLE: 'unavailable', TIMEOUT: 'timeout', UNSUPPORTED: 'unsupported',
}

const OPTIONS = { enableHighAccuracy: true, timeout: 20_000, maximumAge: 15_000 }
const QUICK_OPTIONS = { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 }

export function statusFromError(err) {
  // GeolocationPositionError: 1 denied, 2 unavailable, 3 timeout
  if (err?.code === 1) return LOCATION_STATUS.DENIED
  if (err?.code === 3) return LOCATION_STATUS.TIMEOUT
  return LOCATION_STATUS.UNAVAILABLE
}

// Promise form of the same one-shot fix, for code that is not a component (e.g.
// SOS: it must not leave the tourist waiting on a slow GPS). Resolves
// {lat, lng, accuracy, timestamp}, or null if no real fix could be had.
export function getCurrentFix({ timeout = 8_000, maximumAge = 60_000 } = {}) {
  if (typeof navigator === 'undefined' || !('geolocation' in navigator)) return Promise.resolve(null)
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({
        lat: pos.coords.latitude, lng: pos.coords.longitude,
        accuracy: pos.coords.accuracy, timestamp: pos.timestamp,
      }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout, maximumAge },
    )
  })
}

// `quick`: desktop-friendly fix -- network/Wi-Fi accuracy is fine for a route start,
// so do not insist on GPS hardware (which a laptop does not have and which makes the
// browser hang), and give up after 10 s so the caller can offer another way.
export default function useCurrentLocation({ auto = false, quick = false } = {}) {
  const [status, setStatus] = useState(LOCATION_STATUS.IDLE)
  const [position, setPosition] = useState(null) // {lat, lng, accuracy, timestamp}
  const mounted = useRef(true)
  useEffect(() => () => { mounted.current = false }, [])

  const request = useCallback(() => {
    const options = quick ? QUICK_OPTIONS : OPTIONS
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
      setStatus(LOCATION_STATUS.UNSUPPORTED)
      return Promise.resolve(null)
    }
    setStatus(LOCATION_STATUS.LOCATING)
    return new Promise((resolve) => {
      // The browser's own timeout only starts AFTER the permission prompt is
      // answered, so an ignored/hidden prompt (or a stuck OS location service)
      // would leave us "locating" forever. This watchdog ends the wait; a late
      // answer is still accepted below if it does arrive.
      let settled = false
      const watchdog = setTimeout(() => {
        if (settled) return
        if (mounted.current) setStatus(LOCATION_STATUS.TIMEOUT)
        resolve(null)
      }, (options.timeout || 20_000) + 3_000)
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          settled = true
          clearTimeout(watchdog)
          const fix = {
            lat: pos.coords.latitude, lng: pos.coords.longitude,
            accuracy: pos.coords.accuracy, timestamp: pos.timestamp,
          }
          if (mounted.current) { setPosition(fix); setStatus(LOCATION_STATUS.OK) }
          resolve(fix)
        },
        (err) => {
          settled = true
          clearTimeout(watchdog)
          if (mounted.current) setStatus(statusFromError(err))
          resolve(null)
        },
        options,
      )
    })
  }, [quick])

  useEffect(() => {
    if (auto) request()
  }, [auto, request])

  return { status, position, request, locating: status === LOCATION_STATUS.LOCATING }
}
