// Hand a place or a route to Google Maps (or whatever app the device uses for
// these links -- on Android the Maps app opens directly). Plain universal
// links: no API key, work in the browser and inside the Capacitor WebView.

import { isNativeApp } from './platform'

const GOOGLE_MODE = { driving: 'driving', walking: 'walking', bicycling: 'bicycling', transit: 'transit' }
const fmt = (p) => `${p.lat},${p.lng}`

export function placeUrl({ lat, lng }) {
  return `https://www.google.com/maps/search/?api=1&query=${lat},${lng}`
}

// Turn-by-turn from `origin` (omit to let Maps use the device's live location)
// to `destination`, through any `stops`, in the given travel mode.
export function directionsUrl({ origin, destination, stops = [], mode = 'driving' }) {
  const params = new URLSearchParams({
    api: '1',
    destination: fmt(destination),
    travelmode: GOOGLE_MODE[mode] || 'driving',
  })
  if (origin) params.set('origin', fmt(origin))
  if (stops.length) params.set('waypoints', stops.map(fmt).join('|'))
  return `https://www.google.com/maps/dir/?${params.toString()}`
}

export function openUrl(url) {
  // In the Android app, navigating to an off-origin URL is intercepted by
  // Capacitor and handed to the system (ACTION_VIEW) -- the Maps app opens and
  // our WebView stays where it is. window.open is not wired up there.
  if (isNativeApp()) {
    window.location.assign(url)
    return
  }
  // Browser: a new tab. (No 'noopener' feature -- it makes window.open return
  // null even on success, which would look like a blocked popup.)
  const w = window.open(url, '_blank')
  if (w) {
    try { w.opener = null } catch { /* cross-origin proxy -- nothing to do */ }
  } else {
    window.location.assign(url) // popup blocked: fall back to same-tab navigation
  }
}

export const openPlace = (p) => openUrl(placeUrl(p))
export const openDirections = (opts) => openUrl(directionsUrl(opts))
