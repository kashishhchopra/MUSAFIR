import api from '../api'

// Thin wrappers over the fall & crash detection endpoints
// (backend/app/api/fall_detection.py). No logic here -- the server owns the
// countdown clock and the cancel-vs-escalate race.

export const getFallConfig = () => api.get('/fall-detection/config').then((r) => r.data)

export const startFallEvent = (tid, body) =>
  api.post(`/tourists/${tid}/fall-events`, body).then((r) => r.data)

export const getActiveFallEvent = (tid) =>
  api.get(`/tourists/${tid}/fall-events/active`).then((r) => r.data)

export const listFallEvents = (tid) =>
  api.get(`/tourists/${tid}/fall-events`).then((r) => r.data)

export const cancelFallEvent = (tid, id) =>
  api.post(`/tourists/${tid}/fall-events/${id}/cancel`).then((r) => r.data)

export const confirmFallEvent = (tid, id) =>
  api.post(`/tourists/${tid}/fall-events/${id}/confirm`).then((r) => r.data)

export const sosFromFallEvent = (tid, id, body) =>
  api.post(`/tourists/${tid}/fall-events/${id}/sos`, body).then((r) => r.data)

// A request that never reached the server (offline, DNS, refused) is worth
// retrying; one the server answered (4xx/5xx) is a real outcome.
export const isNetworkError = (err) => !err?.response

// RFC4122-ish id for idempotency. crypto.randomUUID only exists in secure
// contexts, so fall back to a random string that satisfies the server's
// ^[A-Za-z0-9._:-]{8,64}$ pattern.
export function newClientEventId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `evt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}
