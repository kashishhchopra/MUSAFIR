// Location access that keeps working with the screen locked.
//  * Browser: plain navigator.geolocation (unchanged behaviour).
//  * iOS app (Capacitor): the background-geolocation plugin, so Walk-Me-Home and
//    live tracking keep reporting while the phone is locked / app backgrounded.
// Both return positions shaped like the browser's ({coords, timestamp}).
import { registerPlugin } from '@capacitor/core'
import { isNative } from './native'

const Native = registerPlugin('BackgroundGeolocation')

const toPos = (l) => ({
  coords: {
    latitude: l.latitude, longitude: l.longitude, accuracy: l.accuracy,
    speed: l.speed ?? null, heading: l.bearing ?? null,
  },
  timestamp: l.time || Date.now(),
})

const PERMISSION_DENIED = 1
const POSITION_UNAVAILABLE = 2

export const geoSupported = () => isNative() || (typeof navigator !== 'undefined' && 'geolocation' in navigator)

/** Start watching. Returns a stop() function (safe to call more than once). */
export function watchPosition(onPos, onError, options = {}) {
  if (!isNative()) {
    const id = navigator.geolocation.watchPosition(onPos, onError, options)
    return () => navigator.geolocation.clearWatch(id)
  }
  let stopped = false
  let watcherId = null
  Native.addWatcher(
    {
      requestPermissions: true,
      backgroundTitle: 'MUSAFIR is keeping you safe',
      backgroundMessage: 'Sharing your location for your safety.',
      distanceFilter: 5,
    },
    (location, error) => {
      if (stopped) return
      if (error) {
        onError?.({
          code: error.code === 'NOT_AUTHORIZED' ? PERMISSION_DENIED : POSITION_UNAVAILABLE,
          message: error.message || 'Location unavailable',
          PERMISSION_DENIED,
        })
        return
      }
      if (location) onPos(toPos(location))
    },
  ).then((id) => {
    if (stopped) Native.removeWatcher({ id })
    else watcherId = id
  }).catch((e) => onError?.({ code: POSITION_UNAVAILABLE, message: String(e?.message || e), PERMISSION_DENIED }))
  return () => {
    if (stopped) return
    stopped = true
    if (watcherId != null) Native.removeWatcher({ id: watcherId })
  }
}

/** One-shot position (foreground use). */
export function getCurrentPosition(onPos, onError, options = {}) {
  if (!isNative()) return navigator.geolocation.getCurrentPosition(onPos, onError, options)
  let done = false
  let stop = () => {}
  const finish = () => { done = true; stop() }
  const timer = setTimeout(() => {
    if (done) return
    finish()
    onError?.({ code: POSITION_UNAVAILABLE, message: 'timeout', PERMISSION_DENIED })
  }, options.timeout || 15000)
  stop = watchPosition(
    (p) => { if (done) return; clearTimeout(timer); finish(); onPos(p) },
    (e) => { if (done) return; clearTimeout(timer); finish(); onError?.(e) },
    options,
  )
  if (done) stop()
}
