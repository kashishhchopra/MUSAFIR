import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  CACHE_KEY, CACHE_MAX_AGE_MS, LOCATION_STATUS as S, getState, request, requestApprox,
  resetDeviceLocation, startDeviceLocation,
} from './deviceLocation'

const FIX = { lat: 28.6315, lng: 77.2167 }
const pos = (over = {}) => ({ coords: { latitude: FIX.lat, longitude: FIX.lng, accuracy: 25 }, timestamp: Date.now(), ...over })
let geo
const install = (impl) => {
  geo = { getCurrentPosition: vi.fn(impl) }
  Object.defineProperty(globalThis.navigator, 'geolocation', { value: geo, configurable: true })
}
const answers = (p = pos()) => (ok) => ok(p)
const fails = (code) => (_ok, fail) => fail({ code })
const hangs = () => () => {}
const flush = () => vi.advanceTimersByTimeAsync(0)

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  vi.setSystemTime(new Date('2026-11-18T12:00:00Z'))
  resetDeviceLocation()
  Object.defineProperty(globalThis.navigator, 'permissions', { value: undefined, configurable: true })
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('one request for the whole app', () => {
  it('concurrent callers share a single device request and all get the answer', async () => {
    install(answers())
    const [a, b, c] = await Promise.all([request(), request(), request()])
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1)
    expect(a).toMatchObject(FIX)
    expect(b).toBe(a)
    expect(c).toBe(a)
    expect(getState()).toMatchObject({ status: S.OK, source: 'gps', stale: false })
  })

  it('asks again later, once the first request is over', async () => {
    install(answers())
    await request()
    await request()
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(2)
  })

  it('uses the quick (Wi-Fi/cell) fix by default and a precise GPS fix when asked', async () => {
    install(answers())
    await request()
    expect(geo.getCurrentPosition.mock.calls[0][2]).toMatchObject({ enableHighAccuracy: false, timeout: 10_000 })
    await request({ precise: true })
    expect(geo.getCurrentPosition.mock.calls[1][2]).toMatchObject({ enableHighAccuracy: true, timeout: 20_000 })
  })

  it('says unsupported when the device has no geolocation', async () => {
    Object.defineProperty(globalThis.navigator, 'geolocation', { value: undefined, configurable: true })
    expect(await request()).toBeNull()
    expect(getState().status).toBe(S.UNSUPPORTED)
  })
})

describe('an instant start from the last known position', () => {
  const loaded = []
  afterEach(() => { loaded.forEach((m) => m.resetDeviceLocation()); loaded.length = 0 }) // drop the extra copies' listeners
  const fresh = async (cache) => {
    vi.resetModules()
    if (cache) localStorage.setItem(CACHE_KEY, JSON.stringify(cache))
    const m = await import('./deviceLocation')
    loaded.push(m)
    return m
  }

  it('a successful fix is remembered on the device', async () => {
    install(answers())
    await request()
    expect(JSON.parse(localStorage.getItem(CACHE_KEY))).toMatchObject(FIX)
  })

  it('after a reload the app has a position at once, marked stale, before the device has answered', async () => {
    const m = await fresh({ ...FIX, accuracy: 30, timestamp: Date.now() - 40 * 60_000 })
    expect(m.getState()).toMatchObject({ status: 'ok', stale: true, source: 'cache', position: FIX })
    install(hangs())
    m.startDeviceLocation()
    expect(m.getState()).toMatchObject({ status: 'ok', stale: true, refreshing: true }) // usable while refreshing
  })

  it('the live fix replaces the remembered one', async () => {
    const m = await fresh({ lat: 10, lng: 20, accuracy: 30, timestamp: Date.now() - 3600_000 })
    install(answers())
    m.startDeviceLocation()
    await flush()
    expect(m.getState()).toMatchObject({ status: 'ok', stale: false, source: 'gps', position: { lat: FIX.lat, lng: FIX.lng } })
  })

  it('a position older than six hours is not trusted', async () => {
    const m = await fresh({ ...FIX, accuracy: 30, timestamp: Date.now() - CACHE_MAX_AGE_MS - 1000 })
    expect(m.getState()).toMatchObject({ status: 'idle', position: null })
  })

  it('a corrupt remembered value is ignored', async () => {
    vi.resetModules()
    localStorage.setItem(CACHE_KEY, '{nope')
    const m = await import('./deviceLocation')
    loaded.push(m)
    expect(m.getState().position).toBeNull()
  })

  it('when the refresh fails, the remembered position is kept and still marked stale', async () => {
    const m = await fresh({ ...FIX, accuracy: 30, timestamp: Date.now() - 3600_000 })
    install(fails(2))
    await m.request()
    expect(m.getState()).toMatchObject({ status: 'ok', stale: true, position: FIX, error: 'unavailable' })
  })

  it('but a refusal of permission throws it away: the user said no', async () => {
    const m = await fresh({ ...FIX, accuracy: 30, timestamp: Date.now() - 3600_000 })
    install(fails(1))
    await m.request()
    expect(m.getState()).toMatchObject({ status: 'denied', position: null })
    expect(localStorage.getItem(CACHE_KEY)).toBeNull()
  })

  it('startDeviceLocation does not ask again when a fresh position is already held', async () => {
    install(answers())
    await request()
    startDeviceLocation()
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1)
  })
})

describe('failures with nothing to fall back on', () => {
  it.each([[1, S.DENIED], [2, S.UNAVAILABLE], [3, S.TIMEOUT]])('browser error %s -> %s', async (code, status) => {
    install(fails(code))
    expect(await request()).toBeNull()
    expect(getState()).toMatchObject({ status, position: null })
  })

  it('a request nobody answers (ignored permission prompt) gives up after 13 s instead of spinning forever', async () => {
    install(hangs())
    const p = request()
    expect(getState().status).toBe(S.LOCATING)
    await vi.advanceTimersByTimeAsync(12_900)
    expect(getState().status).toBe(S.LOCATING)
    await vi.advanceTimersByTimeAsync(200)
    expect(await p).toBeNull()
    expect(getState().status).toBe(S.TIMEOUT)
  })

  it('...yet an answer that arrives late (the user clicked Allow afterwards) is still accepted', async () => {
    let answer
    install((ok) => { answer = ok })
    request()
    await vi.advanceTimersByTimeAsync(13_500)
    expect(getState().status).toBe(S.TIMEOUT)
    answer(pos())
    expect(getState()).toMatchObject({ status: 'ok', source: 'gps', stale: false })
  })
})

describe('the browser permission', () => {
  const perm = (state) => {
    const p = { state, onchange: null }
    Object.defineProperty(globalThis.navigator, 'permissions', { value: { query: vi.fn().mockResolvedValue(p) }, configurable: true })
    return p
  }

  it('is exposed, so a slow wait can say "click Allow" or "your computer has not answered"', async () => {
    perm('prompt')
    install(hangs())
    startDeviceLocation()
    await flush()
    expect(getState().permission).toBe('prompt')
  })

  it('granting permission later fetches the position by itself', async () => {
    const p = perm('prompt')
    install(fails(1))
    startDeviceLocation()
    await flush()
    expect(getState().status).toBe(S.DENIED)
    install(answers())
    p.state = 'granted'
    p.onchange()
    await flush()
    expect(getState()).toMatchObject({ status: 'ok', source: 'gps' })
  })
})

describe('coming back to the app', () => {
  it('quietly refreshes a stale position when the tab becomes visible again', async () => {
    install(answers())
    startDeviceLocation()
    await flush()
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1)
    vi.setSystemTime(new Date('2026-11-18T12:10:00Z')) // ten minutes later
    document.dispatchEvent(new Event('visibilitychange'))
    await flush()
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(2)
  })
})

describe('approximate location (only when the user asks)', () => {
  const geojs = { latitude: '28.6542', longitude: '77.2373', accuracy: 500, city: 'Delhi', country: 'India' }
  const stubFetch = (impl) => vi.stubGlobal('fetch', vi.fn(impl))
  const ok = (j) => Promise.resolve({ ok: true, json: () => Promise.resolve(j) })

  it('gives a labelled city-level position, and never caches it as if it were a fix', async () => {
    stubFetch(() => ok(geojs))
    const p = await requestApprox()
    expect(p).toMatchObject({ lat: 28.6542, lng: 77.2373, place: 'Delhi, India' })
    expect(p.accuracy).toBeGreaterThanOrEqual(5000)
    expect(getState()).toMatchObject({ status: 'ok', source: 'ip', stale: false })
    expect(localStorage.getItem(CACHE_KEY)).toBeNull()
  })

  it('falls back to the second service, and reports failure honestly when both are down', async () => {
    stubFetch((url) => (url.includes('geojs') ? Promise.reject(new Error('down')) : ok({ success: true, latitude: 19.07, longitude: 72.87, city: 'Mumbai', country: 'India' })))
    expect(await requestApprox()).toMatchObject({ lat: 19.07, lng: 72.87 })
    resetDeviceLocation()
    stubFetch(() => Promise.reject(new Error('down')))
    expect(await requestApprox()).toBeNull()
    expect(getState()).toMatchObject({ position: null, error: 'approx_failed' })
  })

  it('rejects nonsense coordinates', async () => {
    stubFetch(() => ok({ latitude: 'abc', longitude: null }))
    expect(await requestApprox()).toBeNull()
  })

  it('never overrides a fresh device fix', async () => {
    install(answers())
    await request()
    stubFetch(() => { throw new Error('must not be called') })
    expect(await requestApprox()).toMatchObject(FIX)
    expect(getState().source).toBe('gps')
  })

  it('a later device fix replaces an approximation', async () => {
    stubFetch(() => ok(geojs))
    await requestApprox()
    install(answers())
    await request()
    expect(getState()).toMatchObject({ source: 'gps', position: { lat: FIX.lat } })
  })
})
