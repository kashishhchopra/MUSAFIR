import axios from 'axios'
import { API_BASE } from './config'
import { isNativeApp } from './lib/platform'
import { clearRefreshToken, loadRefreshToken, saveRefreshToken } from './lib/nativeSession'

// The access token lives in memory only, never localStorage -- an XSS bug
// can no longer walk out with a long-lived credential. It's lost on a hard
// reload by design; auth.jsx's AuthProvider re-derives it on mount via a
// silent refresh against the httpOnly refresh-token cookie (see below).
let accessToken = null
export function setAccessToken(token) { accessToken = token }
export function getAccessToken() { return accessToken }

// withCredentials so the httpOnly refresh-token cookie (set by the backend
// on login/refresh, scoped to /api/auth) actually gets sent.
const api = axios.create({ baseURL: API_BASE, withCredentials: true })

api.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})

// A plain axios instance for the refresh call itself -- it must NOT go
// through the interceptors above (that would recurse: a 401 on /auth/refresh
// would trigger another refresh attempt). Exported so tests can mock it
// independently of the main `api` instance.
export const bare = axios.create({ baseURL: API_BASE, withCredentials: true })

function clearSessionAndRedirect() {
  setAccessToken(null)
  localStorage.removeItem('user')
  if (isNativeApp()) clearRefreshToken().catch(() => {})
  if (!location.pathname.startsWith('/login')) location.href = '/login'
}

// Concurrent requests that all 401 at once must trigger exactly one refresh
// call, not one per request -- they share this in-flight promise.
let refreshPromise = null

export function refreshAccessToken() {
  if (!refreshPromise) {
    // Browser: no body, the refresh token travels as the httpOnly cookie, not
    // JS-readable state. Android app: the cookie can't reach it (see
    // lib/nativeSession.js), so it sends its stored token in the body and
    // saves the rotated one the server hands back.
    refreshPromise = (async () => {
      const native = isNativeApp()
      const body = native ? { refresh_token: await loadRefreshToken() } : {}
      const { data } = await bare.post('/auth/refresh', body)
      if (native) await saveRefreshToken(data.refresh_token)
      setAccessToken(data.access_token)
      return data.access_token
    })().finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const original = err.config
    const isAuthEndpoint = original?.url?.startsWith('/auth/')

    if (err.response?.status === 401 && !isAuthEndpoint && !original._retried) {
      original._retried = true
      try {
        const newAccessToken = await refreshAccessToken()
        original.headers.Authorization = `Bearer ${newAccessToken}`
        return api(original)
      } catch {
        clearSessionAndRedirect()
        return Promise.reject(err)
      }
    }

    if (err.response?.status === 401) {
      clearSessionAndRedirect()
    }
    return Promise.reject(err)
  }
)

export default api
