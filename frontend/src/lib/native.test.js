// Android-app code paths of the push service, token refresh and text-to-speech,
// with the Capacitor plugins replaced by in-memory fakes.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import MockAdapter from 'axios-mock-adapter'

const fakes = vi.hoisted(() => {
  const store = new Map()
  const listeners = {}
  return {
    store,
    listeners,
    perm: { receive: 'granted' },
    tts: { speak: null, stop: null },
  }
})

vi.mock('./platform', () => ({ isNativeApp: () => true }))

vi.mock('@capacitor/preferences', () => ({
  Preferences: {
    get: async ({ key }) => ({ value: fakes.store.has(key) ? fakes.store.get(key) : null }),
    set: async ({ key, value }) => { fakes.store.set(key, value) },
    remove: async ({ key }) => { fakes.store.delete(key) },
  },
}))

vi.mock('@capacitor/push-notifications', () => ({
  PushNotifications: {
    checkPermissions: vi.fn(async () => ({ receive: fakes.perm.receive })),
    requestPermissions: vi.fn(async () => ({ receive: fakes.perm.receive })),
    createChannel: vi.fn(async () => {}),
    unregister: vi.fn(async () => {}),
    register: vi.fn(async () => { fakes.listeners.registration?.({ value: 'fcm-token-123' }) }),
    addListener: vi.fn((name, fn) => {
      fakes.listeners[name] = fn
      return Promise.resolve({ remove: () => { delete fakes.listeners[name] } })
    }),
  },
}))

vi.mock('@capacitor-community/text-to-speech', () => ({
  TextToSpeech: {
    speak: (...a) => fakes.tts.speak(...a),
    stop: (...a) => fakes.tts.stop(...a),
  },
}))

const { default: api, bare, refreshAccessToken, getAccessToken } = await import('../api')
const push = await import('./pushService')
const { PushNotifications } = await import('@capacitor/push-notifications')
const { speak, speechSynthesisSupported } = await import('./voiceService')

const mockApi = new MockAdapter(api)
const mockBare = new MockAdapter(bare)

beforeEach(() => {
  mockApi.reset()
  mockBare.reset()
  fakes.store.clear()
  fakes.perm.receive = 'granted'
  vi.clearAllMocks()
})

describe('push in the Android app (FCM)', () => {
  it('registers the FCM token with the server and remembers it', async () => {
    mockApi.onPost('/notifications/push/subscribe-fcm').reply(201, { id: 1 })
    expect(push.pushSupported()).toBe(true)

    await push.enablePush()

    expect(JSON.parse(mockApi.history.post[0].data)).toMatchObject({ token: 'fcm-token-123' })
    expect(PushNotifications.createChannel).toHaveBeenCalledWith(expect.objectContaining({ id: 'sos_alerts' }))
    expect(fakes.store.get('fcm_token')).toBe('fcm-token-123')
    expect(await push.currentSubscription()).toBe('fcm-token-123')
    expect(push.permissionState()).toBe('granted')
  })

  it('refuses clearly when notifications are blocked', async () => {
    fakes.perm.receive = 'denied'
    await expect(push.enablePush()).rejects.toThrow(/Android settings/)
    expect(mockApi.history.post).toHaveLength(0)
    expect(push.permissionState()).toBe('denied')
  })

  it('unsubscribes with the token as the endpoint and forgets it', async () => {
    fakes.store.set('fcm_token', 'fcm-token-123')
    mockApi.onPost('/notifications/push/unsubscribe').reply(200, { ok: true })

    await push.disablePush()

    expect(JSON.parse(mockApi.history.post[0].data)).toEqual({ endpoint: 'fcm-token-123' })
    expect(PushNotifications.unregister).toHaveBeenCalled()
    expect(fakes.store.has('fcm_token')).toBe(false)
  })

  it('re-registers on start only when the phone was already subscribed', async () => {
    mockApi.onPost('/notifications/push/subscribe-fcm').reply(201, { id: 1 })
    await push.resyncNativePush()
    expect(PushNotifications.register).not.toHaveBeenCalled()

    fakes.store.set('fcm_token', 'old-token')
    await push.resyncNativePush()
    expect(fakes.store.get('fcm_token')).toBe('fcm-token-123')
    expect(PushNotifications.requestPermissions).not.toHaveBeenCalled()
  })

  it('opens the screen a tapped notification points at', async () => {
    const navigate = vi.fn()
    await push.onPushTap(navigate)
    fakes.listeners.pushNotificationActionPerformed({ notification: { data: { url: 'https://my-pc.ts.net/app?incident=7' } } })
    fakes.listeners.pushNotificationActionPerformed({ notification: { data: {} } })
    expect(navigate.mock.calls).toEqual([['/app?incident=7'], ['/app']])
  })
})

describe('token refresh in the Android app', () => {
  it('sends the stored refresh token in the body and stores the rotated one', async () => {
    fakes.store.set('refresh_token', 'r1')
    mockBare.onPost('/auth/refresh').reply(200, { access_token: 'a2', refresh_token: 'r2' })

    await refreshAccessToken()

    expect(JSON.parse(mockBare.history.post[0].data)).toEqual({ refresh_token: 'r1' })
    expect(fakes.store.get('refresh_token')).toBe('r2')
    expect(getAccessToken()).toBe('a2')
  })
})

describe('text-to-speech in the Android app', () => {
  it('speaks through the native engine with the right locale', async () => {
    fakes.tts.stop = vi.fn(async () => {})
    fakes.tts.speak = vi.fn(async () => {})
    expect(speechSynthesisSupported()).toBe(true)

    await speak('Namaste', 'hi')

    expect(fakes.tts.speak).toHaveBeenCalledWith({ text: 'Namaste', lang: 'hi-IN' })
  })

  it('never rejects even if the engine fails', async () => {
    fakes.tts.stop = vi.fn(async () => {})
    fakes.tts.speak = vi.fn(async () => { throw new Error('no engine') })
    await expect(speak('hello')).resolves.toBeUndefined()
  })
})
