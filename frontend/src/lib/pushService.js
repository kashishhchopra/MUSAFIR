// Web Push registration for tourists, responders, admins (logged in) and
// guardians (share-link token, no account). Talks to /notifications/push/*.
import api, { bare } from '../api'
import { PushNotifications } from '@capacitor/push-notifications'
import { isNative } from './native'

// ---- iOS app (Capacitor): native APNs token instead of a browser subscription.
// The device token is registered with the server as endpoint "apns:<token>".
const TOKEN_KEY = 'musafir_apns_token'
const readToken = () => { try { return localStorage.getItem(TOKEN_KEY) } catch { return null } }
const writeToken = (t) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* private mode */ } }

function nativeToken() {
  return new Promise((resolve, reject) => {
    let regL, errL
    const done = (fn, v) => { regL?.then((h) => h.remove()); errL?.then((h) => h.remove()); fn(v) }
    regL = PushNotifications.addListener('registration', (t) => done(resolve, t.value))
    errL = PushNotifications.addListener('registrationError', (e) => done(reject, new Error(e?.error || 'Could not register for push notifications.')))
    PushNotifications.register().catch((e) => done(reject, e))
  })
}

/** Open the app at the notification's deep link (call once at startup, native only). */
export function initNativePushRouting(navigate) {
  if (!isNative()) return () => {}
  const h = PushNotifications.addListener('pushNotificationActionPerformed', (a) => {
    const url = a?.notification?.data?.url
    if (url && url.startsWith('/')) navigate(url)
  })
  return () => { h.then((x) => x.remove()) }
}

async function enableNative({ guardianToken } = {}) {
  const perm = await PushNotifications.requestPermissions()
  if (perm.receive !== 'granted') {
    throw new Error('Notifications are blocked. Allow them in Settings → MUSAFIR → Notifications, then try again.')
  }
  const token = await nativeToken()
  const body = { endpoint: `apns:${token}`, keys: { p256dh: 'apns', auth: 'apns' }, user_agent: 'MUSAFIR-iOS' }
  if (guardianToken) await bare.post('/notifications/push/guardian-subscribe', { ...body, token: guardianToken })
  else await api.post('/notifications/push/subscribe', body)
  writeToken(token)
  return { endpoint: body.endpoint }
}

export const pushSupported = () =>
  isNative() ||
  typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window

export const permissionState = () => {
  if (isNative()) return readToken() ? 'granted' : 'default' // real state is asked via requestPermissions()
  return pushSupported() ? Notification.permission : 'unsupported'
}

function urlB64ToUint8Array(b64) {
  const pad = '='.repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)))
}

// `ready` never resolves when no service worker is registered (e.g. `vite dev`).
async function registration() {
  const reg = await Promise.race([
    navigator.serviceWorker.ready,
    new Promise((resolve) => setTimeout(() => resolve(null), 4000)),
  ])
  if (!reg) throw new Error('The service worker is not running (it is only active in the built app — see README).')
  return reg
}

export async function currentSubscription() {
  if (isNative()) { const t = readToken(); return t ? { endpoint: `apns:${t}` } : null }
  if (!pushSupported()) return null
  const reg = await navigator.serviceWorker.getRegistration()
  return reg ? reg.pushManager.getSubscription() : null
}

/** Ask permission, subscribe this browser, and register it with the server. */
export async function enablePush({ guardianToken } = {}) {
  if (isNative()) return enableNative({ guardianToken })
  if (!pushSupported()) throw new Error('This browser does not support push notifications.')
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error('Notifications are blocked. Allow them in your browser’s site settings, then try again.')
  }
  const { data: key } = await bare.get('/notifications/push/public-key')
  if (!key.enabled) throw new Error('Push notifications are not configured on this server.')
  const reg = await registration()
  let sub = await reg.pushManager.getSubscription()
  if (!sub) {
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(key.public_key) })
  }
  const json = sub.toJSON()
  const body = { endpoint: json.endpoint, keys: json.keys, user_agent: navigator.userAgent.slice(0, 200) }
  if (guardianToken) await bare.post('/notifications/push/guardian-subscribe', { ...body, token: guardianToken })
  else await api.post('/notifications/push/subscribe', body)
  return sub
}

export async function disablePush({ guardianToken } = {}) {
  if (isNative()) {
    const t = readToken()
    if (!t) return
    const endpoint = `apns:${t}`
    try {
      if (guardianToken) await bare.post('/notifications/push/guardian-unsubscribe', { endpoint, token: guardianToken })
      else await api.post('/notifications/push/unsubscribe', { endpoint })
    } finally { writeToken(null) }
    return
  }
  const sub = await currentSubscription()
  if (!sub) return
  const endpoint = sub.endpoint
  try {
    if (guardianToken) await bare.post('/notifications/push/guardian-unsubscribe', { endpoint, token: guardianToken })
    else await api.post('/notifications/push/unsubscribe', { endpoint })
  } finally {
    await sub.unsubscribe()
  }
}
