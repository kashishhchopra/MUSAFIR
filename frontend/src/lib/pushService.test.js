import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import MockAdapter from 'axios-mock-adapter'
import api, { bare } from '../api'
import { enablePush, disablePush, permissionState, pushSupported } from './pushService'

const mockApi = new MockAdapter(api)
const mockBare = new MockAdapter(bare)

function fakeBrowser({ permission = 'granted' } = {}) {
  const sub = {
    endpoint: 'https://push.example/abc',
    toJSON: () => ({ endpoint: 'https://push.example/abc', keys: { p256dh: 'P', auth: 'A' } }),
    unsubscribe: vi.fn(async () => true),
  }
  const pushManager = { getSubscription: vi.fn(async () => null), subscribe: vi.fn(async () => sub) }
  const reg = { pushManager }
  vi.stubGlobal('Notification', { permission, requestPermission: vi.fn(async () => permission) })
  vi.stubGlobal('PushManager', function PushManager() {})
  Object.defineProperty(navigator, 'serviceWorker', {
    configurable: true,
    value: { ready: Promise.resolve(reg), getRegistration: async () => reg },
  })
  return { sub, pushManager }
}

beforeEach(() => { mockApi.reset(); mockBare.reset() })
afterEach(() => { vi.unstubAllGlobals(); delete navigator.serviceWorker })

describe('pushService', () => {
  it('reports unsupported when the browser has no PushManager', () => {
    expect(pushSupported()).toBe(false)
    expect(permissionState()).toBe('unsupported')
  })

  it('subscribes with the server VAPID key and registers the subscription for a logged-in user', async () => {
    const { pushManager } = fakeBrowser()
    mockBare.onGet('/notifications/push/public-key').reply(200, { enabled: true, public_key: 'BEl6' })
    mockApi.onPost('/notifications/push/subscribe').reply(201, { id: 1 })
    await enablePush()
    const opts = pushManager.subscribe.mock.calls[0][0]
    expect(opts.userVisibleOnly).toBe(true)
    expect(opts.applicationServerKey).toBeInstanceOf(Uint8Array)
    const body = JSON.parse(mockApi.history.post[0].data)
    expect(body).toMatchObject({ endpoint: 'https://push.example/abc', keys: { p256dh: 'P', auth: 'A' } })
  })

  it('a guardian registers with only their share token, no login', async () => {
    fakeBrowser()
    mockBare.onGet('/notifications/push/public-key').reply(200, { enabled: true, public_key: 'BEl6' })
    mockBare.onPost('/notifications/push/guardian-subscribe').reply(201, { id: 2 })
    await enablePush({ guardianToken: 'tok123' })
    expect(JSON.parse(mockBare.history.post[0].data).token).toBe('tok123')
    expect(mockApi.history.post).toHaveLength(0)
  })

  it('refuses clearly when permission is denied, without contacting the server', async () => {
    fakeBrowser({ permission: 'denied' })
    await expect(enablePush()).rejects.toThrow(/blocked/i)
    expect(mockBare.history.get).toHaveLength(0)
  })

  it('refuses when the server has push disabled', async () => {
    fakeBrowser()
    mockBare.onGet('/notifications/push/public-key').reply(200, { enabled: false, public_key: null })
    await expect(enablePush()).rejects.toThrow(/not configured/i)
  })

  it('disable unsubscribes on the server and in the browser', async () => {
    const { sub, pushManager } = fakeBrowser()
    pushManager.getSubscription.mockResolvedValue(sub)
    mockApi.onPost('/notifications/push/unsubscribe').reply(200, { ok: true })
    await disablePush()
    expect(JSON.parse(mockApi.history.post[0].data).endpoint).toBe('https://push.example/abc')
    expect(sub.unsubscribe).toHaveBeenCalled()
  })
})
