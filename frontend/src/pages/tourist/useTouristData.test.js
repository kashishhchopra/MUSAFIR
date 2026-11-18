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

  const sosBodies = () => mock.history.post.filter((r) => r.url === '/tourists/1/sos').map((r) => JSON.parse(r.data))

  it('keeps the SOS and retries when the server errors (5xx) -- a repeat is safe, a lost SOS is not', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').reply(504, { detail: 'gateway timeout' })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    let out
    await act(async () => { out = await result.current.sendSOS() })

    expect(out).toEqual({ queued: true, reason: 'server' })
    expect(result.current.sosQueued).toBe(true)
    expect(result.current.sosQueuedReason).toBe('server')
    expect(result.current.sosFailed).toBe(false)
    expect(result.current.pendingCount).toBeGreaterThan(0)
  })

  it('tells the tourist when the server refuses the SOS (4xx) instead of failing silently', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').reply(422, { detail: [{ msg: 'bad' }] })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    let out
    await act(async () => { out = await result.current.sendSOS() }) // must not throw

    expect(out).toEqual({ failed: true, status: 422 })
    expect(result.current.sosFailed).toBe(true)
    expect(result.current.sosQueued).toBe(false)
    expect(result.current.pendingCount).toBe(0)
  })

  it('treats a rate limit (429) like an outage: keep and retry', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').reply(429, { detail: 'slow down' })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    await act(async () => { await result.current.sendSOS() })
    expect(result.current.sosQueued).toBe(true)
  })

  it('a second tap while one SOS is in flight shares it instead of sending a duplicate', async () => {
    mockLoadEndpoints()
    let release
    mock.onPost('/tourists/1/sos').reply(() => new Promise((resolve) => {
      release = () => resolve([200, { nearest_unit: null, notified_contacts: [] }])
    }))
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    let first, second
    await act(async () => {
      first = result.current.sendSOS()
      second = result.current.sendSOS()
      await waitFor(() => expect(release).toBeTypeOf('function'))
      release()
      await Promise.all([first, second])
    })

    expect(first).toBe(second)
    expect(sosBodies()).toHaveLength(1)
    expect(result.current.sosSending).toBe(false)
  })

  it('never sends an invented position: with no fix at all the SOS goes without coordinates', async () => {
    mock.onGet('/tourists/1').reply(200, { ...meResponse, last_lat: null, last_lng: null })
    mock.onGet('/tourists/1/safety-score').reply(200, scoreResponse)
    mock.onGet('/zones').reply(200, [])
    mock.onGet('/police-units').reply(200, [])
    mock.onGet('/tourists/1/trajectory-forecast').reply(200, { points: [] })
    mock.onGet('/tourists/1/risk-forecast').reply(200, { forecast: [] })
    mock.onPost('/tourists/1/sos').reply(200, { nearest_unit: null, notified_contacts: [] })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    await act(async () => { await result.current.sendSOS() })

    const [body] = sosBodies()
    expect(body).not.toHaveProperty('lat')
    expect(body).not.toHaveProperty('lng')
    expect(result.current.sosSent).toBeTruthy()
  })

  it('truncates a note over the server limit so it cannot make the SOS fail', async () => {
    mockLoadEndpoints()
    mock.onPost('/tourists/1/sos').reply(200, { nearest_unit: null, notified_contacts: [] })
    const { result } = renderHook(() => useTouristData(1))
    await waitFor(() => expect(result.current.ready).toBe(true))

    act(() => { result.current.setEmergencyMessage('x'.repeat(900)) })
    await act(async () => { await result.current.sendSOS() })

    expect(sosBodies()[0].message).toHaveLength(500)
    expect(result.current.sosSent).toBeTruthy()
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
