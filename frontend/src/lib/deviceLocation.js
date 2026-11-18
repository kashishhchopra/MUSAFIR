// The ONE place the app asks the device where it is.
//
// Before this, every card (Nearby, Discovery, Crowd, Fair Price, the itinerary...) called the
// browser's geolocation API on its own, at the same moment, and each had its own spinner: a slow
// or unanswered request left them all "Getting your location…" at once. Now:
//   * there is a single shared request (concurrent callers get the same answer);
//   * the last good fix is remembered (localStorage), so after the first success every screen has a
//     position INSTANTLY, marked `stale` ("last known, updating…") until a live fix replaces it;
//   * a slow wait is explained (waiting for the permission prompt vs. the computer not answering);
//   * on request, an APPROXIMATE city-level position can be taken from the internet connection.
//
// Nothing here ever invents a position: it is a device fix, the user's own remembered fix (age
// shown), or an approximation the user asked for and that is labelled as one.
export const LOCATION_STATUS = {
  IDLE: 'idle', LOCATING: 'locating', OK: 'ok', DENIED: 'denied',
  UNAVAILABLE: 'unavailable', TIMEOUT: 'timeout', UNSUPPORTED: 'unsupported',
}
export const CACHE_KEY = 'musafir.lastFix'
export const CACHE_MAX_AGE_MS = 6 * 3600 * 1000 // an older remembered position is not trusted
export const STALE_REFRESH_MS = 5 * 60 * 1000   // refresh quietly when the app comes back after this long
const QUICK = { enableHighAccuracy: false, timeout: 10_000, maximumAge: 60_000 } // Wi-Fi/cell: fine for "what is near me"
const PRECISE = { enableHighAccuracy: true, timeout: 20_000, maximumAge: 15_000 }
const WATCHDOG_EXTRA_MS = 3_000 // the browser's own timeout only starts after the permission prompt is answered

const supported = () => typeof navigator !== 'undefined' && 'geolocation' in navigator && !!navigator.geolocation

function readCache() {
  try {
    const c = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null')
    if (c && Number.isFinite(c.lat) && Number.isFinite(c.lng) && Date.now() - c.timestamp < CACHE_MAX_AGE_MS) return c
  } catch { /* unreadable: ignore */ }
  return null
}
const writeCache = (p) => { try { localStorage.setItem(CACHE_KEY, JSON.stringify(p)) } catch { /* optional */ } }
const clearCache = () => { try { localStorage.removeItem(CACHE_KEY) } catch { /* optional */ } }

const initial = () => {
  const c = readCache()
  return {
    status: c ? LOCATION_STATUS.OK : LOCATION_STATUS.IDLE, position: c, stale: !!c, source: c ? 'cache' : null,
    permission: 'unknown', refreshing: false, error: null,
  }
}

let state = initial()
let inflight = null
let started = false
let permWatched = false
let onVisible = null
const listeners = new Set()

const set = (patch) => { state = { ...state, ...patch }; listeners.forEach((l) => l()) }
export const getState = () => state
export const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn) }

/** Tests: forget everything (memory, cache, in-flight request). */
export function resetDeviceLocation() {
  clearCache()
  inflight = null
  started = false
  permWatched = false
  if (onVisible && typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible)
  onVisible = null
  state = initial()
  listeners.forEach((l) => l())
}

const kindFromError = (err) => (err?.code === 1 ? LOCATION_STATUS.DENIED : err?.code === 3 ? LOCATION_STATUS.TIMEOUT : LOCATION_STATUS.UNAVAILABLE)

function applyFix(pos) {
  const fix = {
    lat: pos.coords.latitude, lng: pos.coords.longitude,
    accuracy: pos.coords.accuracy, timestamp: pos.timestamp || Date.now(),
  }
  writeCache(fix)
  set({ status: LOCATION_STATUS.OK, position: fix, stale: false, source: 'gps', refreshing: false, error: null })
  return fix
}

function fail(kind) {
  if (kind === LOCATION_STATUS.DENIED) { // the user said no: forget any remembered position too
    clearCache()
    set({ status: LOCATION_STATUS.DENIED, position: null, stale: false, source: null, refreshing: false, error: kind })
  } else if (state.position) { // keep using what we have, but say it is not fresh
    set({ status: LOCATION_STATUS.OK, stale: state.source !== 'ip', refreshing: false, error: kind })
  } else {
    set({ status: kind, refreshing: false, error: kind })
  }
}

/** Ask the device now. Concurrent callers share one request. Resolves a live fix, or null. */
export function request({ precise = false } = {}) {
  if (inflight) return inflight
  if (!supported()) { set({ status: LOCATION_STATUS.UNSUPPORTED }); return Promise.resolve(null) }
  const opts = precise ? PRECISE : QUICK
  set(state.position ? { refreshing: true, error: null } : { status: LOCATION_STATUS.LOCATING, error: null })
  inflight = new Promise((resolve) => {
    let settled = false
    const watchdog = setTimeout(() => {
      if (settled) return
      settled = true
      fail(LOCATION_STATUS.TIMEOUT)
      resolve(null)
    }, opts.timeout + WATCHDOG_EXTRA_MS)
    navigator.geolocation.getCurrentPosition(
      (pos) => { // a late answer (permission granted after the watchdog fired) is still welcome
        clearTimeout(watchdog)
        const fix = applyFix(pos)
        if (!settled) { settled = true; resolve(fix) }
      },
      (err) => {
        clearTimeout(watchdog)
        if (settled) return
        settled = true
        fail(kindFromError(err))
        resolve(null)
      },
      opts,
    )
  }).finally(() => { inflight = null })
  return inflight
}

function watchPermission() {
  if (permWatched || typeof navigator === 'undefined' || !navigator.permissions?.query) return
  permWatched = true
  navigator.permissions.query({ name: 'geolocation' }).then((p) => {
    const sync = () => {
      const was = state.permission
      set({ permission: p.state })
      // The user just clicked Allow (maybe after we gave up waiting): go and get the position.
      if (p.state === 'granted' && was !== 'granted' && state.status !== LOCATION_STATUS.OK) request()
    }
    sync()
    p.onchange = sync
  }).catch(() => { /* no Permissions API: the hints just say less */ })
}

/** Begin (once): ask for the position and keep it fresh when the app is reopened. Safe to call often. */
export function startDeviceLocation() {
  watchPermission()
  if (!started) {
    started = true
    if (typeof document !== 'undefined') {
      onVisible = () => {
        const age = state.position ? Date.now() - state.position.timestamp : Infinity
        if (document.visibilityState === 'visible' && (state.stale || age > STALE_REFRESH_MS)) request()
      }
      document.addEventListener('visibilitychange', onVisible)
    }
  }
  if (!inflight && (!state.position || state.stale)) request()
}

/**
 * An APPROXIMATE, city-level position from the internet connection (free keyless services; this
 * sends the user's IP address to them, so it only ever runs when the user asks). Never replaces a
 * fresh device fix. Resolves the position or null.
 */
export async function requestApprox() {
  if (state.position && !state.stale && state.source === 'gps') return state.position
  const attempts = [
    ['https://get.geojs.io/v1/ip/geo.json', (j) => ({ lat: parseFloat(j.latitude), lng: parseFloat(j.longitude), acc: Number(j.accuracy) || 0, place: [j.city, j.country].filter(Boolean).join(', ') })],
    ['https://ipwho.is/', (j) => (j.success === false ? null : { lat: j.latitude, lng: j.longitude, acc: 0, place: [j.city, j.country].filter(Boolean).join(', ') })],
  ]
  set({ error: null, refreshing: true })
  for (const [url, parse] of attempts) {
    const ctl = new AbortController()
    const timer = setTimeout(() => ctl.abort(), 6000)
    try {
      const r = await fetch(url, { signal: ctl.signal })
      if (!r.ok) continue
      const p = parse(await r.json())
      if (p && Number.isFinite(p.lat) && Number.isFinite(p.lng)) {
        // Deliberately NOT cached: an approximation must never be remembered as if it were a fix.
        const position = { lat: p.lat, lng: p.lng, accuracy: Math.max(p.acc * 1000, 5000), timestamp: Date.now(), place: p.place }
        set({ status: LOCATION_STATUS.OK, position, stale: false, source: 'ip', refreshing: false, error: null })
        return position
      }
    } catch { /* try the next service */ } finally { clearTimeout(timer) }
  }
  set({ refreshing: false, error: 'approx_failed' })
  return null
}
