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

export function statusFromError(err) {
  // GeolocationPositionError: 1 denied, 2 unavailable, 3 timeout
  if (err?.code === 1) return LOCATION_STATUS.DENIED
  if (err?.code === 3) return LOCATION_STATUS.TIMEOUT
  return LOCATION_STATUS.UNAVAILABLE
}

export default function useCurrentLocation({ auto = false } = {}) {
  const [status, setStatus] = useState(LOCATION_STATUS.IDLE)
  const [position, setPosition] = useState(null) // {lat, lng, accuracy, timestamp}
  const mounted = useRef(true)
  useEffect(() => () => { mounted.current = false }, [])

  const request = useCallback(() => {
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) {
      setStatus(LOCATION_STATUS.UNSUPPORTED)
      return Promise.resolve(null)
    }
    setStatus(LOCATION_STATUS.LOCATING)
    return new Promise((resolve) => {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          const fix = {
            lat: pos.coords.latitude, lng: pos.coords.longitude,
            accuracy: pos.coords.accuracy, timestamp: pos.timestamp,
          }
          if (mounted.current) { setPosition(fix); setStatus(LOCATION_STATUS.OK) }
          resolve(fix)
        },
        (err) => {
          if (mounted.current) setStatus(statusFromError(err))
          resolve(null)
        },
        OPTIONS,
      )
    })
  }, [])

  useEffect(() => {
    if (auto) request()
  }, [auto, request])

  return { status, position, request, locating: status === LOCATION_STATUS.LOCATING }
}
