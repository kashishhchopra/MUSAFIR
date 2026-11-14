import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { useRef } from 'react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import i18n from '../i18n'
import useFallDetection from '../hooks/useFallDetection'
import FallCountdownModal from './FallCountdownModal'
import FallDetectionCard, { FallStatusPill } from './FallDetectionCard'

// The live socket is irrelevant here (covered by the backend tests); stub it so
// the hook mounts without a WebSocket.
vi.mock('../useWebSocket', () => ({ default: () => ({ connected: false }) }))

const mock = new MockAdapter(api)

const CONFIG = {
  enabled: true, simulation_enabled: true, countdown_seconds: 30, cooldown_seconds: 60,
  history_limit: 20,
  thresholds: {
    freefall_g: 0.4, freefall_min_ms: 80, impact_g: 2.5, freefall_impact_window_ms: 600,
    crash_impact_g: 4, crash_min_speed_kmh: 20, stillness_dev_g: 0.15, stillness_gyro_dps: 40,
    stillness_ms: 2000, post_impact_observe_ms: 8000,
  },
}
const rec = (status, over = {}) => ({
  id: 5, tourist_id: 1, event_type: 'fall', source: 'simulation', simulated: true, status,
  detected_at: '2026-11-14T10:00:00Z', countdown_deadline: '2026-11-14T10:00:30Z',
  has_location: true, incident_id: null, seconds_remaining: status === 'pending' ? 30 : 0,
  deduplicated: false, ...over,
})

function Harness({ onSos = () => {}, label = '' }) {
  const posRef = useRef([26.14, 91.73])
  const fall = useFallDetection({ tid: 1, posRef, geo: null, onSosTriggered: onSos })
  return (
    <>
      <span data-testid="label">{label}</span>
      <FallStatusPill fall={fall} />
      <FallDetectionCard fall={fall} />
      <FallCountdownModal fall={fall} />
    </>
  )
}

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const body = (call) => JSON.parse(call.data)

async function mount(props) {
  const utils = render(<Harness {...props} />)
  await flush()
  await flush()
  return utils
}
const simulateFall = async () => {
  fireEvent.click(screen.getByText('Simulate a fall'))
  await flush()
}

beforeAll(() => i18n.changeLanguage('en'))

beforeEach(() => {
  mock.reset()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'] })
  mock.onGet('/fall-detection/config').reply(200, CONFIG)
  mock.onGet('/tourists/1/fall-events/active').reply(200, null)
  mock.onGet('/tourists/1/fall-events').reply(200, [])
})
afterEach(() => vi.useRealTimers())

describe('Fall & crash detection UI', () => {
  it('shows monitoring off, the privacy explanation and the no-guarantee disclaimer', async () => {
    await mount()
    expect(screen.getByTestId('fall-status')).toHaveTextContent('Monitoring off')
    expect(screen.getByText(/Raw sensor readings never leave your phone/)).toBeInTheDocument()
    expect(screen.getByText(/cannot detect every fall or crash/)).toBeInTheDocument()
    expect(screen.queryByTestId('fall-pill')).toBeNull() // nothing is being monitored yet
  })

  it('says so plainly when the device has no motion sensor (no pretending)', async () => {
    await mount()
    fireEvent.click(screen.getByText('Turn on motion monitoring'))
    await flush()
    expect(screen.getByTestId('fall-status')).toHaveTextContent('No motion sensor on this device')
    expect(screen.queryByTestId('fall-pill')).toBeNull()
  })

  it('a simulated fall opens a labelled 30 s countdown and posts it with the real position', async () => {
    mock.onPost('/tourists/1/fall-events').reply(201, rec('pending'))
    await mount()
    await simulateFall()
    const modal = screen.getByTestId('fall-modal')
    expect(modal).toHaveAttribute('data-phase', 'countdown')
    expect(screen.getByTestId('fall-sim-badge')).toHaveTextContent('SIMULATION')
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('30')
    expect(screen.getByText(/I AM SAFE/)).toBeInTheDocument()
    expect(screen.getByText(/SEND SOS NOW/)).toBeInTheDocument()
    const sent = body(mock.history.post[0])
    expect(sent).toMatchObject({ event_type: 'fall', source: 'simulation', lat: 26.14, lng: 91.73 })
    expect(sent.client_event_id).toMatch(/^[A-Za-z0-9._:-]{8,64}$/)
    expect(sent.seconds_since_detection).toBeGreaterThanOrEqual(0)
    expect(mock.history.post).toHaveLength(1) // no incident / SOS call yet
  })

  it('a simulated crash starts the same countdown', async () => {
    mock.onPost('/tourists/1/fall-events').reply(201, rec('pending', { event_type: 'crash' }))
    await mount()
    fireEvent.click(screen.getByText('Simulate a crash'))
    await flush()
    expect(screen.getByText('Possible crash detected')).toBeInTheDocument()
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('30')
  })

  it('counts down in real time and keeps counting through unrelated re-renders', async () => {
    mock.onPost('/tourists/1/fall-events').reply(201, rec('pending'))
    const { rerender } = await mount({ label: 'a' })
    await simulateFall()
    await advance(10_000)
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('20')
    rerender(<Harness label="b" />) // a parent re-render must not restart or skip the timer
    await flush()
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('20')
    await advance(5_000)
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('15')
  })

  it('"I AM SAFE" cancels on the server, never confirms, and the countdown never expires', async () => {
    mock.onPost('/tourists/1/fall-events').reply(201, rec('pending'))
    mock.onPost('/tourists/1/fall-events/5/cancel').reply(200, rec('cancelled'))
    await mount()
    await simulateFall()
    await advance(5_000)
    fireEvent.click(screen.getByText(/I AM SAFE/))
    await flush()
    expect(screen.getByText('Alert cancelled')).toBeInTheDocument()
    await advance(60_000) // well past the original deadline
    expect(mock.history.post.filter((c) => c.url.endsWith('/confirm'))).toHaveLength(0)
    expect(mock.history.post.some((c) => c.url.endsWith('/cancel'))).toBe(true)
    fireEvent.click(screen.getByText('Close'))
    expect(screen.queryByTestId('fall-modal')).toBeNull()
  })

  it('when the countdown reaches zero it confirms with the server and reports the incident', async () => {
    mock.onPost('/tourists/1/fall-events').reply(201, rec('pending'))
    mock.onPost('/tourists/1/fall-events/5/confirm').reply(200, rec('confirmed', { incident_id: 42 }))
    await mount()
    await simulateFall()
    await advance(29_000)
    expect(mock.history.post.some((c) => c.url.endsWith('/confirm'))).toBe(false) // not early
    await advance(1_500)
    expect(screen.getByText('Emergency raised')).toBeInTheDocument()
    expect(screen.getByTestId('fall-incident')).toHaveTextContent('Incident #42')
    expect(mock.history.post.filter((c) => c.url.endsWith('/confirm'))).toHaveLength(1) // exactly once
  })

  it('"Send SOS now" uses the SOS endpoint and hands the result to the app', async () => {
    const sos = { incident_id: 9, nearest_unit: { name: 'Unit A' }, live_tracking_active: true }
    mock.onPost('/tourists/1/fall-events').reply(201, rec('pending'))
    mock.onPost('/tourists/1/fall-events/5/sos').reply(200, { event: rec('sos_triggered', { incident_id: 9 }), sos })
    const onSos = vi.fn()
    await mount({ onSos })
    await simulateFall()
    fireEvent.click(screen.getByText(/SEND SOS NOW/))
    await flush()
    expect(onSos).toHaveBeenCalledWith(sos)
    expect(screen.getByText('SOS sent')).toBeInTheDocument()
    expect(body(mock.history.post.find((c) => c.url.endsWith('/sos')))).toMatchObject({ lat: 26.14, lng: 91.73 })
  })

  it('keeps the countdown running and retries while offline, then registers once back', async () => {
    let calls = 0
    mock.onPost('/tourists/1/fall-events').reply(() => {
      calls += 1
      return calls === 1 ? Promise.reject({ message: 'Network Error' }) : [201, rec('pending', { seconds_remaining: 22 })]
    })
    await mount()
    await simulateFall()
    expect(screen.getByText(/No connection/)).toBeInTheDocument()
    await advance(2_000)
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('28') // kept running offline
    await advance(1_500) // retry fires
    expect(calls).toBe(2)
    expect(screen.queryByText(/No connection/)).toBeNull()
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('22') // re-anchored on the server's clock
    expect(body(mock.history.post[1]).seconds_since_detection).toBeGreaterThanOrEqual(3) // the delay is reported
  })

  it('resumes a countdown the server already has running (e.g. started by a wearable)', async () => {
    mock.reset()
    mock.onGet('/fall-detection/config').reply(200, CONFIG)
    mock.onGet('/tourists/1/fall-events').reply(200, [])
    mock.onGet('/tourists/1/fall-events/active').reply(200,
      rec('pending', { source: 'wearable', simulated: false, seconds_remaining: 17 }))
    await mount()
    expect(screen.getByTestId('fall-modal')).toHaveAttribute('data-phase', 'countdown')
    expect(screen.getByTestId('fall-remaining')).toHaveTextContent('17')
    expect(screen.getByText('Detected by your wearable device')).toBeInTheDocument()
    expect(screen.queryByTestId('fall-sim-badge')).toBeNull() // a wearable event is never labelled simulated
  })

  it('a detection folded into an incident that already exists does not reopen a countdown', async () => {
    mock.onPost('/tourists/1/fall-events').reply(200, rec('confirmed', { incident_id: 3, deduplicated: true }))
    await mount()
    await simulateFall()
    expect(screen.queryByTestId('fall-modal')).toBeNull()
  })

  it('hides the simulation panel when the server turns simulation off', async () => {
    mock.reset()
    mock.onGet('/fall-detection/config').reply(200, { ...CONFIG, simulation_enabled: false })
    mock.onGet('/tourists/1/fall-events/active').reply(200, null)
    mock.onGet('/tourists/1/fall-events').reply(200, [])
    await mount()
    expect(screen.queryByTestId('fall-sim-panel')).toBeNull()
  })

  it('shows the permission-denied and sensor-unavailable states from the card', () => {
    const base = { enabled: true, config: CONFIG, history: [], phase: 'idle', secureContext: true,
      enableMonitoring() {}, disableMonitoring() {}, simulate() {} }
    const { rerender } = render(<FallDetectionCard fall={{ ...base, sensorStatus: 'denied' }} />)
    expect(screen.getByTestId('fall-status')).toHaveTextContent('Motion permission denied')
    expect(screen.getByText(/Allow .*Motion & Orientation/)).toBeInTheDocument()
    rerender(<FallDetectionCard fall={{ ...base, sensorStatus: 'unavailable', secureContext: false }} />)
    expect(screen.getByText(/secure \(HTTPS\)/)).toBeInTheDocument()
    rerender(<FallDetectionCard fall={{ ...base, sensorStatus: 'active' }} />)
    expect(screen.getByTestId('fall-status')).toHaveTextContent('Monitoring active')
    expect(render(<FallStatusPill fall={{ ...base, monitoring: true }} />).getByTestId('fall-pill')).toBeInTheDocument()
  })
})
