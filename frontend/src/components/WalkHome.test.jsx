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
  Polyline: ({ positions }) => <div data-testid="route-line" data-n={positions.length} />,
  useMap: () => ({ setView: () => {}, getZoom: () => 15, fitBounds: () => {} }),
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
  route_buffer_percent: 25, route_buffer_min_minutes: 5,
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

let sosFn
function Harness() {
  const walk = useWalkHome({ tid: 1 })
  return (<><WalkHomeBanner walk={walk} onOpen={() => {}} /><WalkHomeCard walk={walk} onSOS={(m) => sosFn(m)} /></>)
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
    { id: 2, guardian_name: 'Mum', guardian_contact: '+919876543210', revoked: false },
    { id: 3, guardian_name: 'Dad', guardian_contact: '', revoked: false }])
  mock.onGet('/maps/modes').reply(200, { modes: ['walking', 'bicycling', 'driving'] })
  sosFn = vi.fn()
  try { localStorage.clear() } catch { /* ignore */ }
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

const HOSPITAL = { name: 'City Hospital, MG Road, Bengaluru, Karnataka, India', lat: 12.97, lng: 77.6, demo: false }
const ROUTE = { distance_km: 3.2, duration_min: 12, demo: false, provider: 'osrm',
  geometry: [[12.9, 77.5], [12.93, 77.55], [12.97, 77.6]] }

// Type a place, let the debounce elapse, tap the suggestion.
async function typeAndPick(text, place = HOSPITAL) {
  fireEvent.change(screen.getByPlaceholderText(/Search any place/), { target: { value: text } })
  await advance(500)
  fireEvent.click(screen.getByText(place.name.split(', ')[0], { exact: false }))
  await flush()
  await flush()
}
const stubMaps = ({ route = ROUTE, places = [HOSPITAL] } = {}) => {
  mock.onGet('/maps/suggest').reply(200, { results: places })
  mock.onGet('/maps/route').reply(typeof route === 'function' ? route : () => [200, route])
}

describe('Walk-Me-Home — setup', () => {
  it('builds the form from server config (nothing hardcoded) and explains how it works', async () => {
    await mount()
    expect(screen.getByPlaceholderText(/Search any place/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '5 min' })).toBeInTheDocument() // interval presets come from the server
    expect(screen.getAllByRole('button', { name: '10 min' })[0]).toHaveAttribute('aria-pressed', 'true') // server default
    expect(screen.queryByRole('button', { name: '120 min' })).toBeNull() // no manual "journey time" guessing by default
    expect(screen.getByText(/only the people ticked here/i)).toBeInTheDocument()
    expect(screen.getByText(/enforced by the server/i)).toBeInTheDocument()
    expect(screen.queryByTestId('wh-banner')).toBeNull()
  })

  it('suggests places while typing, but only once the typing pauses (debounced) and never for 1-2 letters', async () => {
    stubMaps()
    await mount()
    const input = screen.getByPlaceholderText(/Search any place/)
    fireEvent.change(input, { target: { value: 'Ci' } })
    await advance(1000)
    expect(mock.history.get.filter((c) => c.url === '/maps/suggest')).toHaveLength(0)
    fireEvent.change(input, { target: { value: 'Cit' } })
    await advance(200)
    fireEvent.change(input, { target: { value: 'City Hosp' } })
    await advance(500)
    const calls = mock.history.get.filter((c) => c.url === '/maps/suggest')
    expect(calls).toHaveLength(1)
    expect(calls[0].params.q).toBe('City Hosp')
    expect(screen.getByText('City Hospital', { exact: false })).toBeInTheDocument()
    expect(screen.getByText(/MG Road, Bengaluru/)).toBeInTheDocument() // address under the name
    // typing the same thing again is served from the cache
    fireEvent.change(input, { target: { value: 'City Hos' } })
    fireEvent.change(input, { target: { value: 'City Hosp' } })
    await advance(500)
    expect(mock.history.get.filter((c) => c.url === '/maps/suggest')).toHaveLength(1)
  })

  it('says so when a place genuinely cannot be found — and lets nothing through', async () => {
    stubMaps({ places: [] })
    await mount()
    fireEvent.change(screen.getByPlaceholderText(/Search any place/), { target: { value: 'qqzzxx nowhere' } })
    await advance(500)
    expect(screen.getByText(/No matching place found/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a destination')
    expect(screen.queryByTestId('wh-review')).toBeNull()
  })

  it('picks the destination, reads the live GPS fix and computes distance, time and the deadline itself', async () => {
    stubMaps()
    await mount()
    await typeAndPick('City Hosp')
    const r = mock.history.get.find((c) => c.url === '/maps/route')
    expect(r.params).toMatchObject({ olat: 26.14, olng: 91.73, dlat: 12.97, dlng: 77.6, mode: 'walking' })
    expect(screen.getByTestId('wh-distance')).toHaveTextContent('3.2 km')
    expect(screen.getByTestId('wh-travel')).toHaveTextContent('12 min')
    // 12 min + max(25% = 3, minimum 5) = 17 min: travel time plus a safety buffer.
    expect(screen.getByTestId('wh-deadline')).toHaveTextContent('17 min')
    expect(screen.getByTestId('route-line')).toHaveAttribute('data-n', '3')
    expect(screen.queryByTestId('wh-estimate-only')).toBeNull()
  })

  it('recalculates when the travel mode changes', async () => {
    stubMaps({ route: (c) => [200, c.params.mode === 'driving' ? { ...ROUTE, duration_min: 4 } : ROUTE] })
    await mount()
    await typeAndPick('City Hosp')
    expect(screen.getByTestId('wh-travel')).toHaveTextContent('12 min')
    fireEvent.click(screen.getByTestId('wh-mode-driving'))
    await flush()
    await flush()
    expect(mock.history.get.filter((c) => c.url === '/maps/route').pop().params.mode).toBe('driving')
    expect(screen.getByTestId('wh-travel')).toHaveTextContent('4 min')
    expect(screen.getByTestId('wh-deadline')).toHaveTextContent('9 min') // 4 + 5
  })

  it('starts the journey with the computed deadline, the real destination pin and the chosen contacts', async () => {
    stubMaps()
    mock.onPost('/tourists/1/walk-home').reply(201, J())
    await mount()
    await typeAndPick('City Hosp')
    fireEvent.click(screen.getByRole('checkbox', { name: /Mum/ }))
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.getByTestId('wh-review')).toHaveTextContent('City Hospital')
    expect(screen.getByTestId('wh-review')).toHaveTextContent('3.2 km')
    expect(screen.getByTestId('wh-review')).toHaveTextContent('Mum')
    expect(mock.history.post).toHaveLength(0) // nothing is started until confirmed
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    expect(body(mock.history.post[0])).toEqual({
      destination_name: HOSPITAL.name, dest_lat: 12.97, dest_lng: 77.6,
      duration_minutes: 17, check_interval_minutes: 10, grace_minutes: 5,
      guardian_ids: [2], start_lat: 26.14, start_lng: 91.73,
    })
    expect(screen.getByTestId('wh-active')).toBeInTheDocument()
    // the route is kept on the device so the live screen can follow it
    expect(JSON.parse(localStorage.getItem('walkhome.plan.5')).geometry).toHaveLength(3)
  })

  it('never invents a duration when the route fails: explains why and offers the manual picker', async () => {
    stubMaps({ route: () => [502, { detail: 'routing down' }] })
    mock.onPost('/tourists/1/walk-home').reply(201, J())
    await mount()
    await typeAndPick('City Hosp')
    expect(screen.getByTestId('wh-route-error')).toBeInTheDocument()
    expect(screen.queryByTestId('wh-deadline')).toBeNull()
    expect(screen.getAllByRole('button', { name: '30 min' })[0]).toBeInTheDocument() // manual fallback
    fireEvent.click(screen.getByRole('button', { name: '60 min' }))
    fireEvent.click(screen.getByText('Review journey'))
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    expect(body(mock.history.post[0]).duration_minutes).toBe(60)
  })

  it('without a GPS fix it asks where you start from and still calculates the time automatically', async () => {
    geo.getCurrentPosition = vi.fn((_ok, fail) => fail({ code: 1 }))
    const KOCHI = { name: 'Kochi Metro Station, Kochi, Kerala, India', lat: 9.97, lng: 76.28, demo: false }
    mock.onGet('/maps/suggest').reply((c) => [200, { results: c.params.q.startsWith('Koc') ? [KOCHI] : [HOSPITAL] }])
    mock.onGet('/maps/route').reply(200, ROUTE)
    mock.onPost('/tourists/1/walk-home').reply(201, J())
    await mount()
    await typeAndPick('City Hosp')
    expect(mock.history.get.filter((c) => c.url === '/maps/route')).toHaveLength(0) // no fake start point
    expect(screen.getByText(/Location permission is off/)).toBeInTheDocument()
    expect(screen.getByTestId('wh-need-location')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '60 min' })).toBeNull() // not asked to guess a time
    // pick a starting place instead
    fireEvent.change(screen.getByPlaceholderText(/Search your starting place/), { target: { value: 'Kochi Metro' } })
    await advance(500)
    fireEvent.click(screen.getByText('Kochi Metro Station', { exact: false }))
    await flush()
    await flush()
    expect(mock.history.get.filter((c) => c.url === '/maps/route')[0].params).toMatchObject({ olat: 9.97, olng: 76.28 })
    expect(screen.getByTestId('wh-deadline')).toHaveTextContent('17 min')
    fireEvent.click(screen.getByText('Review journey'))
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    const sent = body(mock.history.post[0])
    expect(sent.duration_minutes).toBe(17)
    expect(sent).not.toHaveProperty('start_lat') // GPS gave nothing; nothing invented
  })

  it('keeps both ways to set the start: a searched place, or "Detect my current location"', async () => {
    const KOCHI = { name: 'Kochi Metro Station, Kochi, Kerala, India', lat: 9.97, lng: 76.28, demo: false }
    mock.onGet('/maps/suggest').reply((c) => [200, { results: c.params.q.startsWith('Koc') ? [KOCHI] : [HOSPITAL] }])
    mock.onGet('/maps/route').reply(200, ROUTE)
    await mount()
    await typeAndPick('City Hosp')
    // both options are on screen at once, and GPS was tried automatically
    expect(screen.getByPlaceholderText(/Search your starting place/)).toBeInTheDocument()
    expect(screen.getByTestId('wh-detect')).toHaveTextContent('Detect my current location')
    expect(mock.history.get.filter((c) => c.url === '/maps/route')[0].params).toMatchObject({ olat: 26.14, olng: 91.73 })
    // choose a searched start: the route restarts from there
    fireEvent.change(screen.getByPlaceholderText(/Search your starting place/), { target: { value: 'Kochi Metro' } })
    await advance(500)
    fireEvent.click(screen.getByText('Kochi Metro Station', { exact: false }))
    await flush()
    await flush()
    expect(mock.history.get.filter((c) => c.url === '/maps/route').pop().params).toMatchObject({ olat: 9.97, olng: 76.28 })
    expect(screen.getByText('Starting point')).toBeInTheDocument() // labelled as the start, not a "Destination"
    // detect again: back to the live position, fresh fix requested
    const before = geo.getCurrentPosition.mock.calls.length
    fireEvent.click(screen.getByTestId('wh-detect'))
    await flush()
    await flush()
    expect(geo.getCurrentPosition.mock.calls.length).toBe(before + 1)
    expect(mock.history.get.filter((c) => c.url === '/maps/route').pop().params).toMatchObject({ olat: 26.14, olng: 91.73 })
    expect(screen.getByPlaceholderText(/Search your starting place/)).toBeInTheDocument()
  })

  it('does not show the time picker while the location is still being found', async () => {
    geo.getCurrentPosition = vi.fn() // never answers
    stubMaps()
    await mount()
    await typeAndPick('City Hosp')
    expect(screen.queryByRole('button', { name: '60 min' })).toBeNull()
    expect(screen.getByText('Review journey')).toBeInTheDocument()
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.queryByTestId('wh-review')).toBeNull() // blocked until a start point exists
    expect(screen.getByRole('alert')).toHaveTextContent(/where you are starting from/i)
    // a start box is offered straight away -- the traveller is never stuck waiting on GPS
    expect(screen.getByTestId('wh-start-picker')).toBeInTheDocument()
    expect(screen.queryByText('Calculating route…')).toBeNull()
  })

  it('labels a straight-line estimate as one', async () => {
    stubMaps({ route: { ...ROUTE, demo: true, provider: 'estimate', geometry: null } })
    await mount()
    await typeAndPick('City Hosp')
    expect(screen.getByTestId('wh-estimate-only')).toHaveTextContent(/straight-line estimate/)
    expect(screen.queryByTestId('route-line')).toBeNull() // no fake road drawn
  })

  it('refuses a trip longer than the server maximum and asks for another time/mode', async () => {
    stubMaps({ route: { ...ROUTE, duration_min: 900 } })
    await mount()
    await typeAndPick('City Hosp')
    expect(screen.getByTestId('wh-too-long')).toHaveTextContent('720')
    expect(screen.queryByTestId('wh-deadline')).not.toBeNull()
  })

  it('lets the traveller override the calculated time', async () => {
    stubMaps()
    mock.onPost('/tourists/1/walk-home').reply(201, J())
    await mount()
    await typeAndPick('City Hosp')
    fireEvent.click(screen.getByTestId('wh-manual-toggle'))
    fireEvent.click(screen.getByRole('button', { name: '15 min' }))
    fireEvent.click(screen.getByText('Review journey'))
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    expect(body(mock.history.post[0]).duration_minutes).toBe(15)
  })

  it('warns when no guardian is selected and shows server rejections', async () => {
    stubMaps()
    mock.onPost('/tourists/1/walk-home').reply(409, { detail: 'You already have a journey in progress.' })
    await mount()
    await typeAndPick('City Hosp')
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.getByText(/No guardian selected/)).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('wh-start'))
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('already have a journey')
    expect(screen.queryByTestId('wh-active')).toBeNull()
  })

  it('rejects out-of-range values before calling the server', async () => {
    stubMaps()
    await mount()
    await typeAndPick('City Hosp')
    fireEvent.click(screen.getByText('Check-in settings')) // advanced section
    fireEvent.change(screen.getByLabelText('Grace after a missed check-in'), { target: { value: '99' } })
    fireEvent.click(screen.getByText('Review journey'))
    expect(screen.getByRole('alert')).toHaveTextContent('1–30')
    expect(screen.queryByTestId('wh-review')).toBeNull()
  })
})

describe('Walk-Me-Home — trusted contacts', () => {
  it('the Add guardian button never submits a surrounding form (no page refresh)', async () => {
    mock.onPost('/tourists/1/guardians').reply(201, { id: 9, guardian_name: 'Sis', guardian_contact: '' })
    await mount()
    for (const f of document.querySelectorAll('form')) expect(f.parentElement.closest('form')).toBeNull() // no nested forms
    const submits = []
    const onSubmit = (e) => { submits.push(e.target); e.preventDefault() }
    document.addEventListener('submit', onSubmit)
    fireEvent.change(screen.getByLabelText('Name (e.g. Mum)'), { target: { value: 'Sis' } })
    fireEvent.click(screen.getByText('Add guardian'))
    await flush()
    document.removeEventListener('submit', onSubmit)
    expect(posts('/guardians')).toHaveLength(1)
    expect(submits.length).toBeLessThanOrEqual(1) // at most the contacts form itself, never a parent form
  })

  it('shows each contact once with its number — no duplicate "myself" rows', async () => {
    await mount()
    const rows = screen.getAllByTestId('wh-contact')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('Mum')
    expect(rows[0]).toHaveTextContent('+919876543210')
    expect(rows[1]).toHaveTextContent('no phone')
  })

  it('adds a contact and ticks it for this journey; shows the server\'s reason when it is refused', async () => {
    mock.onPost('/tourists/1/guardians').replyOnce(422, { detail: [{ msg: 'Value error, Enter a valid phone number (7-15 digits, e.g. +919876543210)' }] })
    mock.onPost('/tourists/1/guardians').replyOnce(409, { detail: 'Mum is already in your trusted contacts.' })
    mock.onPost('/tourists/1/guardians').replyOnce(201, { id: 9, guardian_name: 'Sis', guardian_contact: '+911234567890' })
    await mount()
    const name = screen.getByLabelText('Name (e.g. Mum)')
    const phone = screen.getByLabelText(/Phone number/, { selector: 'form input' })
    fireEvent.change(name, { target: { value: 'Sis' } })
    fireEvent.change(phone, { target: { value: 'abc' } })
    fireEvent.click(screen.getByText('Add guardian'))
    await flush()
    expect(screen.getByTestId('wh-contact-error')).toHaveTextContent('valid phone number')
    fireEvent.click(screen.getByText('Add guardian'))
    await flush()
    expect(screen.getByTestId('wh-contact-error')).toHaveTextContent('already in your trusted contacts')
    fireEvent.change(phone, { target: { value: '+91 12345 67890' } })
    fireEvent.click(screen.getByText('Add guardian'))
    await flush()
    expect(screen.queryByTestId('wh-contact-error')).toBeNull()
    expect(name).toHaveValue('')
  })

  it('edits a contact in place and shows a refusal', async () => {
    mock.onPatch('/tourists/1/guardians/2').replyOnce(409, { detail: 'Dad is already in your trusted contacts.' })
    mock.onPatch('/tourists/1/guardians/2').replyOnce(200, { id: 2, guardian_name: 'Mummy', guardian_contact: '+919876543210' })
    await mount()
    fireEvent.click(screen.getAllByText('Edit')[0])
    fireEvent.change(screen.getByDisplayValue('Mum'), { target: { value: 'Mummy' } })
    fireEvent.click(screen.getByText('Save'))
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent('already in your trusted contacts')
    fireEvent.click(screen.getByText('Save'))
    await flush()
    expect(body(mock.history.patch[1])).toEqual({ guardian_name: 'Mummy', guardian_contact: '+919876543210' })
  })

  it('removes a contact only after confirmation', async () => {
    mock.onPost('/tourists/1/guardians/3/revoke').reply(200, {})
    await mount()
    fireEvent.click(screen.getAllByTestId('wh-contact-remove')[1])
    expect(posts('/revoke')).toHaveLength(0)
    expect(screen.getByText(/lose access to any journey/)).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('wh-contact-remove-yes'))
    await flush()
    expect(posts('/guardians/3/revoke')).toHaveLength(1)
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
    expect(screen.getByPlaceholderText(/Search any place/)).toBeInTheDocument()
  })
})

describe('Walk-Me-Home — SOS during a journey', () => {
  const open = async () => {
    await mount(J())
    fireEvent.click(screen.getByTestId('wh-sos-open'))
  }

  it('needs a second tap, so one accidental press sends nothing', async () => {
    await open()
    expect(sosFn).not.toHaveBeenCalled()
    expect(screen.getByRole('alertdialog', { name: /SOS/ })).toBeInTheDocument()
    fireEvent.click(screen.getByText('Keep going'))
    expect(sosFn).not.toHaveBeenCalled()
    expect(screen.queryByTestId('wh-sos-send')).toBeNull()
  })

  it('sends the journey details with the SOS and reports the incident the server returned', async () => {
    sosFn = vi.fn().mockResolvedValue({ incident_id: 77 })
    await open()
    fireEvent.click(screen.getByTestId('wh-sos-send'))
    await flush()
    expect(sosFn).toHaveBeenCalledTimes(1)
    expect(sosFn.mock.calls[0][0]).toMatch(/Walk-Me-Home journey to Hostel Aurora/)
    expect(screen.getByTestId('wh-sos-sent')).toHaveTextContent('#77')
    expect(screen.getByText(/Call emergency number/)).toBeInTheDocument()
  })

  it('does not claim delivery when it was only queued offline', async () => {
    sosFn = vi.fn().mockResolvedValue({ queued: true })
    await open()
    fireEvent.click(screen.getByTestId('wh-sos-send'))
    await flush()
    expect(screen.getByTestId('wh-sos-queued')).toHaveTextContent(/will be sent automatically/)
    expect(screen.queryByTestId('wh-sos-sent')).toBeNull()
  })

  it('says it failed — and offers the phone call — when the server rejects it', async () => {
    sosFn = vi.fn().mockRejectedValue(new Error('boom'))
    await open()
    fireEvent.click(screen.getByTestId('wh-sos-send'))
    await flush()
    expect(screen.getByTestId('wh-sos-failed')).toBeInTheDocument()
    expect(screen.queryByTestId('wh-sos-sent')).toBeNull()
    expect(screen.getByText(/Call emergency number/).closest('a')).toHaveAttribute('href', 'tel:112')
  })
})

describe('Walk-Me-Home — following the route', () => {
  const PATH = [[12.9, 77.5], [12.9, 77.52], [12.9, 77.54], [12.9, 77.56]] // ~2 km east
  const withPlan = () => localStorage.setItem('walkhome.plan.5', JSON.stringify({
    mode: 'walking', geometry: PATH, distance_km: 2.2, duration_min: 28, dest: PATH[3] }))
  const at = (lat, lng, n) => J({
    dest_lat: 12.9, dest_lng: 77.56, has_location: true, last_lat: lat, last_lng: lng,
    last_location_at: iso(n), location_age_seconds: 1, location_accuracy_m: 10, server_time: iso(n) })
  // Each 30 s poll returns the next position.
  async function feed(positions) {
    await mount(positions[0])
    let i = 1 // the mount above consumed positions[0]; each later 30 s poll gets the next
    mock.onGet('/tourists/1/walk-home/active').reply(() => [200, positions[Math.min(i++, positions.length - 1)]])
    for (let k = 1; k < positions.length; k += 1) await advance(30_000)
  }

  it('shows distance and time left along the planned route', async () => {
    withPlan()
    await feed([at(12.9, 77.52, 1)])
    expect(screen.getByTestId('wh-progress')).toHaveTextContent(/km to go/)
    expect(screen.getByTestId('wh-progress')).toHaveTextContent(/min left/)
    expect(screen.getByTestId('route-line')).toBeInTheDocument()
  })

  it('flags a wrong turn only after two separate fixes far from the route, and can recalculate', async () => {
    withPlan()
    mock.onGet('/maps/route').reply(200, { ...ROUTE, geometry: [[12.95, 77.52], [12.9, 77.56]] })
    await feed([at(12.95, 77.52, 1)])           // first far fix
    expect(screen.queryByTestId('wh-deviation')).toBeNull()
    await advance(30_000)                        // second far fix
    mock.onGet('/tourists/1/walk-home/active').reply(200, at(12.95, 77.52, 31))
    await advance(30_000)
    expect(screen.getByTestId('wh-deviation')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('wh-recalc'))
    await flush()
    expect(mock.history.get.filter((c) => c.url === '/maps/route')).toHaveLength(1)
    expect(screen.queryByTestId('wh-deviation')).toBeNull()
    expect(JSON.parse(localStorage.getItem('walkhome.plan.5')).geometry).toHaveLength(2)
  })

  it('ignores a vague (low-accuracy) or stale fix instead of crying "off route"', async () => {
    withPlan()
    const vague = (n) => ({ ...at(12.95, 77.52, n), location_accuracy_m: 900 })
    await feed([vague(1), vague(31), vague(61)])
    expect(screen.queryByTestId('wh-deviation')).toBeNull()
  })

  it('asks before ending when two fixes are at the destination — never ends by itself by default', async () => {
    withPlan()
    mock.onPost('/tourists/1/walk-home/5/end').reply(200, J({ status: 'completed', phase: 'completed', active: false }))
    await feed([at(12.9, 77.5599, 1), at(12.9, 77.5599, 31), at(12.9, 77.5599, 61)])
    expect(screen.getByTestId('wh-arrival-prompt')).toBeInTheDocument()
    expect(posts('/end')).toHaveLength(0)
    fireEvent.click(screen.getByTestId('wh-arrival-yes'))
    await flush()
    expect(posts('/end')).toHaveLength(1)
  })

  it('a single reading at the destination is not enough', async () => {
    withPlan()
    await feed([at(12.9, 77.5599, 1)])
    expect(screen.queryByTestId('wh-arrival-prompt')).toBeNull()
  })

  it('ends by itself on arrival only when the traveller turned that on', async () => {
    withPlan()
    mock.onPost('/tourists/1/walk-home/5/end').reply(200, J({ status: 'completed', phase: 'completed', active: false }))
    const pos = [at(12.9, 77.5599, 1), at(12.9, 77.5599, 31), at(12.9, 77.5599, 61)]
    await mount(pos[0])
    let i = 1
    mock.onGet('/tourists/1/walk-home/active').reply(() => [200, pos[Math.min(i++, 2)]])
    fireEvent.click(screen.getByTestId('wh-auto-end'))
    await advance(30_000)
    await advance(30_000)
    expect(posts('/end')).toHaveLength(1)
  })

  it('warns when the device goes offline, and says the server is still timing the journey', async () => {
    await mount(J())
    expect(screen.queryByTestId('wh-offline')).toBeNull()
    await act(async () => { window.dispatchEvent(new Event('offline')) })
    expect(screen.getByTestId('wh-offline')).toHaveTextContent(/still being timed by the server/)
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(screen.queryByTestId('wh-offline')).toBeNull()
  })
})

describe('Walk-Me-Home — history', () => {
  it('lists finished journeys with date and duration, and clears them on request', async () => {
    mock.onGet('/tourists/1/walk-home').reply(200, [
      J({ id: 1, status: 'completed', active: false, destination_name: 'City Hospital, MG Road',
        started_at: '2026-11-10T10:00:00Z', ended_at: '2026-11-10T10:25:00Z' })])
    mock.onDelete('/tourists/1/walk-home').reply(200, { deleted: 1 })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    await mount()
    const list = screen.getByTestId('wh-history')
    expect(list).toHaveTextContent('City Hospital')
    expect(list).toHaveTextContent('25 min')
    expect(list).toHaveTextContent('Arrived safely')
    fireEvent.click(screen.getByTestId('wh-history-clear'))
    await flush()
    expect(mock.history.delete).toHaveLength(1)
    expect(screen.queryByTestId('wh-history')).toBeNull()
    expect(screen.getByTestId('wh-ended')).toHaveTextContent('History cleared')
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
