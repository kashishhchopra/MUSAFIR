import api from '../api'

// Thin wrappers over /maps/* (real place search + road routing, see
// backend/app/services/maps.py). Routed answers
// carry `demo: true` when only a straight-line estimate was possible -- callers
// must say so rather than present it as a road distance.

export const searchPlaces = (q, limit = 5, near) =>
  api.get('/maps/search', {
    params: { q, limit, ...(near ? { lat: near.lat, lng: near.lng } : {}) }, timeout: 12000,
  }).then((r) => r.data.results)

// Live type-ahead: real suggestions for a partial name. `near` ({lat,lng}) only
// biases ranking; `signal` lets the caller cancel a superseded request.
export const suggestPlaces = (q, { near, signal, limit = 6 } = {}) =>
  api.get('/maps/suggest', {
    params: { q, limit, ...(near ? { lat: near.lat, lng: near.lng } : {}) }, timeout: 12000, signal,
  }).then((r) => r.data.results)

export const listTravelModes = () => api.get('/maps/modes').then((r) => r.data.modes)

export const getRoute = (from, to, mode = 'driving') =>
  api.get('/maps/route', {
    params: { olat: from.lat, olng: from.lng, dlat: to.lat, dlng: to.lng, mode }, timeout: 15000,
  }).then((r) => r.data)

export const getTrip = (points, mode = 'driving') =>
  api.post('/maps/trip', { points: points.map(({ lat, lng }) => ({ lat, lng })), mode }, { timeout: 40000 })
    .then((r) => r.data)

export const formatDuration = (min) => {
  if (min == null) return '—'
  const m = Math.round(min)
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const r = m % 60
  return r ? `${h} h ${r} min` : `${h} h`
}
