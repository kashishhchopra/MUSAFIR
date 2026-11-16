import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup, act } from '@testing-library/react'
import useGeolocation from './useGeolocation'

function installFakeGeolocation() {
  const watchers = {}
  let nextId = 1
  const fake = {
    watchPosition: vi.fn((onSuccess, onError) => {
      const id = nextId++
      watchers[id] = { onSuccess, onError }
      return id
    }),
    clearWatch: vi.fn((id) => { delete watchers[id] }),
  }
  Object.defineProperty(navigator, 'geolocation', { value: fake, configurable: true })
  return { fake, watchers }
}

describe('useGeolocation', () => {
  afterEach(() => {
    // Unmount every hook FIRST (so cleanup effects still see a real
    // navigator.geolocation to call clearWatch on), then remove the fake.
    cleanup()
    delete navigator.geolocation
    delete navigator.permissions
  })

  it('reports unsupported when navigator.geolocation is absent', () => {
    delete navigator.geolocation
    const { result } = renderHook(() => useGeolocation())
    expect(result.current.permissionState).toBe('unsupported')
  })

  it('does not start watching when disabled', () => {
    const { fake } = installFakeGeolocation()
    renderHook(() => useGeolocation({ enabled: false }))
    expect(fake.watchPosition).not.toHaveBeenCalled()
  })

  it('converts a successful position into lat/lng/speedKmh', async () => {
    const { watchers } = installFakeGeolocation()
    const { result } = renderHook(() => useGeolocation({ enabled: true }))

    watchers[1].onSuccess({
      coords: { latitude: 26.15, longitude: 91.74, accuracy: 12, speed: 2 }, // 2 m/s
      timestamp: 1700000000000,
    })

    await waitFor(() => expect(result.current.position).not.toBeNull())
    expect(result.current.position.lat).toBe(26.15)
    expect(result.current.position.lng).toBe(91.74)
    expect(result.current.position.speedKmh).toBeCloseTo(7.2, 1) // 2 m/s * 3.6
    expect(result.current.permissionState).toBe('granted')
  })

  it('treats a null GPS speed as stationary rather than NaN', async () => {
    const { watchers } = installFakeGeolocation()
    const { result } = renderHook(() => useGeolocation({ enabled: true }))
    watchers[1].onSuccess({
      coords: { latitude: 26.15, longitude: 91.74, accuracy: 12, speed: null },
      timestamp: 1700000000000,
    })
    await waitFor(() => expect(result.current.position).not.toBeNull())
    expect(result.current.position.speedKmh).toBe(0)
  })

  it('surfaces a permission-denied error', async () => {
    const { watchers } = installFakeGeolocation()
    const { result } = renderHook(() => useGeolocation({ enabled: true }))
    watchers[1].onError({ code: 1, PERMISSION_DENIED: 1, message: 'User denied Geolocation' })
    await waitFor(() => expect(result.current.permissionState).toBe('denied'))
    expect(result.current.error).toBe('User denied Geolocation')
  })

  it('clears the watch on unmount', () => {
    const { fake } = installFakeGeolocation()
    const { unmount } = renderHook(() => useGeolocation({ enabled: true }))
    unmount()
    expect(fake.clearWatch).toHaveBeenCalledWith(1)
  })

  describe('gating', () => {
    // ~0.0001 deg latitude is ~11 m; 0.0003 deg is ~33 m.
    const fix = (lat) => ({ coords: { latitude: lat, longitude: 91.74, accuracy: 5, speed: 0 }, timestamp: 0 })
    const opts = { enabled: true, minDistanceM: 25, minIntervalMs: 15000, heartbeatMs: 120000 }

    afterEach(() => vi.useRealTimers())

    it('ignores small jitter and emits a 25 m move', () => {
      vi.useFakeTimers()
      const { watchers } = installFakeGeolocation()
      const { result } = renderHook(() => useGeolocation(opts))
      act(() => watchers[1].onSuccess(fix(26.15)))
      const first = result.current.position
      expect(first.lat).toBe(26.15)

      act(() => { vi.advanceTimersByTime(20000); watchers[1].onSuccess(fix(26.1501)) })
      expect(result.current.position).toBe(first) // ~11 m: not emitted

      act(() => watchers[1].onSuccess(fix(26.1503)))
      expect(result.current.position.lat).toBe(26.1503) // ~33 m: emitted
    })

    it('emits a move made inside the interval once the interval ends, instead of dropping it', () => {
      vi.useFakeTimers()
      const { watchers } = installFakeGeolocation()
      const { result } = renderHook(() => useGeolocation(opts))
      act(() => watchers[1].onSuccess(fix(26.15)))
      act(() => { vi.advanceTimersByTime(3000); watchers[1].onSuccess(fix(26.1503)) })
      expect(result.current.position.lat).toBe(26.15) // too soon

      act(() => vi.advanceTimersByTime(15000)) // no new fix arrives
      expect(result.current.position.lat).toBe(26.1503)
    })

    it('re-emits the current position as a heartbeat while stationary', () => {
      vi.useFakeTimers()
      const { watchers } = installFakeGeolocation()
      const { result } = renderHook(() => useGeolocation(opts))
      act(() => watchers[1].onSuccess(fix(26.15)))
      const first = result.current.position

      act(() => vi.advanceTimersByTime(60000))
      expect(result.current.position).toBe(first)

      act(() => vi.advanceTimersByTime(65000))
      expect(result.current.position).not.toBe(first)
      expect(result.current.position.lat).toBe(26.15)
    })

    it('has no heartbeat unless asked for one', () => {
      vi.useFakeTimers()
      const { watchers } = installFakeGeolocation()
      const { result } = renderHook(() => useGeolocation({ enabled: true }))
      act(() => watchers[1].onSuccess(fix(26.15)))
      const first = result.current.position
      act(() => vi.advanceTimersByTime(600000))
      expect(result.current.position).toBe(first)
    })
  })

  it('clears the watch when enabled flips to false', () => {
    const { fake } = installFakeGeolocation()
    const { rerender } = renderHook(({ enabled }) => useGeolocation({ enabled }), {
      initialProps: { enabled: true },
    })
    expect(fake.watchPosition).toHaveBeenCalledTimes(1)
    rerender({ enabled: false })
    expect(fake.clearWatch).toHaveBeenCalledWith(1)
  })
})
