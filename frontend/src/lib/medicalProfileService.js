import api from '../api'

// Thin wrappers over the Emergency Medical Profile API
// (backend/app/api/medical_profile.py). The server owns every rule -- who may
// read what, token validity, limits -- this file only fetches.
export const getMedicalOptions = () => api.get('/medical-profile/options').then((r) => r.data)
export const getMedicalProfile = (tid) => api.get(`/tourists/${tid}/medical-profile`).then((r) => r.data)
export const saveMedicalProfile = (tid, body) =>
  api.put(`/tourists/${tid}/medical-profile`, body).then((r) => r.data)
export const clearMedicalField = (tid, field) =>
  api.delete(`/tourists/${tid}/medical-profile/${field}`).then((r) => r.data)
export const getMedicalQr = (tid) => api.get(`/tourists/${tid}/medical-qr`).then((r) => r.data)
export const regenerateMedicalQr = (tid) =>
  api.post(`/tourists/${tid}/medical-qr/regenerate`).then((r) => r.data)
export const revokeMedicalQr = (tid) => api.post(`/tourists/${tid}/medical-qr/revoke`).then((r) => r.data)
export const getMedicalAccessHistory = (tid) =>
  api.get(`/tourists/${tid}/medical-access`).then((r) => r.data)
// Responder: scanned QR payload (or pasted token) -> medical profile.
export const accessMedicalProfile = (body) => api.post('/tourist-id/medical', body).then((r) => r.data)

export function errorText(e, fallback = 'Something went wrong. Please try again.') {
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace(/^Value error, /, '')
  return fallback
}

export const ACCESS_STATUS_TEXT = {
  invalid: 'This is not a valid MUSAFIR tourist QR code.',
  revoked: 'This QR code was revoked or replaced and is no longer valid.',
  expired: 'This QR code has expired.',
  no_profile: 'This tourist has not provided a medical profile.',
}

export function purposeLabel(p) {
  return String(p || '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())
}
