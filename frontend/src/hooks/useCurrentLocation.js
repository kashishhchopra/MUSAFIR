import { useCallback, useEffect, useSyncExternalStore } from 'react'
import {
  LOCATION_STATUS, getState, request as requestLocation, requestApprox, startDeviceLocation, subscribe,
} from '../lib/deviceLocation'

// Where the device is, shared by every screen (see lib/deviceLocation.js): one request for the whole
// app, a remembered last fix for an instant start, and honest states when it cannot be had.
// status:
//   idle         nothing requested yet          locating   waiting for the device
//   ok           `position` holds a position     denied     the user refused permission
//   unavailable  GPS off / no signal             timeout    no answer in time
//   unsupported  this device/browser has no geolocation
// Besides `status`/`position`: `stale` (a remembered fix being refreshed, not a live one), `source`
// ('gps' | 'cache' | 'ip' = approximate), `permission` (the browser's own state), `refreshing`.
export { LOCATION_STATUS }

export function statusFromError(err) {
  // GeolocationPositionError: 1 denied, 2 unavailable, 3 timeout
  if (err?.code === 1) return LOCATION_STATUS.DENIED
  if (err?.code === 3) return LOCATION_STATUS.TIMEOUT
  return LOCATION_STATUS.UNAVAILABLE
}

// Promise form of a one-shot fix, for code that is not a component (e.g. SOS: it must not leave the
// tourist waiting on a slow GPS, so it asks the device directly and does not share a request).
// Resolves {lat, lng, accuracy, timestamp}, or null if no real fix could be had.
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

// `quick` (default): a Wi-Fi/cell fix in about 10 s -- enough for "what is near me". `quick: false`
// asks for a precise GPS fix (up to 20 s) when `request()` has to start a fresh request.
export default function useCurrentLocation({ auto = false, quick = true } = {}) {
  const s = useSyncExternalStore(subscribe, getState, getState)
  useEffect(() => { if (auto) startDeviceLocation() }, [auto])
  const request = useCallback(() => requestLocation({ precise: !quick }), [quick])
  return {
    status: s.status, position: s.position, stale: s.stale, source: s.source, permission: s.permission,
    refreshing: s.refreshing, error: s.error, request, requestApprox, locating: s.status === LOCATION_STATUS.LOCATING,
  }
}
