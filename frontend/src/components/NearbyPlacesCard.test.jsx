import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, act, within } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import i18n from '../i18n'
import NearbyPlacesCard from './NearbyPlacesCard.jsx'

vi.mock('react-leaflet', async () => {
  const React = await import('react')
  const glyph = (icon) => icon?.options?.html?.match(/🏥|💊|👮|🚕|📍/u)?.[0]
  return {
    MapContainer: ({ children }) => <div data-testid="leaflet">{children}</div>,
    TileLayer: () => null,
    Polyline: ({ positions }) => <div data-testid="route-line" data-n={positions.length} />,
    Marker: React.forwardRef(function MockMarker({ position, icon, children, eventHandlers }, ref) {
      React.useImperativeHandle(ref, () => ({ openPopup: () => {} }))
      return <div data-testid="marker" data-pos={position.join(',')} data-glyph={glyph(icon)} onClick={() => eventHandlers?.click?.()}>{children}</div>
    }),
    Popup: ({ children }) => <div data-testid="popup">{children}</div>,
    useMap: () => ({ setView: () => {}, fitBounds: () => {}, getZoom: () => 13 }),
  }
})

const mock = new MockAdapter(api)
const here = { lat: 28.6315, lng: 77.2167 }

const P = (id, category, name, km, extra = {}) => ({
  id: `osm:node/${id}`, osm_url: `https://www.openstreetmap.org/node/${id}`, name, category,
  kind: { hospital: 'Hospital', pharmacy: 'Pharmacy', police: 'Police station', transport: 'Metro station' }[category],
  lat: 28.63 + id / 1000, lng: 77.21 + id / 1000, distance_km: km, address: null, phone: null, phones: [], website: null,
  opening_hours: null, open_now: null, open_24h: false, operator_type: null, emergency: false, details: {}, rank: 1,
  source: 'osm', ...extra,
})
const DATA = {
  hospital: [
    P(1, 'hospital', 'Corner Clinic', 0.3, { kind: 'Clinic', rank: 2 }),
    P(2, 'hospital', 'City Hospital', 1.2, { kind: 'Emergency hospital', emergency: true, rank: 0, operator_type: 'Government',
      phone: '+91 11 2345 6789', phones: ['+91 11 2345 6789', '+91 11 9999 0000'], address: 'Ring Road, Delhi', open_24h: true, open_now: true,
      opening_hours: '24/7', website: 'http://cityhospital.example.com' }),
    P(3, 'hospital', 'Care Hospital', 2.5),
  ],
  pharmacy: [
    P(10, 'pharmacy', 'Day Chemist', 0.4, { opening_hours: 'Mo-Sa 09:00-21:00', open_now: true }),
    P(11, 'pharmacy', 'Closed Chemist', 0.6, { opening_hours: 'Mo-Sa 09:00-13:00', open_now: false }),
    P(12, 'pharmacy', 'Mystery Medicals', 0.8, { opening_hours: 'by appointment' }),
    P(13, 'pharmacy', 'No Hours Store', 1.0),
  ],
  police: [
    P(20, 'police', 'CP Police Station', 0.5, { phone: '+91 11 2334 0000', phones: ['+91 11 2334 0000'] }),
    P(21, 'police', 'Janpath Police Post', 0.9, { kind: 'Police post' }),
  ],
  transport: [
    P(30, 'transport', 'Rajiv Chowk', 0.3, { details: { operator: 'DMRC', network: 'Delhi Metro', lines: 'Blue;Yellow' } }),
    P(31, 'transport', 'CP Bus Stop', 0.6, { kind: 'Bus stop' }),
  ],
}
const REGION = { name: 'Connaught Place, New Delhi', country_code: 'IN' }

const replyWith = (over = {}) => (cfg) => {
  const cat = cfg.params.category
  const places = over[cat] ?? DATA[cat] ?? []
  return [200, { lat: cfg.params.lat, lng: cfg.params.lng, radius_km: cfg.params.radius_km, category: cat, places, count: places.length,
    provider: over.provider || 'overpass', degraded: !!over.degraded, cached: false, stale: !!over.stale, region: REGION }]
}
const calls = () => mock.history.get.filter((c) => c.url === '/nearby')

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const settle = async () => { await advance(400); await flush(); await flush() }

let geo
const installGeo = (mode = 'ok') => {
  geo = {
    getCurrentPosition: vi.fn((ok, fail) => {
      if (mode === 'ok') ok({ coords: { latitude: here.lat, longitude: here.lng, accuracy: 15 }, timestamp: Date.now() })
      else if (mode === 'denied') fail({ code: 1 })
      else if (mode === 'unavailable') fail({ code: 2 })
      else if (mode === 'timeout') fail({ code: 3 })
    }),
  }
  Object.defineProperty(globalThis.navigator, 'geolocation', { value: geo, configurable: true })
}
const open = async (mode = 'ok') => {
  installGeo(mode)
  const utils = render(<NearbyPlacesCard />)
  await flush()
  await settle()
  return utils
}
const names = () => screen.queryAllByTestId('nearby-place').map((e) => e.querySelector('.font-semibold').textContent.replace(/^\S+\s/u, ''))

beforeAll(() => i18n.changeLanguage('en'))
beforeEach(() => {
  mock.reset()
  try { localStorage.clear() } catch { /* ignore */ }
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
  mock.onGet('/nearby').reply(replyWith())
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('Nearby — finding the user', () => {
  it('opens already searching: asks the device, then searches hospitals around THAT position', async () => {
    await open()
    expect(geo.getCurrentPosition).toHaveBeenCalled()
    expect(calls()).toHaveLength(1)
    expect(calls()[0].params).toMatchObject({ lat: here.lat, lng: here.lng, category: 'hospital', radius_km: 5 })
    expect(screen.getByTestId('discovery-area')).toHaveTextContent('Connaught Place, New Delhi')
    expect(names()).toEqual(['City Hospital', 'Care Hospital', 'Corner Clinic']) // emergency hospital, other hospital, then the clinic
    expect(screen.queryByText(/Nothing found nearby/)).toBeNull()
  })

  it('sends the device clock offset only for the live position (so "open now" is honest)', async () => {
    await open()
    expect(calls()[0].params.tz_offset_min).toBe(-new Date().getTimezoneOffset())
  })

  it('permission refused: names the real problem, searches nothing, offers a manual place', async () => {
    await open('denied')
    expect(screen.getByText(/Location permission is off/)).toBeInTheDocument()
    expect(screen.queryByText(/found within|Nothing found|No hospitals/)).toBeNull()
    expect(calls()).toHaveLength(0)
    expect(screen.getByPlaceholderText('Search a city or area…')).toBeInTheDocument()
    expect(screen.queryByTestId('nearby-empty')).toBeNull()
  })

  it.each([['unavailable', /couldn't be determined/], ['timeout', /took too long/]])('%s GPS is explained', async (mode, text) => {
    await open(mode)
    expect(screen.getByText(text)).toBeInTheDocument()
    expect(calls()).toHaveLength(0)
  })

  it('a typed place works when GPS does not — and then no clock offset is sent', async () => {
    mock.onGet('/maps/suggest').reply(200, { results: [{ name: 'Pune, Maharashtra, India', lat: 18.5204, lng: 73.8567, demo: false }] })
    await open('denied')
    fireEvent.change(screen.getByPlaceholderText('Search a city or area…'), { target: { value: 'Pune' } })
    await advance(500)
    fireEvent.click(screen.getByText('Pune', { exact: false }))
    await settle()
    expect(calls()[0].params).toMatchObject({ lat: 18.5204, lng: 73.8567, category: 'hospital' })
    expect(calls()[0].params.tz_offset_min).toBeUndefined()
    expect(screen.getByTestId('discovery-area')).toHaveTextContent('Searching near')
    expect(names().length).toBeGreaterThan(0)
  })
})

describe('Nearby — categories', () => {
  it.each([
    ['pharmacy', ['Day Chemist', 'Closed Chemist', 'Mystery Medicals', 'No Hours Store']],
    ['police', ['CP Police Station', 'Janpath Police Post']],
    ['transport', ['Rajiv Chowk', 'CP Bus Stop']],
  ])('%s fetches and shows only its own kind', async (cat, expected) => {
    await open()
    fireEvent.click(screen.getByTestId(`cat-${cat}`))
    expect(screen.getByTestId(`cat-${cat}`)).toHaveAttribute('aria-pressed', 'true')
    await settle()
    expect(calls().pop().params.category).toBe(cat)
    expect(names()).toEqual(expected)
    for (const row of screen.getAllByTestId('nearby-place')) expect(row).toHaveAttribute('data-category', cat)
  })

  it('switching back to a loaded category is instant (no new request) and counts appear on the chips', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-police'))
    await settle()
    const n = calls().length
    fireEvent.click(screen.getByTestId('cat-hospital'))
    await settle()
    expect(calls()).toHaveLength(n)
    expect(screen.getByTestId('cat-hospital')).toHaveTextContent('(3)')
    expect(screen.getByTestId('cat-police')).toHaveTextContent('(2)')
    expect(screen.getByTestId('cat-pharmacy')).not.toHaveTextContent('(')  // never visited: no count claimed
  })

  it('shows loading skeletons while a category is on its way', async () => {
    await open()
    mock.onGet('/nearby').reply(() => new Promise(() => {}))
    fireEvent.click(screen.getByTestId('cat-pharmacy'))
    await advance(400)
    expect(screen.getByTestId('nearby-skeleton')).toBeInTheDocument()
    expect(screen.queryByTestId('nearby-empty')).toBeNull()
  })

  it('shows how many were found', async () => {
    await open()
    expect(screen.getByTestId('nearby-count')).toHaveTextContent('3 found within 5 km')
  })
})

describe('Nearby — hospital details', () => {
  it('shows distance, type, emergency, government, address, hours, website and a real Call button', async () => {
    await open()
    const city = screen.getAllByTestId('nearby-place').find((e) => e.textContent.includes('City Hospital'))
    expect(within(city).getByTestId('place-distance')).toHaveTextContent('1.2 km')
    expect(city).toHaveTextContent('Emergency hospital')
    expect(within(city).getByTestId('emergency-badge')).toBeInTheDocument()
    expect(city).toHaveTextContent('Government')
    expect(city).toHaveTextContent('Ring Road, Delhi')
    expect(within(city).getByTestId('open-badge')).toHaveTextContent('Open 24 hours')
    expect(within(city).getByTestId('place-call')).toHaveAttribute('href', 'tel:+911123456789')
    expect(within(city).getByTestId('place-call')).toHaveTextContent('+91 11 2345 6789')
    expect(within(city).getByText(/\+91 11 9999 0000/).closest('a')).toHaveAttribute('href', 'tel:+911199990000')
    expect(within(city).getByText(/Website/).closest('a')).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('a hospital with no mapped number has NO call button, and nothing is invented', async () => {
    await open()
    const care = screen.getAllByTestId('nearby-place').find((e) => e.textContent.includes('Care Hospital'))
    expect(within(care).queryByTestId('place-call')).toBeNull()
    expect(care).not.toHaveTextContent(/rating|★|beds|ICU|doctor on duty/i)
    expect(within(care).queryByTestId('open-badge')).toBeNull()
  })

  it('"Emergency first" ranks emergency hospitals ahead of nearer clinics; "Nearest" is by distance', async () => {
    await open()
    expect(names()[0]).toBe('City Hospital')
    fireEvent.change(screen.getByTestId('nearby-sort'), { target: { value: 'nearest' } })
    expect(names()[0]).toBe('Corner Clinic')
  })
})

describe('Nearby — pharmacy hours', () => {
  const pharmacies = async () => { await open(); fireEvent.click(screen.getByTestId('cat-pharmacy')); await settle() }

  it('labels open / closed only where proven, and says nothing where hours are unknown', async () => {
    await pharmacies()
    const row = (n) => screen.getAllByTestId('nearby-place').find((e) => e.textContent.includes(n))
    expect(within(row('Day Chemist')).getByTestId('open-badge')).toHaveTextContent('Open now')
    expect(within(row('Closed Chemist')).getByTestId('open-badge')).toHaveTextContent('Closed now')
    expect(within(row('Mystery Medicals')).queryByTestId('open-badge')).toBeNull()
    expect(within(row('No Hours Store')).queryByTestId('open-badge')).toBeNull()
  })

  it('"only confirmed open" keeps just the proven ones, and explains when none qualify', async () => {
    await pharmacies()
    fireEvent.click(screen.getByTestId('open-only'))
    expect(names()).toEqual(['Day Chemist'])
    mock.onGet('/nearby').reply(replyWith({ pharmacy: [DATA.pharmacy[1], DATA.pharmacy[3]] }))
    fireEvent.click(screen.getByTestId('radius-10'))
    await settle()
    expect(screen.getByTestId('nearby-empty')).toHaveTextContent('None of these are confirmed open')
    fireEvent.click(screen.getByText('Show all'))
    expect(names()).toHaveLength(2)
  })

  it('for a typed place the open-now filter is replaced by an honest note', async () => {
    mock.onGet('/maps/suggest').reply(200, { results: [{ name: 'Pune, India', lat: 18.5, lng: 73.8 }] })
    await open('denied')
    fireEvent.change(screen.getByPlaceholderText('Search a city or area…'), { target: { value: 'Pune' } })
    await advance(500)
    fireEvent.click(screen.getByText('Pune', { exact: false }))
    await settle()
    fireEvent.click(screen.getByTestId('cat-pharmacy'))
    await settle()
    expect(screen.queryByTestId('open-only')).toBeNull()
    expect(screen.getByTestId('open-unknown-note')).toBeInTheDocument()
  })
})

describe('Nearby — police and transport', () => {
  it('police: kind, mapped number only, nearest first', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-police'))
    await settle()
    const [station, post] = screen.getAllByTestId('nearby-place')
    expect(station).toHaveTextContent('Police station')
    expect(within(station).getByTestId('place-call')).toHaveAttribute('href', 'tel:+911123340000')
    expect(post).toHaveTextContent('Police post')
    expect(within(post).queryByTestId('place-call')).toBeNull() // no number mapped, none shown
  })

  it('transport: type, operator/network/lines, and no claim that anything is running', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-transport'))
    await settle()
    const metro = screen.getAllByTestId('nearby-place')[0]
    expect(metro).toHaveTextContent('Metro station')
    expect(within(metro).getByTestId('transport-details')).toHaveTextContent('Operator: DMRC')
    expect(within(metro).getByTestId('transport-details')).toHaveTextContent('Lines / routes: Blue, Yellow')
    expect(screen.getByTestId('transport-note')).toHaveTextContent(/Timetables and live running status are not available/)
    expect(screen.getByTestId('nearest-badge').closest('[data-testid="nearby-place"]')).toBe(metro)
    expect(document.body).not.toHaveTextContent(/arriving|next train|departs/i)
  })
})

describe('Nearby — radius, search, nearest', () => {
  it('offers 1, 2, 5, 10, 25 and 50 km and re-searches once for the final choice', async () => {
    await open()
    for (const r of [1, 2, 5, 10, 25, 50]) expect(screen.getByTestId(`radius-${r}`)).toBeInTheDocument()
    const before = calls().length
    fireEvent.click(screen.getByTestId('radius-1'))
    fireEvent.click(screen.getByTestId('radius-2'))
    fireEvent.click(screen.getByTestId('radius-25'))
    await settle()
    expect(calls().slice(before)).toHaveLength(1)
    expect(calls().pop().params.radius_km).toBe(25)
  })

  it('changing the radius drops other categories (they were for a different radius)', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-police'))
    await settle()
    fireEvent.click(screen.getByTestId('radius-10'))
    await settle()
    expect(screen.getByTestId('cat-hospital')).not.toHaveTextContent('(3)')
    fireEvent.click(screen.getByTestId('cat-hospital'))
    await settle()
    expect(calls().pop().params).toMatchObject({ category: 'hospital', radius_km: 10 })
  })

  it('searches the results by name or type', async () => {
    await open()
    fireEvent.change(screen.getByTestId('nearby-search'), { target: { value: 'clinic' } })
    expect(names()).toEqual(['Corner Clinic'])
    fireEvent.change(screen.getByTestId('nearby-search'), { target: { value: 'zzz' } })
    expect(screen.getByTestId('nearby-empty')).toHaveTextContent('No results match “zzz”')
  })

  it('"Nearest hospital" / "Nearest police" jump there, order by distance and highlight the nearest', async () => {
    await open()
    fireEvent.click(screen.getByTestId('nearest-police'))
    await settle()
    expect(screen.getByTestId('cat-police')).toHaveAttribute('aria-pressed', 'true')
    const first = screen.getAllByTestId('nearby-place')[0]
    expect(first).toHaveTextContent('CP Police Station')
    expect(within(first).getByTestId('nearest-badge')).toBeInTheDocument()
    expect(first).toHaveAttribute('aria-current', 'true')
    fireEvent.click(screen.getByTestId('nearest-hospital'))
    await settle()
    expect(names()[0]).toBe('Corner Clinic') // by distance, even though City Hospital has an emergency department
    expect(screen.getAllByTestId('nearby-place')[0]).toHaveTextContent('Nearest')
  })

  it('an emergency call link to the national number is always on screen', async () => {
    await open('denied')
    expect(screen.getByTestId('emergency-call')).toHaveAttribute('href', 'tel:112')
  })
})

describe('Nearby — map', () => {
  it('shows the user and each place with the right icon for the category', async () => {
    await open()
    fireEvent.click(screen.getByTestId('view-map'))
    const pins = screen.getAllByTestId('marker')
    expect(pins.find((m) => m.dataset.glyph === '📍').dataset.pos).toBe(`${here.lat},${here.lng}`)
    expect(pins.filter((m) => m.dataset.glyph === '🏥')).toHaveLength(3)
    fireEvent.click(screen.getByTestId('cat-pharmacy'))
    await settle()
    expect(screen.getAllByTestId('marker').filter((m) => m.dataset.glyph === '💊')).toHaveLength(4)
    expect(screen.queryAllByTestId('marker').filter((m) => m.dataset.glyph === '🏥')).toHaveLength(0)
    fireEvent.click(screen.getByTestId('cat-police'))
    await settle()
    expect(screen.getAllByTestId('marker').filter((m) => m.dataset.glyph === '👮')).toHaveLength(2)
    fireEvent.click(screen.getByTestId('cat-transport'))
    await settle()
    expect(screen.getAllByTestId('marker').filter((m) => m.dataset.glyph === '🚕')).toHaveLength(2)
  })

  it('a pin shows details with Call (when a number exists) and Directions', async () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({})
    await open()
    fireEvent.click(screen.getByTestId('view-map'))
    const popups = screen.getAllByTestId('map-popup')
    const city = popups.find((p) => p.textContent.includes('City Hospital'))
    expect(within(city).getByTestId('popup-call')).toHaveAttribute('href', 'tel:+911123456789')
    const care = popups.find((p) => p.textContent.includes('Care Hospital'))
    expect(within(care).queryByTestId('popup-call')).toBeNull()
    fireEvent.click(within(city).getByTestId('popup-directions'))
    const [dlat, dlng] = new URL(openSpy.mock.calls[0][0]).searchParams.get('destination').split(',').map(Number)
    expect(dlat).toBeCloseTo(28.632, 4)
    expect(dlng).toBeCloseTo(77.212, 3)
  })

  it('the map follows the category and radius', async () => {
    await open()
    fireEvent.click(screen.getByTestId('view-map'))
    fireEvent.click(screen.getByTestId('radius-10'))
    await settle()
    expect(calls().pop().params.radius_km).toBe(10)
    expect(screen.getByTestId('discovery-map')).toBeInTheDocument()
  })
})

describe('Nearby — directions and route', () => {
  it('Directions opens turn-by-turn from the live position (walking close, driving far)', async () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({})
    await open()
    const row = (n) => screen.getAllByTestId('nearby-place').find((e) => e.textContent.includes(n))
    fireEvent.click(within(row('City Hospital')).getByTestId('place-directions')) // 1.2 km
    fireEvent.click(within(row('Care Hospital')).getByTestId('place-directions')) // 2.5 km
    const near = new URL(openSpy.mock.calls[0][0]).searchParams
    const far = new URL(openSpy.mock.calls[1][0]).searchParams
    expect(near.get('origin')).toBe(`${here.lat},${here.lng}`)
    expect(near.get('travelmode')).toBe('walking')
    expect(far.get('travelmode')).toBe('driving')
  })

  it('for a typed place, Directions leaves the start to Maps', async () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({})
    mock.onGet('/maps/suggest').reply(200, { results: [{ name: 'Goa, India', lat: 15.3, lng: 74.1 }] })
    await open('denied')
    fireEvent.change(screen.getByPlaceholderText('Search a city or area…'), { target: { value: 'Goa' } })
    await advance(500)
    fireEvent.click(screen.getByText('Goa', { exact: false }))
    await settle()
    fireEvent.click(screen.getAllByTestId('place-directions')[0])
    expect(new URL(openSpy.mock.calls[0][0]).searchParams.get('origin')).toBeNull()
  })

  it('"Time & route" shows real distance and duration, and can switch mode', async () => {
    mock.onGet('/maps/route').reply((c) => [200, { distance_km: 1.4, duration_min: c.params.mode === 'driving' ? 6 : 18, demo: false, provider: 'osrm',
      geometry: [[28.63, 77.21], [28.632, 77.211]] }])
    await open()
    const city = screen.getAllByTestId('nearby-place')[0]
    fireEvent.click(within(city).getByTestId('place-route'))
    await flush()
    await flush()
    const first = mock.history.get.find((c) => c.url === '/maps/route')
    expect(first.params).toMatchObject({ olat: here.lat, olng: here.lng, mode: 'walking' })
    expect(within(city).getByTestId('route-result')).toHaveTextContent('≈ 18 min on foot · 1.4 km')
    fireEvent.click(within(city).getByTestId('route-mode-driving'))
    await flush()
    await flush()
    expect(within(city).getByTestId('route-result')).toHaveTextContent('≈ 6 min by car · 1.4 km')
  })

  it('a straight-line estimate is labelled as one, and a failed route says so', async () => {
    mock.onGet('/maps/route').replyOnce(200, { distance_km: 1.2, duration_min: 3, demo: true, provider: 'estimate', geometry: null })
    await open()
    const city = screen.getAllByTestId('nearby-place')[0]
    fireEvent.click(within(city).getByTestId('place-route'))
    await flush(); await flush()
    expect(within(city).getByTestId('route-result')).toHaveTextContent(/Straight-line estimate/)
    mock.onGet('/maps/route').reply(502, {})
    fireEvent.click(within(city).getByTestId('route-mode-driving'))
    await flush(); await flush()
    expect(within(city).getByRole('alert')).toHaveTextContent(/Could not calculate the route/)
  })

  it('draws the road route on the map for the selected place', async () => {
    mock.onGet('/maps/route').reply(200, { distance_km: 1.4, duration_min: 9, demo: false, provider: 'osrm', geometry: [[28.63, 77.21], [28.631, 77.211], [28.632, 77.211]] })
    await open()
    fireEvent.click(within(screen.getAllByTestId('nearby-place')[0]).getByTestId('place-route'))
    await flush(); await flush()
    fireEvent.click(within(screen.getAllByTestId('nearby-place')[0]).getByTestId('place-on-map'))
    expect(screen.getByTestId('route-line')).toHaveAttribute('data-n', '3')
  })
})

describe('Nearby — empty, failing and offline', () => {
  it('a genuine empty result names the radius and offers to widen it', async () => {
    mock.onGet('/nearby').reply(replyWith({ hospital: [] }))
    await open()
    expect(screen.getByTestId('nearby-empty')).toHaveTextContent('No hospital found within 5 km')
    expect(screen.queryByTestId('nearby-error')).toBeNull()
    fireEvent.click(screen.getByTestId('expand-radius'))
    await settle()
    expect(calls().pop().params.radius_km).toBe(10)
  })

  it('an outage (503) is an ERROR with a retry — never "nothing found"', async () => {
    mock.onGet('/nearby').reply(503, { detail: 'Nearby search is unavailable right now.' })
    await open()
    expect(screen.getByTestId('nearby-error')).toHaveTextContent(/busy or unreachable/)
    expect(screen.getByTestId('nearby-error')).toHaveTextContent(/call the emergency number/)
    expect(screen.queryByTestId('nearby-empty')).toBeNull()
    mock.onGet('/nearby').reply(replyWith())
    fireEvent.click(screen.getByTestId('nearby-retry'))
    await settle()
    expect(screen.queryByTestId('nearby-error')).toBeNull()
    expect(names().length).toBe(3)
  })

  it('no connection to the server is reported as such', async () => {
    mock.onGet('/nearby').networkError()
    await open()
    expect(screen.getByTestId('nearby-error')).toHaveTextContent(/Check your internet connection/)
    expect(screen.queryByTestId('nearby-empty')).toBeNull()
  })

  it('tells the tourist when the browser itself is offline', async () => {
    await open()
    await act(async () => { window.dispatchEvent(new Event('offline')) })
    expect(screen.getByTestId('nearby-offline')).toBeInTheDocument()
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(screen.queryByTestId('nearby-offline')).toBeNull()
  })

  it('labels the backup service, an old copy, and the saved last-resort data', async () => {
    mock.onGet('/nearby').reply(replyWith({ provider: 'nominatim', degraded: true }))
    const first = await open()
    expect(screen.getByTestId('nearby-degraded')).toBeInTheDocument()
    first.unmount()
    mock.onGet('/nearby').reply(replyWith({ provider: 'saved', degraded: true, stale: true,
      hospital: [P(50, 'hospital', 'Saved General Hospital', 1.0, { source: 'saved_osm' })] }))
    await open()
    expect(screen.getByTestId('nearby-saved')).toHaveTextContent(/saved copy.*out of date.*no phone numbers/i)
    expect(screen.getByText(/From a saved OpenStreetMap copy/)).toBeInTheDocument()
    expect(screen.queryByTestId('nearby-stale')).toBeNull()
  })

  it('Refresh takes a fresh position and bypasses the server cache', async () => {
    await open()
    const gpsCalls = geo.getCurrentPosition.mock.calls.length
    fireEvent.click(screen.getByTestId('nearby-refresh'))
    await settle()
    expect(geo.getCurrentPosition.mock.calls.length).toBe(gpsCalls + 1)
    expect(calls().pop().params.refresh).toBe(true)
  })

  it('pages very long lists', async () => {
    const many = Array.from({ length: 35 }, (_, i) => P(100 + i, 'hospital', `Clinic ${i}`, 0.1 + i / 20))
    mock.onGet('/nearby').reply(replyWith({ hospital: many }))
    await open()
    expect(screen.getAllByTestId('nearby-place')).toHaveLength(30)
    fireEvent.click(screen.getByTestId('nearby-more'))
    expect(screen.getAllByTestId('nearby-place')).toHaveLength(35)
  })
})
