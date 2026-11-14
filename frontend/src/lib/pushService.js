// Push registration. In a browser: Web Push for tourists, responders, admins
// (logged in) and guardians (share-link token, no account). In the Android
// app: Firebase Cloud Messaging via @capacitor/push-notifications.
// Talks to /notifications/push/*.
import { PushNotifications } from '@capacitor/push-notifications'
import { Preferences } from '@capacitor/preferences'
import api, { bare } from '../api'
import { isNativeApp } from './platform'

const FCM_TOKEN_KEY = 'fcm_token'
const CHANNEL_ID = 'sos_alerts' // must match FCM_CHANNEL_ID in backend services/notifications.py
let nativePermission = 'prompt'

export const pushSupported = () =>
  isNativeApp() ||
  (typeof window !== 'undefined' && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window)

export const permissionState = () => {
  if (isNativeApp()) return nativePermission
  return pushSupported() ? Notification.permission : 'unsupported'
}

// ------------------------------------------------------------ Android app (FCM)
const normalizePermission = (p) => (p === 'prompt-with-rationale' ? 'prompt' : p)

async function nativeToken() {
  const { value } = await Preferences.get({ key: FCM_TOKEN_KEY })
  return value
}

async function refreshNativePermission() {
  const { receive } = await PushNotifications.checkPermissions()
  nativePermission = normalizePermission(receive)
  return nativePermission
}

function registerForToken() {
  return new Promise((resolve, reject) => {
    const handles = []
    const finish = (fn, value) => {
      clearTimeout(timer)
      handles.forEach((h) => h.then((x) => x.remove()))
      fn(value)
    }
    const timer = setTimeout(
      () => finish(reject, new Error('No push token from Google. Check the phone has internet and Google Play services.')),
      15000,
    )
    handles.push(PushNotifications.addListener('registration', ({ value }) => finish(resolve, value)))
    handles.push(PushNotifications.addListener('registrationError', (e) =>
      finish(reject, new Error(e?.error || 'Push registration failed.'))))
    PushNotifications.register().catch((e) => finish(reject, e))
  })
}

async function enableNativePush() {
  if ((await refreshNativePermission()) !== 'granted') {
    const { receive } = await PushNotifications.requestPermissions()
    nativePermission = normalizePermission(receive)
    if (receive !== 'granted') {
      throw new Error('Notifications are blocked. Allow them for MUSAFIR in Android settings, then try again.')
    }
  }
  await PushNotifications.createChannel({
    id: CHANNEL_ID, name: 'Emergency alerts', description: 'SOS and incident updates',
    importance: 5, visibility: 1, vibration: true,
  })
  const token = await registerForToken()
  await api.post('/notifications/push/subscribe-fcm', { token, user_agent: 'MUSAFIR Android app' })
  await Preferences.set({ key: FCM_TOKEN_KEY, value: token })
  return token
}

async function disableNativePush() {
  const token = await nativeToken()
  if (!token) return
  try {
    await api.post('/notifications/push/unsubscribe', { endpoint: token })
  } finally {
    await PushNotifications.unregister().catch(() => {})
    await Preferences.remove({ key: FCM_TOKEN_KEY })
  }
}

/** On app start: re-register so a rotated FCM token reaches the server. No prompt. */
export async function resyncNativePush() {
  try {
    if (!(await nativeToken())) return
    if ((await refreshNativePermission()) !== 'granted') return
    await enableNativePush()
  } catch {
    // best effort; the toggle in the Me tab shows the real state
  }
}

/** Open the screen a tapped notification points at. Returns the listener handle promise. */
export function onPushTap(navigate) {
  return PushNotifications.addListener('pushNotificationActionPerformed', ({ notification }) => {
    const url = notification?.data?.url
    if (!url) return navigate('/app')
    const target = new URL(url, window.location.origin)
    navigate(target.pathname + target.search)
  })
}

// ------------------------------------------------------------ browser (Web Push)

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
  if (isNativeApp()) return (await refreshNativePermission()) === 'granted' ? nativeToken() : null
  if (!pushSupported()) return null
  const reg = await navigator.serviceWorker.getRegistration()
  return reg ? reg.pushManager.getSubscription() : null
}

/** Ask permission, subscribe this browser, and register it with the server. */
export async function enablePush({ guardianToken } = {}) {
  if (isNativeApp()) return enableNativePush()
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
  if (isNativeApp()) return disableNativePush()
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
