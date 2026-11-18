import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import i18n from '../i18n'
import LocationStatus from './LocationStatus.jsx'
import useCurrentLocation from '../hooks/useCurrentLocation.js'
import { CACHE_KEY } from '../lib/deviceLocation'

const here = { lat: 28.6315, lng: 77.2167 }
let geo
const install = (impl) => {
  geo = { getCurrentPosition: vi.fn(impl) }
  Object.defineProperty(globalThis.navigator, 'geolocation', { value: geo, configurable: true })
}
const ok = (ok_) => ok_({ coords: { latitude: here.lat, longitude: here.lng, accuracy: 20 }, timestamp: Date.now() })
const hang = () => {}
const perm = (state) => {
  const p = { state, onchange: null }
  Object.defineProperty(globalThis.navigator, 'permissions', { value: { query: vi.fn().mockResolvedValue(p) }, configurable: true })
}
const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

function Probe({ auto = true }) {
  const loc = useCurrentLocation({ auto })
  return <LocationStatus loc={loc} />
}

beforeAll(() => i18n.changeLanguage('en'))
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  vi.setSystemTime(new Date('2026-11-18T12:00:00Z'))
  Object.defineProperty(globalThis.navigator, 'permissions', { value: undefined, configurable: true })
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('LocationStatus — what the tourist sees', () => {
  it('shows the live position once the device answers', async () => {
    install(ok)
    render(<Probe />)
    await flush()
    expect(screen.getByTestId('location-live')).toHaveTextContent('28.6315, 77.2167')
    expect(screen.getByTestId('location-live')).toHaveTextContent('±20 m')
  })

  it('shows a spinner at first, then — if it drags on — says WHY and offers a way out', async () => {
    install(hang)
    render(<Probe />)
    await flush()
    expect(screen.getByText('Getting your location…')).toBeInTheDocument()
    expect(screen.queryByTestId('location-slow')).toBeNull() // not for the first seconds
    await advance(4100)
    expect(screen.getByTestId('location-slow')).toBeInTheDocument()
    expect(screen.getByTestId('use-approx-location')).toBeInTheDocument()
  })

  it('tells you to click Allow when the browser is still waiting for permission', async () => {
    perm('prompt')
    install(hang)
    render(<Probe />)
    await flush()
    await advance(4100)
    expect(screen.getByTestId('location-slow-hint')).toHaveTextContent(/click Allow in the pop-up/)
  })

  it('points at the computer\'s Location Services when the browser is allowed but nothing comes back', async () => {
    perm('granted')
    install(hang)
    render(<Probe />)
    await flush()
    await advance(4100)
    expect(screen.getByTestId('location-slow-hint')).toHaveTextContent(/Location Services/)
    await advance(10_000) // and when it finally gives up, the same hint stays with the timeout message
    expect(screen.getByText(/took too long/)).toBeInTheDocument()
    expect(screen.getByTestId('location-os-hint')).toBeInTheDocument()
  })

  it('refused permission: says so, and does NOT offer a workaround that bypasses the user\'s choice', async () => {
    install((_o, fail) => fail({ code: 1 }))
    render(<Probe />)
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent(/Location permission is off/)
    expect(screen.queryByTestId('use-approx-location')).toBeNull()
  })

  it.each([[2, /couldn't be determined/], [3, /took too long/]])('error %s is explained and offers retry + approximate', async (code, text) => {
    install((_o, fail) => fail({ code }))
    render(<Probe />)
    await flush()
    expect(screen.getByRole('alert')).toHaveTextContent(text)
    expect(screen.getByTestId('location-retry')).toBeInTheDocument()
    expect(screen.getByTestId('use-approx-location')).toBeInTheDocument()
  })

  it('"Try again" asks the device again', async () => {
    install((_o, fail) => fail({ code: 2 }))
    render(<Probe />)
    await flush()
    install(ok)
    fireEvent.click(screen.getByTestId('location-retry'))
    await flush()
    expect(screen.getByTestId('location-live')).toBeInTheDocument()
  })
})

describe('LocationStatus — last known position (instant start)', () => {
  it('uses the remembered position straight away, labelled with its age, and says it is updating', async () => {
    // A fresh copy of the store, as after reloading the app, with a position remembered 40 minutes ago.
    vi.resetModules()
    localStorage.setItem(CACHE_KEY, JSON.stringify({ ...here, accuracy: 30, timestamp: Date.now() - 40 * 60_000 }))
    const store = await import('../lib/deviceLocation')
    const { default: useLoc } = await import('../hooks/useCurrentLocation.js')
    const { default: Status } = await import('./LocationStatus.jsx')
    install(hang) // the device has not answered yet
    function Fresh() { return <Status loc={useLoc({ auto: true })} /> }
    render(<Fresh />)
    await flush()
    expect(screen.getByTestId('location-stale')).toHaveTextContent('Using your last known location (40 min ago)')
    expect(screen.getByTestId('location-stale')).toHaveTextContent('updating')
    store.resetDeviceLocation() // drop this copy's listeners
  })
})

describe('LocationStatus — approximate location', () => {
  const geojs = { latitude: '28.6542', longitude: '77.2373', accuracy: 500, city: 'Delhi', country: 'India' }
  const okJson = (j) => Promise.resolve({ ok: true, json: () => Promise.resolve(j) })

  it('the button takes a labelled city-level position, explaining what it does with the IP address', async () => {
    install((_o, fail) => fail({ code: 2 }))
    vi.stubGlobal('fetch', vi.fn(() => okJson(geojs)))
    render(<Probe />)
    await flush()
    expect(screen.getByText(/sends your IP address to a free location service/)).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('use-approx-location'))
    await flush(); await flush()
    expect(screen.getByTestId('location-approx')).toHaveTextContent('approximate location (Delhi, India)')
    expect(screen.getByTestId('location-approx')).toHaveTextContent('city-level only')
  })

  it('nothing is sent to that service unless the user taps the button', async () => {
    const f = vi.fn(() => okJson(geojs))
    vi.stubGlobal('fetch', f)
    install(hang)
    render(<Probe />)
    await flush()
    await advance(20_000)
    expect(f).not.toHaveBeenCalled()
  })

  it('"Use my precise location" goes back to the device', async () => {
    install((_o, fail) => fail({ code: 2 }))
    vi.stubGlobal('fetch', vi.fn(() => okJson(geojs)))
    render(<Probe />)
    await flush()
    fireEvent.click(screen.getByTestId('use-approx-location'))
    await flush(); await flush()
    install(ok)
    fireEvent.click(screen.getByTestId('location-precise'))
    await flush()
    expect(screen.getByTestId('location-live')).toBeInTheDocument()
    expect(screen.queryByTestId('location-approx')).toBeNull()
  })
})

describe('every card shares one request', () => {
  it('four cards mounting together make ONE call to the device, and all get the answer', async () => {
    install(ok)
    render(<><Probe /><Probe /><Probe /><Probe /></>)
    await flush()
    expect(geo.getCurrentPosition).toHaveBeenCalledTimes(1)
    expect(screen.getAllByTestId('location-live')).toHaveLength(4)
  })

  it('and a spinner never outlives the request: all four give up together', async () => {
    install(hang)
    render(<><Probe /><Probe /><Probe /><Probe /></>)
    await flush()
    expect(screen.getAllByText('Getting your location…')).toHaveLength(4)
    await advance(13_500)
    expect(screen.queryByText('Getting your location…')).toBeNull()
    expect(screen.getAllByText(/took too long/)).toHaveLength(4)
  })
})
