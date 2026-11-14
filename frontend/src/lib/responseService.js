import api from '../api'

// Thin wrappers over the response-management API (backend/app/api/response.py).
// The SERVER owns every target, deadline and stage; this file only fetches and
// formats them.
export const getResponseConfig = () => api.get('/response/config').then((r) => r.data)
export const updateResponseConfig = (body) => api.put('/response/config', body).then((r) => r.data)
export const getBoard = () => api.get('/response/board').then((r) => r.data)
export const getSlo = (params) => api.get('/response/slo', { params }).then((r) => r.data)
export const getIncidentResponse = (id) => api.get(`/incidents/${id}/response`).then((r) => r.data)
export const acknowledgeIncident = (id) => api.post(`/incidents/${id}/acknowledge`).then((r) => r.data)
export const overrideSeverity = (id, severity, reason) =>
  api.post(`/incidents/${id}/severity`, { severity, reason }).then((r) => r.data)
export const listUnits = () => api.get('/police-units').then((r) => r.data)

export function errorText(e, fallback = 'Something went wrong') {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d) && d[0]?.msg) return d[0].msg
  return fallback
}

// 75 -> "01:15", 3725 -> "1:02:05". Always non-negative; the caller shows overdue separately.
export function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(Math.abs(totalSeconds ?? 0)))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(sec).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export const localTime = (iso) => (iso ? new Date(iso).toLocaleString() : '—')
