import api from '../api'

// Thin wrappers over the privacy API (backend/app/api/privacy.py). The server
// decides every rule -- ownership, re-authentication, what is retained -- this
// file only fetches. Nothing here caches or stores personal data.
export { errorText } from './medicalProfileService'

export const getNotice = () => api.get('/privacy/notice').then((r) => r.data)
export const getOverview = (tid) => api.get(`/tourists/${tid}/privacy/overview`).then((r) => r.data)

export const grantConsent = (tid, purpose) =>
  api.post(`/tourists/${tid}/consents`, { purpose }).then((r) => r.data)
export const withdrawConsent = (tid, purpose) =>
  api.post(`/tourists/${tid}/consents/${purpose}/withdraw`).then((r) => r.data)
export const getConsentHistory = (tid) => api.get(`/tourists/${tid}/consents/history`).then((r) => r.data)

export const getLocationHistory = (tid, params) =>
  api.get(`/tourists/${tid}/location-history`, { params }).then((r) => r.data)
export const deleteLocationHistory = (tid, body) =>
  api.post(`/tourists/${tid}/location-history/delete`, body).then((r) => r.data)

export const requestExport = (tid, body) => api.post(`/tourists/${tid}/data-export`, body).then((r) => r.data)
export const listExports = (tid) => api.get(`/tourists/${tid}/data-export`).then((r) => r.data)
export async function downloadExport(tid, exportId) {
  const res = await api.get(`/tourists/${tid}/data-export/${exportId}/download`, { responseType: 'blob' })
  const cd = res.headers?.['content-disposition'] || ''
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || `musafir-data-${exportId}`
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
  return name
}

export const getDeletionPreview = (tid) => api.get(`/tourists/${tid}/account-deletion/preview`).then((r) => r.data)
export const startDeletion = (tid, password) =>
  api.post(`/tourists/${tid}/account-deletion`, { password }).then((r) => r.data)
export const confirmDeletion = (tid, requestId, body) =>
  api.post(`/tourists/${tid}/account-deletion/${requestId}/confirm`, body).then((r) => r.data)
export const cancelDeletion = (tid, requestId) =>
  api.post(`/tourists/${tid}/account-deletion/${requestId}/cancel`).then((r) => r.data)
export const getDeletionStatus = (requestId) => api.get(`/privacy/deletion-status/${requestId}`).then((r) => r.data)

export const listGrievances = (tid) => api.get(`/tourists/${tid}/grievances`).then((r) => r.data)
export const getGrievance = (tid, gid) => api.get(`/tourists/${tid}/grievances/${gid}`).then((r) => r.data)
export const submitGrievance = (tid, body) => api.post(`/tourists/${tid}/grievances`, body).then((r) => r.data)
export const replyGrievance = (tid, gid, message) =>
  api.post(`/tourists/${tid}/grievances/${gid}/reply`, { message }).then((r) => r.data)

// ---- admin ----
export const adminPolicies = () => api.get('/admin/privacy/retention/policies').then((r) => r.data)
export const adminUpdatePolicy = (cat, body) =>
  api.patch(`/admin/privacy/retention/policies/${cat}`, body).then((r) => r.data)
export const adminExceptions = () => api.get('/admin/privacy/retention/exceptions').then((r) => r.data)
export const adminAddException = (body) => api.post('/admin/privacy/retention/exceptions', body).then((r) => r.data)
export const adminReleaseException = (id) =>
  api.post(`/admin/privacy/retention/exceptions/${id}/release`).then((r) => r.data)
export const adminJobs = () => api.get('/admin/privacy/retention/jobs').then((r) => r.data)
export const adminRunRetention = (category) =>
  api.post('/admin/privacy/retention/run', category ? { category } : {}).then((r) => r.data)
export const adminDeletionRequests = () => api.get('/admin/privacy/deletion-requests').then((r) => r.data)
export const adminProcessDeletions = () => api.post('/admin/privacy/deletion-requests/process').then((r) => r.data)
export const adminGrievances = (status) =>
  api.get('/admin/privacy/grievances', { params: status ? { status } : {} }).then((r) => r.data)
export const adminGrievance = (gid) => api.get(`/admin/privacy/grievances/${gid}`).then((r) => r.data)
export const adminRespond = (gid, body) =>
  api.post(`/admin/privacy/grievances/${gid}/respond`, body).then((r) => r.data)

export const DELETION_LABELS = {
  awaiting_confirmation: 'Deletion requested - awaiting your confirmation',
  requested: 'Deletion requested',
  in_progress: 'Deletion in progress',
  completed: 'Deletion completed',
  partially_retained: 'Partially retained under a documented exception',
  failed: 'Deletion failed - requires attention',
  cancelled: 'Deletion cancelled',
}
export const TERMINAL_DELETION = ['completed', 'partially_retained', 'failed', 'cancelled']
