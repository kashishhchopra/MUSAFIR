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

export const updateGuardian = (tid, id, body) =>
  api.patch(`/tourists/${tid}/guardians/${id}`, body).then((r) => r.data)

export const removeGuardian = (tid, id) =>
  api.post(`/tourists/${tid}/guardians/${id}/revoke`).then((r) => r.data)

// Existing map/geocode service (api/maps.py): {lat, lng} or nulls -- never a guess.
export const geocodePlace = (place) =>
  api.get('/maps/geocode', { params: { place } }).then((r) => r.data)

// Guardian side: public, token-scoped.
export const getGuardianJourney = (token) =>
  api.get(`/guardian/${token}/journey`).then((r) => r.data)

export const isNetworkError = (err) => !err?.response
// The server's own words: a plain string, or the first validation message
// ("Value error, Enter a valid phone number" -> "Enter a valid phone number").
export const errorText = (err, fallback) => {
  const d = err?.response?.data?.detail
  if (typeof d === 'string') return d
  const first = Array.isArray(d) ? d[0]?.msg : null
  return typeof first === 'string' ? first.replace(/^Value error,\s*/, '') : fallback
}

// A journey's route (geometry, mode, ETA) is not stored server-side, so keep it
// on this device to draw the map, measure progress and spot a wrong turn.
const planKey = (id) => `walkhome.plan.${id}`
export function savePlan(id, plan) {
  try { localStorage.setItem(planKey(id), JSON.stringify(plan)) } catch { /* private mode: plan is optional */ }
}
export function loadPlan(id) {
  try { return JSON.parse(localStorage.getItem(planKey(id)) || 'null') } catch { return null }
}
export function clearPlan(id) {
  try { localStorage.removeItem(planKey(id)) } catch { /* ignore */ }
}

// Seconds -> "m:ss" / "h:mm:ss".
export function clock(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}
