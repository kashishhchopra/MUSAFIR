// Hand a place or a route to Google Maps (or whatever app the device uses for
// these links -- on Android the Maps app opens directly). Plain universal
// links: no API key, work in the browser and inside the Capacitor WebView.

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
  // _blank hands the link to the system (Maps app on Android) instead of
  // navigating the app's own WebView away from the UI.
  const w = window.open(url, '_blank', 'noopener,noreferrer')
  if (!w) window.location.href = url
}

export const openPlace = (p) => openUrl(placeUrl(p))
export const openDirections = (opts) => openUrl(directionsUrl(opts))
