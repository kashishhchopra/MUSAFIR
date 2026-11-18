import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, act, within } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import i18n from '../i18n'
import DiscoveryCard from './DiscoveryCard.jsx'

vi.mock('react-leaflet', async () => {
  const React = await import('react')
  const glyph = (icon) => icon?.options?.html?.match(/💎|🍲|🏡|📍/u)?.[0]
  return {
    MapContainer: ({ children }) => <div data-testid="leaflet">{children}</div>,
    TileLayer: () => null,
    Marker: React.forwardRef(({ position, icon, children, eventHandlers }, ref) => {
      React.useImperativeHandle(ref, () => ({ openPopup: () => {} }))
      return (
        <div data-testid="marker" data-pos={position.join(',')} data-glyph={glyph(icon)}
          onClick={() => eventHandlers?.click?.()}>{children}</div>
      )
    }),
    Popup: ({ children }) => <div data-testid="popup">{children}</div>,
    useMap: () => ({ setView: () => {}, fitBounds: () => {}, getZoom: () => 13 }),
  }
})

const mock = new MockAdapter(api)

const here = { lat: 28.6315, lng: 77.2167 }
const P = (id, category, name, km, extra = {}) => ({
  id: `osm:node/${id}`, osm_url: `https://www.openstreetmap.org/node/${id}`, name, category,
  kind: { hidden_gem: 'Viewpoint', regional_food: 'Restaurant', homestay: 'Guest house' }[category],
  lat: 28.63 + id / 1000, lng: 77.21 + id / 1000, distance_km: km, address: `${name} Road`,
  description: null, cuisine: null, phone: null, website: null, opening_hours: null,
  regional_match: null, match_label: null, tip: '', source: 'osm', ...extra,
})
const DATA = {
  hidden_gem: [P(1, 'hidden_gem', 'Sunset Point', 0.4), P(2, 'hidden_gem', 'Old Baoli', 3.2, { description: 'A stepwell' })],
  regional_food: [
    P(3, 'regional_food', 'Punjabi Dhaba', 0.9, { cuisine: 'Punjabi, Indian', regional_match: 'region', match_label: 'punjabi' }),
    P(4, 'regional_food', 'Old Bakery', 6, { kind: 'Bakery' }),
  ],
  homestay: [P(5, 'homestay', 'Sharma Homestay', 1.5, { phone: '+91 98100 00000', website: 'http://sharma.example.com' })],
}
const REGION = { name: 'Connaught Place, New Delhi, Delhi', country_code: 'IN', cuisine_scope: 'Delhi' }

const replyWith = (over = {}) => (cfg) => {
  const cat = cfg.params.categories
  const radius = cfg.params.radius_km
  const data = over[cat] ?? DATA[cat] ?? []
  return [200, { lat: cfg.params.lat, lng: cfg.params.lng, radius_km: radius, places: data, counts: { [cat]: data.length },
    provider: over.provider || 'overpass', degraded: !!over.degraded, stale: !!over.stale, cached: false,
    region: cat === 'regional_food' ? REGION : null }]
}
const discoveryCalls = () => mock.history.get.filter((c) => c.url === '/discovery')

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
const settle = async () => { await advance(400); await flush(); await flush() }

let geo
const installGeo = (mode = 'ok') => {
  geo = {
    getCurrentPosition: vi.fn((ok, fail) => {
      if (mode === 'ok') ok({ coords: { latitude: here.lat, longitude: here.lng, accuracy: 20 }, timestamp: Date.now() })
      else if (mode === 'denied') fail({ code: 1 })
      else if (mode === 'unavailable') fail({ code: 2 })
      else if (mode === 'timeout') fail({ code: 3 })
      // 'hang': never answers
    }),
  }
  Object.defineProperty(globalThis.navigator, 'geolocation', { value: geo, configurable: true })
}
const open = async (mode = 'ok') => {
  installGeo(mode)
  const utils = render(<DiscoveryCard />)
  await flush()
  await settle()
  return utils
}
const places = () => screen.queryAllByTestId('discovery-place').map((e) => within(e).getAllByText(/./)[0].textContent.replace(/^\S+\s/u, ''))

beforeAll(() => i18n.changeLanguage('en'))
beforeEach(() => {
  mock.reset()
  try { localStorage.clear() } catch { /* ignore */ }
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
  mock.onGet('/discovery').reply(replyWith())
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('Discovery — finding the user', () => {
  it('asks the device where it is and searches around THAT position, in all three categories', async () => {
    await open()
    expect(geo.getCurrentPosition).toHaveBeenCalled()
    const calls = discoveryCalls()
    expect(calls.map((c) => c.params.categories).sort()).toEqual(['hidden_gem', 'homestay', 'regional_food'])
    for (const c of calls) {
      expect(c.params).toMatchObject({ lat: here.lat, lng: here.lng, radius_km: 10 })
    }
    expect(screen.getByTestId('discovery-area')).toHaveTextContent('Connaught Place, New Delhi, Delhi')
    expect(places()).toEqual(expect.arrayContaining(['Sunset Point', 'Punjabi Dhaba', 'Sharma Homestay']))
    expect(screen.queryByText(/No places found/)).toBeNull()
  })

  it('lists places nearest first with real distances, kind and address', async () => {
    await open()
    const rows = screen.getAllByTestId('discovery-place')
    expect(rows.map((r) => within(r).getByTestId('place-distance').textContent))
      .toEqual(['400 m', '900 m', '1.5 km', '3.2 km', '6.0 km'])
    expect(rows[0]).toHaveTextContent('Viewpoint')
    expect(rows[0]).toHaveTextContent('Sunset Point Road')
  })

  it('shows only what OpenStreetMap holds: no invented description, rating or availability', async () => {
    await open()
    const sunset = screen.getAllByTestId('discovery-place')[0]
    expect(sunset).not.toHaveTextContent(/rating|★|available|book now|₹/i)
    expect(screen.getByText('A stepwell')).toBeInTheDocument() // present in the data -> shown
    expect(within(sunset).queryByText(/Cuisine/)).toBeNull()
  })

  it('explains when location permission is refused — and does NOT claim there is nothing nearby', async () => {
    await open('denied')
    expect(screen.getByText(/Location permission is off/)).toBeInTheDocument()
    expect(screen.queryByText(/No places found/)).toBeNull()
    expect(discoveryCalls()).toHaveLength(0) // nothing is searched without a real position
    expect(screen.getByPlaceholderText('Search a city or area…')).toBeInTheDocument() // manual option offered
  })

  it.each([['unavailable', /couldn't be determined/], ['timeout', /took too long/]])('explains a %s location failure', async (mode, text) => {
    await open(mode)
    expect(screen.getByText(text)).toBeInTheDocument()
    expect(discoveryCalls()).toHaveLength(0)
  })

  it('says so when the device has no geolocation at all', async () => {
    Object.defineProperty(globalThis.navigator, 'geolocation', { value: undefined, configurable: true })
    delete globalThis.navigator.geolocation
    render(<DiscoveryCard />)
    await flush()
    expect(screen.getByText(/can't provide a location/)).toBeInTheDocument()
  })

  it('lets the tourist pick a place by name when GPS is unavailable, then searches around it', async () => {
    mock.onGet('/maps/suggest').reply(200, { results: [{ name: 'Jaipur, Rajasthan, India', lat: 26.9124, lng: 75.7873, demo: false }] })
    await open('denied')
    fireEvent.change(screen.getByPlaceholderText('Search a city or area…'), { target: { value: 'Jaipur' } })
    await advance(500)
    fireEvent.click(screen.getByText('Jaipur', { exact: false }))
    await settle()
    expect(discoveryCalls().every((c) => c.params.lat === 26.9124 && c.params.lng === 75.7873)).toBe(true)
    expect(screen.getByTestId('discovery-area')).toHaveTextContent('Jaipur, Rajasthan')
    expect(screen.getByTestId('discovery-area')).toHaveTextContent('Searching near')
    expect(places().length).toBeGreaterThan(0)
  })

  it('can switch from a typed place back to the live position', async () => {
    mock.onGet('/maps/suggest').reply(200, { results: [{ name: 'Goa, India', lat: 15.3, lng: 74.1 }] })
    await open()
    fireEvent.click(screen.getByTestId('change-location'))
    fireEvent.change(screen.getByPlaceholderText('Search a city or area…'), { target: { value: 'Goa' } })
    await advance(500)
    fireEvent.click(screen.getByText('Goa', { exact: false }))
    await settle()
    expect(discoveryCalls().pop().params.lat).toBe(15.3)
    fireEvent.click(screen.getByTestId('use-my-location'))
    await settle()
    expect(discoveryCalls().pop().params.lat).toBe(here.lat)
  })
})

describe('Discovery — categories', () => {
  it('shows a count on every filter', async () => {
    await open()
    expect(screen.getByTestId('cat-all')).toHaveTextContent('All (5)')
    expect(screen.getByTestId('cat-hidden_gem')).toHaveTextContent('Hidden gems (2)')
    expect(screen.getByTestId('cat-regional_food')).toHaveTextContent('Regional food (2)')
    expect(screen.getByTestId('cat-homestay')).toHaveTextContent('Homestays (1)')
  })

  it.each([
    ['hidden_gem', ['Sunset Point', 'Old Baoli']],
    ['regional_food', ['Punjabi Dhaba', 'Old Bakery']],
    ['homestay', ['Sharma Homestay']],
  ])('%s shows only its own places', async (cat, names) => {
    await open()
    fireEvent.click(screen.getByTestId(`cat-${cat}`))
    expect(screen.getByTestId(`cat-${cat}`)).toHaveAttribute('aria-pressed', 'true')
    expect(places()).toEqual(names)
    for (const row of screen.getAllByTestId('discovery-place')) expect(row).toHaveAttribute('data-category', cat)
  })

  it('All shows everything again, and switching needs no new request', async () => {
    await open()
    const before = discoveryCalls().length
    fireEvent.click(screen.getByTestId('cat-homestay'))
    fireEvent.click(screen.getByTestId('cat-all'))
    expect(places()).toHaveLength(5)
    expect(discoveryCalls()).toHaveLength(before)
  })

  it('marks regional matches on food', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-regional_food'))
    const dhaba = screen.getAllByTestId('discovery-place')[0]
    expect(dhaba).toHaveTextContent('Cuisine: Punjabi, Indian')
    expect(within(dhaba).getByTestId('match-badge')).toHaveTextContent('Local to this region')
  })

  it('carries the contact details a homestay actually has, and no availability claim', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-homestay'))
    const stay = screen.getByTestId('discovery-place')
    expect(within(stay).getByText(/98100 00000/).closest('a')).toHaveAttribute('href', 'tel:+919810000000')
    expect(within(stay).getByText(/Website/).closest('a')).toHaveAttribute('href', 'http://sharma.example.com')
    expect(within(stay).getByText(/Website/).closest('a')).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByTestId('stay-disclaimer')).toHaveTextContent(/not confirmed/)
  })
})

describe('Discovery — radius, sort, search', () => {
  it('re-searches at the chosen radius, once (rapid taps are debounced)', async () => {
    await open()
    const before = discoveryCalls().length
    fireEvent.click(screen.getByTestId('radius-2'))
    fireEvent.click(screen.getByTestId('radius-5'))
    fireEvent.click(screen.getByTestId('radius-25'))
    await settle()
    const fresh = discoveryCalls().slice(before)
    expect(fresh).toHaveLength(3) // one per category, for the final radius only
    expect(fresh.every((c) => c.params.radius_km === 25)).toBe(true)
    expect(screen.getByTestId('radius-25')).toHaveAttribute('aria-pressed', 'true')
  })

  it('offers 2, 5, 10, 25 and 50 km and remembers the choice', async () => {
    const first = await open()
    for (const r of [2, 5, 10, 25, 50]) expect(screen.getByTestId(`radius-${r}`)).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('radius-50'))
    await settle()
    first.unmount()
    await open()
    expect(screen.getByTestId('radius-50')).toHaveAttribute('aria-pressed', 'true')
  })

  it('sorts by name when asked', async () => {
    await open()
    fireEvent.change(screen.getByTestId('discovery-sort'), { target: { value: 'name' } })
    expect(places()).toEqual(['Old Bakery', 'Old Baoli', 'Punjabi Dhaba', 'Sharma Homestay', 'Sunset Point'])
  })

  it('"best local match" puts regional food first', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-regional_food'))
    fireEvent.change(screen.getByTestId('discovery-sort'), { target: { value: 'match' } })
    expect(places()[0]).toBe('Punjabi Dhaba')
  })

  it('searches the results by name or type, and explains an empty search', async () => {
    await open()
    fireEvent.change(screen.getByTestId('discovery-search'), { target: { value: 'bakery' } })
    expect(places()).toEqual(['Old Bakery'])
    fireEvent.change(screen.getByTestId('discovery-search'), { target: { value: 'zzzz' } })
    expect(screen.getByTestId('discovery-empty')).toHaveTextContent('No places match “zzzz”')
    fireEvent.click(screen.getByText('Clear search'))
    expect(places()).toHaveLength(5)
  })
})

describe('Discovery — map and directions', () => {
  it('shows the user and every place on a map, with a different pin per category', async () => {
    await open()
    fireEvent.click(screen.getByTestId('view-map'))
    const pins = screen.getAllByTestId('marker')
    expect(pins.find((m) => m.dataset.glyph === '📍').dataset.pos).toBe(`${here.lat},${here.lng}`)
    expect(pins.filter((m) => m.dataset.glyph === '💎')).toHaveLength(2)
    expect(pins.filter((m) => m.dataset.glyph === '🍲')).toHaveLength(2)
    expect(pins.filter((m) => m.dataset.glyph === '🏡')).toHaveLength(1)
  })

  it('the map follows the active filter', async () => {
    await open()
    fireEvent.click(screen.getByTestId('cat-homestay'))
    fireEvent.click(screen.getByTestId('view-map'))
    expect(screen.getAllByTestId('marker').filter((m) => m.dataset.glyph !== '📍')).toHaveLength(1)
  })

  it('tapping a pin shows its details and a Directions button', async () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({})
    await open()
    fireEvent.click(screen.getByTestId('view-map'))
    const popup = screen.getAllByTestId('map-popup')[0]
    expect(popup).toHaveTextContent('Sunset Point')
    expect(popup).toHaveTextContent('Viewpoint · 400 m')
    fireEvent.click(within(popup).getByTestId('popup-directions'))
    const url = new URL(openSpy.mock.calls[0][0])
    expect(url.host).toBe('www.google.com')
    expect(url.searchParams.get('destination')).toBe('28.631,77.211')
  })

  it('"On map" jumps from a card to the map view', async () => {
    await open()
    fireEvent.click(screen.getAllByTestId('place-on-map')[0])
    expect(screen.getByTestId('discovery-map')).toBeInTheDocument()
    expect(screen.getByTestId('view-map')).toHaveAttribute('aria-pressed', 'true')
  })

  it('Directions: from the live position, walking when close and driving when far', async () => {
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({})
    await open()
    const rows = screen.getAllByTestId('discovery-place')
    fireEvent.click(within(rows[0]).getByTestId('place-directions')) // 0.4 km
    fireEvent.click(within(rows[3]).getByTestId('place-directions')) // 3.2 km
    const near = new URL(openSpy.mock.calls[0][0]).searchParams
    const far = new URL(openSpy.mock.calls[1][0]).searchParams
    expect(near.get('origin')).toBe(`${here.lat},${here.lng}`)
    expect(near.get('travelmode')).toBe('walking')
    expect(far.get('travelmode')).toBe('driving')
  })

  it('for a typed location, Directions leaves the start to Maps (the tourist is not standing there)', async () => {
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
})

describe('Discovery — empty, failing and offline', () => {
  it('a genuine empty result says so, names the radius, and offers to widen it', async () => {
    mock.onGet('/discovery').reply(replyWith({ hidden_gem: [], regional_food: [], homestay: [] }))
    await open()
    expect(screen.getByTestId('discovery-empty')).toHaveTextContent('No places found within 10 km')
    expect(screen.queryByTestId('discovery-error')).toBeNull()
    fireEvent.click(screen.getByTestId('expand-radius'))
    await settle()
    expect(discoveryCalls().pop().params.radius_km).toBe(25)
  })

  it('an empty category explains which one', async () => {
    mock.onGet('/discovery').reply(replyWith({ homestay: [] }))
    await open()
    fireEvent.click(screen.getByTestId('cat-homestay'))
    expect(screen.getByTestId('discovery-empty')).toHaveTextContent('No homestays found within 10 km')
  })

  it('a map-service outage (503) is an ERROR with a retry — never "no places found"', async () => {
    mock.onGet('/discovery').reply(503, { detail: 'Place search is unavailable right now.' })
    await open()
    expect(screen.getByTestId('discovery-error')).toHaveTextContent(/busy or unreachable/)
    expect(screen.queryByTestId('discovery-empty')).toBeNull()
    expect(screen.queryByText(/No places found/)).toBeNull()
    mock.onGet('/discovery').reply(replyWith())
    fireEvent.click(screen.getByTestId('discovery-retry'))
    await settle()
    expect(screen.queryByTestId('discovery-error')).toBeNull()
    expect(places().length).toBe(5)
  })

  it('no connection to the server is reported as such', async () => {
    mock.onGet('/discovery').networkError()
    await open()
    expect(screen.getByTestId('discovery-error')).toHaveTextContent(/Check your internet connection/)
    expect(screen.queryByTestId('discovery-empty')).toBeNull()
  })

  it('when only one category fails, the others still show and the failure is named', async () => {
    mock.onGet('/discovery').reply((cfg) => (cfg.params.categories === 'homestay' ? [503, {}] : replyWith()(cfg)))
    await open()
    expect(places()).toEqual(expect.arrayContaining(['Sunset Point', 'Punjabi Dhaba']))
    expect(screen.getByTestId('discovery-partial')).toHaveTextContent('Could not load: Homestays')
    expect(screen.getByTestId('cat-homestay')).not.toHaveTextContent('(')
  })

  it('tells the tourist when the browser itself is offline', async () => {
    await open()
    await act(async () => { window.dispatchEvent(new Event('offline')) })
    expect(screen.getByTestId('discovery-offline')).toBeInTheDocument()
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(screen.queryByTestId('discovery-offline')).toBeNull()
  })

  it('says when the backup map service answered, and when a saved copy is shown', async () => {
    mock.onGet('/discovery').reply(replyWith({ provider: 'nominatim', degraded: true, stale: true }))
    await open()
    expect(screen.getByTestId('discovery-degraded')).toBeInTheDocument()
    expect(screen.getByTestId('discovery-stale')).toBeInTheDocument()
  })

  it('shows loading skeletons while the first results are on their way', async () => {
    mock.onGet('/discovery').reply(() => new Promise(() => {})) // never answers
    installGeo()
    render(<DiscoveryCard />)
    await flush()
    await advance(400)
    expect(screen.getByTestId('discovery-skeleton')).toBeInTheDocument()
    expect(screen.queryByText(/No places found/)).toBeNull()
  })

  it('Refresh asks for a fresh position and bypasses the server cache', async () => {
    await open()
    const gpsCalls = geo.getCurrentPosition.mock.calls.length
    fireEvent.click(screen.getByTestId('discovery-refresh'))
    await settle()
    expect(geo.getCurrentPosition.mock.calls.length).toBe(gpsCalls + 1)
    expect(discoveryCalls().slice(-3).every((c) => c.params.refresh === true)).toBe(true)
  })

  it('pages long lists instead of rendering everything', async () => {
    const many = Array.from({ length: 35 }, (_, i) => P(100 + i, 'hidden_gem', `Spot ${i}`, 1 + i / 10))
    mock.onGet('/discovery').reply(replyWith({ hidden_gem: many, regional_food: [], homestay: [] }))
    await open()
    expect(screen.getAllByTestId('discovery-place')).toHaveLength(30)
    fireEvent.click(screen.getByTestId('discovery-more'))
    expect(screen.getAllByTestId('discovery-place')).toHaveLength(35)
  })
})
