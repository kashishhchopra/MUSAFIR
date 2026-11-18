import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../../api'
import useTouristData from './useTouristData.js'

vi.mock('../../useWebSocket', () => ({ default: () => ({ connected: true }) }))

const mock = new MockAdapter(api)

const meResponse = {
  id: 1, digital_id: 'STS-001', full_name: 'Aarav', last_lat: 26.14, last_lng: 91.73,
  tracking_enabled: true, itinerary: [{ name: 'Start', lat: 26.14, lng: 91.73 }],
}
const scoreResponse = { score: 88, band: 'safe', breakdown: { zone: 'City Center', night_penalty: false, explanation: [] } }

function mockLoadEndpoints() {
  mock.onGet('/tourists/1').reply(200, meResponse)
  mock.onGet('/tourists/1/safety-score').reply(200, scoreResponse)
  mock.onGet('/zones').reply(200, [])
  mock.onGet('/police-units').reply(200, [])
  mock.onGet('/tourists/1/trajectory-forecast').reply(200, { points: [] })
  mock.onGet('/tourists/1/risk-forecast').reply(200, { forecast: [] })
}

beforeEach(() => {
  mock.reset()
  localStorage.clear()
})

describe('useTouristData', () => {
  it('is not ready until the profile and score both load', async () => {
    mockLoadEndpoints()
    const { result } = renderHook(() => useTouristData(1))
    expect(result.current.ready).toBe(false)
    await waitFor(() => expect(result.current.ready).toBe(true))
    expect(result.current.me.digital_id).toBe('STS-001')
    expect(result.current.score.score).toBe(88)
  })

  it('computes the nearest units sorted by distance', async () => {
    mockLoadEndpoints()
    mock.onGet('/police-units').reply(200, [
      { id: 1, name: 'Far', lat: 26.5, lng: 91.9, station: 'Far PS', phone: '100' },
      { id: 2, name: 'Near', lat: 26.141, lng: 91.731, station: 'Near PS', phone: '100' },
    ])
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))
    expect(result.current.nearby[0].name).toBe('Near')
  })

  it('flags the current zone as risky only for high/restricted risk levels', async () => {
    mockLoadEndpoints()
    mock.onGet('/zones').reply(200, [
      { id: 1, name: 'Danger Zone', risk_level: 'restricted', polygon: [[26.0, 91.0], [26.0, 92.0], [27.0, 92.0], [27.0, 91.0]] },
    ])
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))
    expect(result.current.riskyZone?.name).toBe('Danger Zone')
  })

  it('sendSOS posts the current position and message, then updates sosSent', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').reply(200, { nearest_unit: null, notified_contacts: [] })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    await act(async () => { await result.current.sendSOS() })

    expect(result.current.sosSent).toBeTruthy()
    expect(result.current.sosQueued).toBe(false)
    const [, body] = mock.history.post.filter((r) => r.url === '/tourists/1/sos').map((r) => [r.url, JSON.parse(r.data)]).at(-1)
    expect(body.lat).toBe(26.14)
    expect(body.lng).toBe(91.73)
  })

  it('queues the SOS locally when the request never reaches the server', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').networkError()
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    await act(async () => { await result.current.sendSOS() })

    expect(result.current.sosQueued).toBe(true)
    expect(result.current.pendingCount).toBeGreaterThan(0)
  })

  it('rethrows a genuine server error instead of silently queueing it', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').reply(500, { detail: 'server error' })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    await expect(act(async () => { await result.current.sendSOS() })).rejects.toBeTruthy()
    expect(result.current.sosQueued).toBe(false)
  })

  describe('SOS for a tourist whose first GPS fix has not been stored yet', () => {
    const noPos = { ...meResponse, last_lat: null, last_lng: null }
    const setGeo = (impl) => Object.defineProperty(navigator, 'geolocation', {
      value: { getCurrentPosition: impl, watchPosition: vi.fn(() => 1), clearWatch: vi.fn() }, configurable: true,
    })

    it('gets a real fix from the device and sends it (never null coordinates)', async () => {
      mockLoadEndpoints()
      mock.onGet('/tourists/1').reply(200, noPos)
      mock.onPost('/tourists/1/sos').reply(200, { nearest_unit: null, notified_contacts: [] })
      setGeo((ok) => ok({ coords: { latitude: 27.18, longitude: 78.01, accuracy: 9 }, timestamp: Date.now() }))
      const { result } = renderHook(() => useTouristData(1))
      await waitFor(() => expect(result.current.ready).toBe(true))
      await act(async () => { await result.current.sendSOS() })
      const body = JSON.parse(mock.history.post.filter((r) => r.url === '/tourists/1/sos').at(-1).data)
      expect(body.lat).toBe(27.18)
      expect(body.lng).toBe(78.01)
    })

    it('with no fix available it says so clearly and does not post an unusable SOS', async () => {
      mockLoadEndpoints()
      mock.onGet('/tourists/1').reply(200, noPos)
      mock.onPost('/tourists/1/sos').reply(200, {})
      setGeo((_ok, fail) => fail({ code: 1 }))
      const { result } = renderHook(() => useTouristData(1))
      await waitFor(() => expect(result.current.ready).toBe(true))
      let caught
      await act(async () => { await result.current.sendSOS().catch((e) => { caught = e }) })
      expect(caught.message).toBe('sos-no-location')
      expect(mock.history.post.filter((r) => r.url === '/tourists/1/sos')).toHaveLength(0)
      await waitFor(() => expect(result.current.toast).toMatch(/call 112/))
    })
  })

  it('tells the tourist when the server rejects the SOS', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').reply(500, { detail: 'boom' })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))
    let caught
    await act(async () => { await result.current.sendSOS().catch((e) => { caught = e }) })
    expect(caught).toBeTruthy()
    await waitFor(() => expect(result.current.toast).toMatch(/SOS could not be sent/))
  })

  it('toggleTracking flips state and informs the backend', async () => {
    mockLoadEndpoints()
    mock.onPost(/\/tourists\/1\/tracking/).reply(200, {})
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))
    expect(result.current.tracking).toBe(true)

    await act(async () => { await result.current.toggleTracking() })

    expect(result.current.tracking).toBe(false)
    expect(mock.history.post.some((r) => r.url.includes('enabled=false'))).toBe(true)
  })
})
