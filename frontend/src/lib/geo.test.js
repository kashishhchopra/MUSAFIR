import { beforeEach, describe, expect, it, vi } from 'vitest'

const { native, plugin } = vi.hoisted(() => ({
  native: { on: false },
  plugin: { addWatcher: vi.fn(), removeWatcher: vi.fn() },
}))
vi.mock('./native', () => ({ isNative: () => native.on }))
vi.mock('@capacitor/core', () => ({ registerPlugin: () => plugin }))

import { getCurrentPosition, watchPosition } from './geo'

beforeEach(() => { vi.clearAllMocks(); native.on = false })

describe('geo', () => {
  it('uses navigator.geolocation in the browser and stop() clears the watch', () => {
    const geo = { watchPosition: vi.fn(() => 7), clearWatch: vi.fn() }
    vi.stubGlobal('navigator', { geolocation: geo })
    const stop = watchPosition(() => {}, () => {})
    expect(geo.watchPosition).toHaveBeenCalled()
    stop()
    expect(geo.clearWatch).toHaveBeenCalledWith(7)
  })

  it('native: maps plugin locations to browser-shaped positions and removes the watcher', async () => {
    native.on = true
    let cb
    plugin.addWatcher.mockImplementation((_o, fn) => { cb = fn; return Promise.resolve('w1') })
    const onPos = vi.fn()
    const stop = watchPosition(onPos, vi.fn())
    await Promise.resolve()
    cb({ latitude: 26.1, longitude: 91.7, accuracy: 5, speed: 2, bearing: 90, time: 123 })
    expect(onPos).toHaveBeenCalledWith({ coords: { latitude: 26.1, longitude: 91.7, accuracy: 5, speed: 2, heading: 90 }, timestamp: 123 })
    stop()
    expect(plugin.removeWatcher).toHaveBeenCalledWith({ id: 'w1' })
  })

  it('native: NOT_AUTHORIZED becomes a PERMISSION_DENIED error', async () => {
    native.on = true
    plugin.addWatcher.mockImplementation((_o, fn) => { fn(null, { code: 'NOT_AUTHORIZED' }); return Promise.resolve('w2') })
    const onErr = vi.fn()
    getCurrentPosition(() => {}, onErr)
    await Promise.resolve()
    expect(onErr.mock.calls[0][0].code).toBe(1)
  })
})
