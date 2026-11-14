import { StrictMode } from 'react'
import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import i18n from '../i18n'
import useWalkHome from '../hooks/useWalkHome'
import WalkHomeCard from './WalkHomeCard'
import WalkHomeBanner from './WalkHomeBanner'
import WalkHomeGuardianPanel from './WalkHomeGuardianPanel'

// Maps and the live socket are irrelevant here (the server side is covered by
// the backend tests); stub them so the components mount in jsdom.
vi.mock('react-leaflet', () => ({
  MapContainer: ({ children }) => <div data-testid="leaflet">{children}</div>,
  TileLayer: () => null,
  Marker: ({ position }) => <div data-testid="marker" data-pos={position.join(',')} />,
  useMap: () => ({ setView: () => {}, getZoom: () => 15 }),
}))
vi.mock('../useWebSocket', () => ({ default: () => ({ connected: false }) }))

const mock = new MockAdapter(api)

const CONFIG = {
  enabled: true,
  duration: { min: 2, max: 720, default: 30, presets: [15, 30, 60] },
  interval: { min: 1, max: 120, default: 10, presets: [5, 10, 30] },
  grace: { min: 1, max: 30, default: 5 },
  max_extensions: 3, max_extension_minutes: 60,
  location_interval_seconds: 15, location_stale_seconds: 120, max_guardians: 10,
}
const T0 = '2026-11-14T10:00:00Z'
const iso = (sec) => new Date(Date.parse(T0) + sec * 1000).toISOString()
const J = (over = {}) => ({
  id: 5, tourist_id: 1, destination_name: 'Hostel Aurora', dest_lat: 26.15, dest_lng: 91.74,
  status: 'planned', phase: 'on_track', active: true,
  started_at: T0, arrive_by: iso(1800), next_checkin_deadline: iso(600), grace_minutes: 5,
  grace_deadline: iso(900), check_interval_minutes: 10, last_checkin_at: null, checkin_count: 0,
  server_time: T0, deadline_passed: false, has_location: false, last_lat: null, last_lng: null,
  last_location_at: null, location_age_seconds: null, location_stale: false,
  extensions: 0, extensions_left: 3, incident_id: null,
  guardians: [{ id: 2, name: 'Mum', has_access: true }], ...over,
})

function Harness() {
  const walk = useWalkHome({ tid: 1 })
  return (<><WalkHomeBanner walk={walk} onOpen={() => {}} /><WalkHomeCard walk={walk} /></>)
}

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const body = (call) => JSON.parse(call.data)
const posts = (suffix) => mock.history.post.filter((c) => c.url.endsWith(suffix))

async function mount(active = null) {
  mock.onGet('/tourists/1/walk-home/active').reply(200, active)
  const utils = render(<Harness />)
  await flush()
  await flush()
  return utils
}

// A controllable navigator.geolocation.
let geo
function installGeo() {
  geo = {
    ok: null, fail: null,
    watchPosition: vi.fn((ok, fail) => { geo.ok = ok; geo.fail = fail; return 7 }),
    clearWatch: vi.fn(),
    getCurrentPosition: vi.fn((ok) => ok({ coords: { latitude: 26.14, longitude: 91.73 } })),
  }
  Object.defineProperty(globalThis.navigator, 'geolocation', { value: geo, configurable: true })
}
const fix = (lat = 26.141, lng = 91.731) => act(async () => {
  geo.ok({ coords: { latitude: lat, longitude: lng, accuracy: 9 } })
  await vi.advanceTimersByTimeAsync(0)
})

beforeAll(() => i18n.changeLanguage('en'))

beforeEach(() => {
  mock.reset()
  installGeo()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'] })
  vi.setSystemTime(new Date(T0))
  mock.onGet('/walk-home/config').reply(200, CONFIG)
  mock.onGet('/tourists/1/guardians').reply(200, [
    { id: 2, guardian_name: 'Mum', revoked: false }, { id: 3, guardian_name: 'Dad', revoked: false }])
  mock.onGet('/tourists/1/walk-home').reply(200, [])
})
afterEach(() => vi.useRealTimers())

describe('Walk-Me-Home — under React StrictMode (what `npm run dev` uses)', () => {
  it('still loads a running journey and updates after actions', async () => {
    mock.onGet('/tourists/1/walk-home/active').reply(200, J())
    mock.onPost('/tourists/1/walk-home/5/checkin').reply(200, J({ checkin_count: 1, last_checkin_at: iso(5) }))
    render(<StrictMode><Harness /></StrictMode>)
    await flush()
    await flush()
    expect(screen.getByTestId('wh-active')).toBeInTheDocument() // dev double-mount must not freeze state
    fireEvent.click(screen.getByTestId('wh-checkin'))
    await flush()
    expect(screen.getByTestId('wh-last-checkin')).not.toHaveTextContent('None yet')
  })
})

describe('Walk-Me-Home — setup', () => {
  it('builds the form from server config (nothing hardcoded) and explains how it works', async () => {
    await mount()
    expect(screen.getByLabelText('Destination')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '15 min' })).toBeInTheDocument() // presets come from the server
    expect(screen.getAllByRole('button', { name: '30 min' })[0]).toHaveAttribute('aria-pressed', 'true') // server default
    expect(screen.getByText(/only the people ticked here/i)).toBeInTheDocument()
    expect(screen.getByText(/enforced by the server/i)).toBeInTheDocument()
    expect(screen.queryByTestId('wh-banner')).toBeNull()
  })

  it('reviews the details, then starts with the chosen guardians and the real current position', async () => {
    mock.onPost('/tourists/1/walk-home').reply(201, J())
    await mount()
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'Hostel Aurora' } })
    fireEvent.click(screen.getByLabelText('Mum'))
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.getByTestId('wh-review')).toHaveTextContent('Hostel Aurora')
    expect(screen.getByTestId('wh-review')).toHaveTextContent('Mum')
    expect(mock.history.post).toHaveLength(0) // nothing is started until confirmed
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    expect(body(mock.history.post[0])).toEqual({
      destination_name: 'Hostel Aurora', duration_minutes: 30, check_interval_minutes: 10,
      grace_minutes: 5, guardian_ids: [2], start_lat: 26.14, start_lng: 91.73,
    })
    expect(screen.getByTestId('wh-active')).toBeInTheDocument()
  })

  it('still starts when GPS is unavailable — it just sends no position', async () => {
    geo.getCurrentPosition = vi.fn((_ok, fail) => fail({ code: 1 }))
    mock.onPost('/tourists/1/walk-home').reply(201, J())
    await mount()
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'Home' } })
    fireEvent.click(screen.getByText('Review journey'))
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    const sent = body(mock.history.post[0])
    expect(sent).not.toHaveProperty('start_lat')
    expect(sent.destination_name).toBe('Home')
  })

  it('looks the destination up through the existing geocoder and never invents a pin', async () => {
    mock.onGet('/maps/geocode').reply((c) => [200, c.params.place === 'Kamakhya'
      ? { lat: 26.166, lng: 91.705 } : { lat: null, lng: null }])
    mock.onPost('/tourists/1/walk-home').reply(201, J())
    await mount()
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'Nowhere Land' } })
    fireEvent.click(screen.getByText('Find on map'))
    await flush()
    expect(screen.getByText(/Place not found/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'Kamakhya' } })
    fireEvent.click(screen.getByText('Find on map'))
    await flush()
    expect(screen.getByTestId('wh-found')).toHaveTextContent('26.1660')
    fireEvent.click(screen.getByText('Review journey'))
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    expect(body(mock.history.post[0])).toMatchObject({ dest_lat: 26.166, dest_lng: 91.705 })
  })

  it('warns when no guardian is selected and shows server rejections', async () => {
    mock.onPost('/tourists/1/walk-home').reply(409, { detail: 'You already have a journey in progress.' })
    await mount()
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'Home' } })
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.getByText(/No guardian selected/)).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('already have a journey')
    expect(screen.queryByTestId('wh-active')).toBeNull()
  })

  it('rejects out-of-range values before calling the server', async () => {
    await mount()
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: 'Home' } })
    fireEvent.change(screen.getByLabelText('Grace after a missed check-in'), { target: { value: '99' } })
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.getByRole('alert')).toHaveTextContent('1–30')
    expect(screen.queryByTestId('wh-review')).toBeNull()
  })
})

describe('Walk-Me-Home — live journey', () => {
  it('resumes a journey the server already has (refresh/reopen) without resetting its timer', async () => {
    await mount(J({ server_time: iso(120) })) // 2 min into it: 8 min to the check-in
    // The server's clock keeps moving: later polls report a later server_time.
    mock.onGet('/tourists/1/walk-home/active').reply(() => [200, J({ server_time: iso(120 + performance.now() / 1000) })])
    expect(screen.getByTestId('wh-remaining')).toHaveTextContent('8:00')
    expect(screen.getByTestId('wh-banner')).toHaveTextContent('Hostel Aurora')
    await advance(30_000)
    expect(screen.getByTestId('wh-remaining')).toHaveTextContent('7:30')
  })

  it('counts down from the SERVER clock even if the phone clock is wrong', async () => {
    vi.setSystemTime(new Date('2031-01-01T00:00:00Z')) // wildly wrong device clock
    await mount(J())
    mock.onGet('/tourists/1/walk-home/active').reply(() => [200, J({ server_time: iso(performance.now() / 1000) })])
    expect(screen.getByTestId('wh-remaining')).toHaveTextContent('10:00')
    await advance(60_000)
    expect(screen.getByTestId('wh-remaining')).toHaveTextContent('9:00')
  })

  it('checks in with one tap and shows the new deadline and last check-in', async () => {
    mock.onPost('/tourists/1/walk-home/5/checkin').reply(200,
      J({ next_checkin_deadline: iso(1200), last_checkin_at: iso(5), checkin_count: 1, server_time: iso(5) }))
    await mount(J())
    fireEvent.click(screen.getByTestId('wh-checkin'))
    await flush()
    expect(posts('/checkin')).toHaveLength(1)
    expect(screen.getByTestId('wh-remaining')).toHaveTextContent('19:55')
    expect(screen.getByTestId('wh-last-checkin')).not.toHaveTextContent('None yet')
  })

  it('can check in from the always-visible strip on any tab', async () => {
    mock.onPost('/tourists/1/walk-home/5/checkin').reply(200, J({ checkin_count: 1 }))
    await mount(J())
    fireEvent.click(screen.getByTestId('wh-banner-checkin'))
    await flush()
    expect(posts('/checkin')).toHaveLength(1)
  })

  it('shows a clear warning and the grace countdown when a check-in is missed', async () => {
    await mount(J({ status: 'missed', phase: 'missed', server_time: iso(630), deadline_passed: true }))
    expect(screen.getByTestId('wh-active')).toHaveAttribute('data-phase', 'missed')
    expect(screen.getByText('You missed a check-in')).toBeInTheDocument()
    expect(screen.getByTestId('wh-grace-left')).toHaveTextContent('4:30') // 900 - 630
    expect(screen.getByTestId('wh-checkin')).toHaveTextContent('RESPOND NOW')
    expect(screen.getByTestId('wh-banner')).toHaveAttribute('data-phase', 'missed')
  })

  it('a late check-in during the grace period returns to normal', async () => {
    mock.onPost('/tourists/1/walk-home/5/checkin').reply(200, J({ server_time: iso(640), next_checkin_deadline: iso(1240) }))
    await mount(J({ status: 'missed', phase: 'missed', server_time: iso(630) }))
    fireEvent.click(screen.getByTestId('wh-checkin'))
    await flush()
    expect(screen.getByTestId('wh-active')).toHaveAttribute('data-phase', 'on_track')
    expect(screen.queryByText('You missed a check-in')).toBeNull()
  })

  it('after escalation there is no check-in button, the incident is shown, and the journey can be ended', async () => {
    mock.onPost('/tourists/1/walk-home/5/end').reply(200, J({ status: 'completed', phase: 'completed', active: false }))
    await mount(J({ status: 'escalated', phase: 'escalated', incident_id: 42, server_time: iso(1000) }))
    expect(screen.queryByTestId('wh-checkin')).toBeNull()
    expect(screen.getByTestId('wh-incident')).toHaveTextContent('Incident #42')
    expect(screen.getAllByText(/No response/).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByTestId('wh-end'))
    await flush()
    expect(screen.queryByTestId('wh-active')).toBeNull()
  })

  it('keeps the journey and says so plainly when a check-in cannot reach the server', async () => {
    mock.onPost('/tourists/1/walk-home/5/checkin').networkError()
    await mount(J())
    fireEvent.click(screen.getByTestId('wh-checkin'))
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('No connection')
    expect(screen.getByTestId('wh-active')).toBeInTheDocument()
  })

  it('shows the guardians and whether each still has access', async () => {
    await mount(J({ guardians: [{ id: 2, name: 'Mum', has_access: true }, { id: 3, name: 'Dad', has_access: false }] }))
    const list = screen.getByTestId('wh-guardians')
    expect(list).toHaveTextContent('Mum')
    expect(list).toHaveTextContent('has access')
    expect(list).toHaveTextContent('access removed')
  })

  it('adds time through the server and respects the remaining allowance', async () => {
    mock.onPost('/tourists/1/walk-home/5/extend').reply(200, J({ extensions_left: 2 }))
    await mount(J())
    fireEvent.click(screen.getByText('+15 min'))
    await flush()
    expect(body(posts('/extend')[0])).toEqual({ minutes: 15 })
  })

  it('cancelling asks first, then records it and stops everything', async () => {
    mock.onPost('/tourists/1/walk-home/5/cancel').reply(200, J({ status: 'cancelled', phase: 'cancelled', active: false }))
    await mount(J())
    fireEvent.click(screen.getByTestId('wh-cancel'))
    expect(posts('/cancel')).toHaveLength(0) // not yet
    fireEvent.click(screen.getByTestId('wh-cancel-yes'))
    await flush()
    expect(posts('/cancel')).toHaveLength(1)
    expect(screen.getByTestId('wh-ended')).toHaveTextContent('cancelled')
    expect(geo.clearWatch).toHaveBeenCalled()
  })

  it('ending marks arrival, stops location sharing and offers a fresh start', async () => {
    mock.onPost('/tourists/1/walk-home/5/end').reply(200, J({ status: 'completed', phase: 'completed', active: false }))
    await mount(J())
    expect(geo.watchPosition).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId('wh-end'))
    await flush()
    expect(screen.getByTestId('wh-ended')).toHaveTextContent('arrived safely')
    expect(geo.clearWatch).toHaveBeenCalledWith(7)
    expect(screen.getByLabelText('Destination')).toBeInTheDocument()
  })
})

describe('Walk-Me-Home — location sharing', () => {
  it('does not touch GPS when there is no active journey', async () => {
    await mount(null)
    expect(geo.watchPosition).not.toHaveBeenCalled()
  })

  it('sends the first real fix at once, then only NEW fixes, with their age', async () => {
    mock.onPost('/tourists/1/walk-home/5/location').reply(200, { accepted: true, reason: '', journey: J({ has_location: true, last_lat: 26.141, last_lng: 91.731, location_age_seconds: 0 }) })
    await mount(J())
    expect(posts('/location')).toHaveLength(0) // nothing invented before a real reading
    await fix()
    expect(posts('/location')).toHaveLength(1)
    expect(body(posts('/location')[0])).toMatchObject({ lat: 26.141, lng: 91.731, accuracy_m: 9 })
    expect(body(posts('/location')[0]).fix_age_seconds).toBeGreaterThanOrEqual(0)
    await advance(45_000) // the same fix is not re-sent
    expect(posts('/location')).toHaveLength(1)
    await fix(26.142, 91.732)
    await advance(15_000)
    expect(posts('/location').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByTestId('wh-sharing')).toHaveAttribute('data-state', 'sharing')
    expect(screen.getByText('LIVE')).toBeInTheDocument()
    expect(screen.getAllByTestId('marker').length).toBeGreaterThanOrEqual(2) // destination + position
  })

  it('explains a denied permission and keeps the journey running', async () => {
    await mount(J())
    await act(async () => { geo.fail({ code: 1 }); await vi.advanceTimersByTimeAsync(0) })
    expect(screen.getByTestId('wh-sharing')).toHaveAttribute('data-state', 'denied')
    expect(screen.getByTestId('wh-sharing')).toHaveTextContent('permission denied')
    expect(screen.getByTestId('wh-active')).toBeInTheDocument()
    expect(posts('/location')).toHaveLength(0)
  })

  it('reports an offline connection and resends when it is back', async () => {
    let n = 0
    mock.onPost('/tourists/1/walk-home/5/location').reply(() => {
      n += 1
      return n === 1 ? Promise.reject({ message: 'Network Error' }) : [200, { accepted: true, journey: J() }]
    })
    await mount(J())
    await fix()
    expect(screen.getByTestId('wh-sharing')).toHaveAttribute('data-state', 'offline')
    await advance(15_000)
    expect(n).toBe(2)
    expect(screen.getByTestId('wh-sharing')).toHaveAttribute('data-state', 'sharing')
  })

  it('stops sharing when the server says the journey has ended', async () => {
    mock.onPost('/tourists/1/walk-home/5/location').reply(409, { detail: 'This journey has ended' })
    await mount(J())
    mock.onGet('/tourists/1/walk-home/active').reply(200, null)
    await fix()
    await flush()
    expect(screen.queryByTestId('wh-active')).toBeNull()
    expect(geo.clearWatch).toHaveBeenCalled()
  })

  it('labels an old position STALE instead of presenting it as live', async () => {
    await mount(J({ has_location: true, last_lat: 26.14, last_lng: 91.73, location_age_seconds: 600, location_stale: true }))
    expect(screen.getByText('STALE — not live')).toBeInTheDocument()
    expect(screen.queryByText('LIVE')).toBeNull()
  })
})

describe('Walk-Me-Home — guardian view', () => {
  beforeEach(() => {
    globalThis.WebSocket = class { constructor() { this.readyState = 0 } close() {} }
  })
  const G = (over = {}) => J({ tourist_name: 'Aarav Sharma', guardian_name: 'Mum', ...over })

  it('shows destination, timer, last check-in and a clearly-aged location', async () => {
    mock.onGet('/guardian/tok/journey').reply(200, { journey: G({
      server_time: iso(60), last_checkin_at: iso(30), has_location: true, last_lat: 26.14,
      last_lng: 91.73, location_age_seconds: 20, location_stale: false }) })
    render(<WalkHomeGuardianPanel token="tok" />)
    await flush()
    expect(screen.getByText(/You are following Aarav Sharma/)).toBeInTheDocument()
    expect(screen.getByText('Hostel Aurora')).toBeInTheDocument()
    expect(screen.getByTestId('wh-g-remaining')).toHaveTextContent('9:00')
    expect(screen.getByTestId('wh-g-location')).toHaveTextContent('LIVE')
    expect(screen.getByTestId('wh-g-last')).not.toHaveTextContent('None yet')
    expect(screen.getByTestId('wh-guardian')).not.toHaveTextContent('Incident')
  })

  it('flags a stale location and a missed check-in', async () => {
    mock.onGet('/guardian/tok/journey').reply(200, { journey: G({
      status: 'missed', phase: 'missed', alert: 'missed', server_time: iso(700), has_location: true,
      last_lat: 26.14, last_lng: 91.73, location_age_seconds: 900, location_stale: true }) })
    render(<WalkHomeGuardianPanel token="tok" />)
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('missed a check-in')
    expect(screen.getByTestId('wh-g-location')).toHaveTextContent('STALE')
    expect(screen.getByTestId('wh-g-location')).not.toHaveTextContent('LIVE')
  })

  it('shows the escalation to the guardian', async () => {
    mock.onGet('/guardian/tok/journey').reply(200, { journey: G({ status: 'escalated', phase: 'escalated', alert: 'escalated', server_time: iso(2000) }) })
    render(<WalkHomeGuardianPanel token="tok" />)
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('control room has been alerted')
  })

  it('says nothing is shared when there is no journey for this guardian', async () => {
    mock.onGet('/guardian/tok/journey').reply(200, { journey: null })
    render(<WalkHomeGuardianPanel token="tok" />)
    await flush()
    expect(screen.getByTestId('wh-guardian-empty')).toHaveTextContent('No journey is being shared')
  })

  it('drops the journey and its location the moment it ends', async () => {
    let live = true
    mock.onGet('/guardian/tok/journey').reply(() => [200, { journey: live ? G({ has_location: true, last_lat: 1, last_lng: 1, location_age_seconds: 1 }) : null }])
    render(<WalkHomeGuardianPanel token="tok" />)
    await flush()
    expect(screen.getByTestId('wh-guardian')).toBeInTheDocument()
    live = false
    await advance(10_000)
    expect(screen.queryByTestId('wh-guardian')).toBeNull()
    expect(screen.getByTestId('wh-guardian-empty')).toHaveTextContent('has ended')
  })
})
