import api from '../api'

// Thin wrappers over the Walk-Me-Home endpoints (backend/app/api/walk_home.py).
// No logic here: the server owns every deadline and the check-in/escalation race.

export const getWalkHomeConfig = () => api.get('/walk-home/config').then((r) => r.data)

export const getActiveJourney = (tid) =>
  api.get(`/tourists/${tid}/walk-home/active`).then((r) => r.data)

export const listJourneys = (tid) =>
  api.get(`/tourists/${tid}/walk-home`).then((r) => r.data)

export const startJourney = (tid, body) =>
  api.post(`/tourists/${tid}/walk-home`, body).then((r) => r.data)

export const checkInJourney = (tid, id) =>
  api.post(`/tourists/${tid}/walk-home/${id}/checkin`).then((r) => r.data)

export const shareJourneyLocation = (tid, id, body) =>
  api.post(`/tourists/${tid}/walk-home/${id}/location`, body).then((r) => r.data)

export const extendJourney = (tid, id, minutes) =>
  api.post(`/tourists/${tid}/walk-home/${id}/extend`, { minutes }).then((r) => r.data)

export const endJourney = (tid, id) =>
  api.post(`/tourists/${tid}/walk-home/${id}/end`).then((r) => r.data)

export const cancelJourney = (tid, id) =>
  api.post(`/tourists/${tid}/walk-home/${id}/cancel`).then((r) => r.data)

// Guardians reuse the existing Trip Guardian records (api/guardian.py).
export const listGuardians = (tid) =>
  api.get(`/tourists/${tid}/guardians`).then((r) => r.data)

export const addGuardian = (tid, body) =>
  api.post(`/tourists/${tid}/guardians`, body).then((r) => r.data)

// Existing map/geocode service (api/maps.py): {lat, lng} or nulls -- never a guess.
export const geocodePlace = (place) =>
  api.get('/maps/geocode', { params: { place } }).then((r) => r.data)

// Guardian side: public, token-scoped.
export const getGuardianJourney = (token) =>
  api.get(`/guardian/${token}/journey`).then((r) => r.data)

export const isNetworkError = (err) => !err?.response
export const errorText = (err, fallback) => err?.response?.data?.detail
  ? (typeof err.response.data.detail === 'string' ? err.response.data.detail : fallback)
  : fallback

// Seconds -> "m:ss" / "h:mm:ss".
export function clock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}
