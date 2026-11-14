import { useCallback, useEffect, useRef, useState } from 'react'
import { FALL_SENSOR_PROBE_MS } from '../config'
import { G } from '../lib/fallDetector'

// Subscribes to the browser's devicemotion events and hands normalised samples
// to `onSample` ({ t ms, ax, ay, az in g, gyro deg/s | null }).
//
// status: 'off' | 'starting' | 'active' | 'denied' | 'unsupported' | 'unavailable'
//   denied       iOS/Safari motion permission was refused
//   unsupported  no DeviceMotionEvent at all
//   unavailable  the event exists but never carries readings (typical on a
//                laptop, or on a phone over plain HTTP) -- detected by a probe
//                timeout rather than guessed
//
// start() must be called from a user gesture: iOS only shows its permission
// prompt then. Nothing is read, stored or sent until start() succeeds, and
// only the latest sample is ever handed on -- no history is kept here.
export default function useMotionSensor({ onSample }) {
  const [status, setStatus] = useState('off')
  const cbRef = useRef(onSample)
  cbRef.current = onSample
  const handlerRef = useRef(null)
  const probeRef = useRef(null)
  const gotDataRef = useRef(false)

  const detach = useCallback(() => {
    if (handlerRef.current) {
      window.removeEventListener('devicemotion', handlerRef.current)
      handlerRef.current = null
    }
    clearTimeout(probeRef.current)
  }, [])

  const stop = useCallback(() => {
    detach()
    setStatus('off')
  }, [detach])

  const start = useCallback(async () => {
    if (typeof window === 'undefined' || typeof window.DeviceMotionEvent === 'undefined') {
      setStatus('unsupported')
      return 'unsupported'
    }
    detach()
    setStatus('starting')
    // iOS 13+ gates motion data behind an explicit, gesture-triggered prompt.
    if (typeof window.DeviceMotionEvent.requestPermission === 'function') {
      try {
        const res = await window.DeviceMotionEvent.requestPermission()
        if (res !== 'granted') {
          setStatus('denied')
          return 'denied'
        }
      } catch {
        setStatus('denied')
        return 'denied'
      }
    }

    gotDataRef.current = false
    const handler = (e) => {
      const a = e.accelerationIncludingGravity
      if (!a || a.x == null || a.y == null || a.z == null) return
      if (!gotDataRef.current) {
        gotDataRef.current = true
        clearTimeout(probeRef.current)
        setStatus('active')
      }
      const r = e.rotationRate
      const gyro = r && r.alpha != null && r.beta != null && r.gamma != null
        ? Math.hypot(r.alpha, r.beta, r.gamma)
        : null
      cbRef.current?.({
        t: performance.now(), ax: a.x / G, ay: a.y / G, az: a.z / G, gyro,
      })
    }
    handlerRef.current = handler
    window.addEventListener('devicemotion', handler)

    probeRef.current = setTimeout(() => {
      if (!gotDataRef.current) {
        detach()
        setStatus('unavailable')
      }
    }, FALL_SENSOR_PROBE_MS)
    return 'starting'
  }, [detach])

  useEffect(() => detach, [detach])

  return { status, start, stop }
}
