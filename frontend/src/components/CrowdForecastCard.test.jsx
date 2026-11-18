import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import i18n from '../i18n'
import CrowdForecastCard from './CrowdForecastCard.jsx'

const mock = new MockAdapter(api)
const here = { lat: 28.6315, lng: 77.2167 }

const hour = (i, band, score) => ({ hour: `${String((15 + i) % 24).padStart(2, '0')}:00`, day_offset: 0, score, band, rain_chance: 10, temp_c: 24 })
const BANDS = [['busy', 54], ['busy', 58], ['busy', 61], ['busy', 61], ['busy', 54], ['moderate', 43], ['moderate', 31], ['quiet', 18], ['quiet', 8], ['quiet', 2], ['quiet', 1], ['quiet', 1]]
const OUTLOOK = (over = {}) => ({
  lat: here.lat, lng: here.lng, method: 'estimate',
  note: 'An estimate … not a live count of people.',
  local_time: '2026-11-16T15:00:00', now: hour(0, 'busy', 54), hours: BANDS.map(([b, s], i) => hour(i, b, s)),
  best_time: { hour: '09:00', band: 'moderate', day_offset: 1 }, area: { counts: { attractions: 6, markets: 3, stations: 4 }, level: 'many', label: 'Many attractions' },
  factors: [
    { effect: 'up', text: 'Many attractions, markets or stations within 1 km — a busy visitor area' },
    { effect: 'up', text: 'Weekend: more people out' },
    { effect: 'down', text: 'Rain likely at times (up to 80%): fewer people out' },
  ],
  inputs: { weather: true, area: true, area_pending: false, holidays: true, season: true },
  region: { name: 'Connaught Place, New Delhi', country_code: 'IN' }, ...over,
})
const calls = () => mock.history.get.filter((c) => c.url === '/crowd/outlook')

const flush = () => act(async () => { await vi.advanceTimersByTimeAsync(0) })
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

let geo
const installGeo = (mode = 'ok') => {
  geo = {
    getCurrentPosition: vi.fn((ok, fail) => {
      if (mode === 'ok') ok({ coords: { latitude: here.lat, longitude: here.lng, accuracy: 20 }, timestamp: Date.now() })
      else if (mode === 'denied') fail({ code: 1 })
      else if (mode === 'timeout') fail({ code: 3 })
    }),
  }
  Object.defineProperty(globalThis.navigator, 'geolocation', { value: geo, configurable: true })
}
const open = async (mode = 'ok') => {
  installGeo(mode)
  const u = render(<CrowdForecastCard />)
  await flush(); await flush()
  return u
}

beforeAll(() => i18n.changeLanguage('en'))
beforeEach(() => {
  mock.reset()
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
  mock.onGet('/crowd/outlook').reply(200, OUTLOOK())
})
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('Crowd outlook — real position, honest estimate', () => {
  it('asks the device where it is and requests the outlook for THAT position, with the device clock offset', async () => {
    await open()
    expect(geo.getCurrentPosition).toHaveBeenCalled()
    expect(calls()).toHaveLength(1)
    expect(calls()[0].params).toMatchObject({ lat: here.lat, lng: here.lng })
    expect(calls()[0].params.tz_offset_min).toBe(-new Date().getTimezoneOffset())
    expect(screen.getByTestId('discovery-area')).toHaveTextContent('Connaught Place, New Delhi')
  })

  it('does not use the old fixture zones or the old zone endpoint at all', async () => {
    await open()
    expect(mock.history.get.some((c) => c.url === '/crowd/forecast')).toBe(false)
    expect(document.body).not.toHaveTextContent(/City Center Safe Zone|Riverside Restricted|DBSCAN|Hillside Trek/)
  })

  it('shows how busy it is now, the quietest time, and a 12-hour chart coloured by level', async () => {
    await open()
    expect(screen.getByTestId('crowd-now')).toHaveTextContent('Busy')
    expect(screen.getByTestId('crowd-now')).toHaveAttribute('data-band', 'busy')
    expect(screen.getByTestId('crowd-best')).toHaveTextContent('09:00 tomorrow')
    const bars = screen.getAllByTestId('crowd-bar')
    expect(bars).toHaveLength(12)
    expect(bars.map((b) => b.dataset.band)).toEqual(BANDS.map(([b]) => b))
    expect(bars[0]).toHaveAttribute('title', '15:00 — Busy')
    expect(bars[8]).toHaveAttribute('title', '23:00 — Quiet')
    // not every bar is the same: it varies with the hour
    expect(new Set(bars.map((b) => b.dataset.band)).size).toBeGreaterThan(2)
  })

  it('names every factor that moved the estimate, with its direction', async () => {
    await open()
    const factors = screen.getByTestId('crowd-factors')
    expect(factors).toHaveTextContent('busy visitor area')
    expect(factors).toHaveTextContent('Weekend: more people out')
    expect(factors).toHaveTextContent('Rain likely at times (up to 80%)')
  })

  it('says plainly, every time, that it is an estimate and not a live count', async () => {
    await open()
    expect(screen.getByTestId('crowd-disclaimer')).toHaveTextContent(/estimate, not a live count of people/)
    expect(screen.getByText('Right now (estimate)')).toBeInTheDocument()
  })

  it('a different place gives a different request — and different results', async () => {
    mock.onGet('/maps/suggest').reply(200, { results: [{ name: 'Manali, Himachal Pradesh, India', lat: 32.2396, lng: 77.1887 }] })
    mock.onGet('/crowd/outlook').reply((c) => [200, c.params.lat === 32.2396
      ? OUTLOOK({ now: hour(0, 'quiet', 12), hours: BANDS.map((_, i) => hour(i, 'quiet', 10)), factors: [{ effect: 'down', text: 'Few attractions, markets or stations mapped within 1 km' }] })
      : OUTLOOK()])
    await open()
    expect(screen.getByTestId('crowd-now')).toHaveAttribute('data-band', 'busy')
    fireEvent.click(screen.getByTestId('change-location'))
    fireEvent.change(screen.getByPlaceholderText('Search a city or area…'), { target: { value: 'Manali' } })
    await advance(500)
    fireEvent.click(screen.getByText('Manali', { exact: false }))
    await flush(); await flush()
    expect(calls().pop().params).toMatchObject({ lat: 32.2396, lng: 77.1887 })
    expect(calls().pop().params.tz_offset_min).toBeUndefined() // a typed place may be elsewhere: the server uses that place's own time
    expect(screen.getByTestId('crowd-now')).toHaveAttribute('data-band', 'quiet')
    expect(screen.getByTestId('crowd-factors')).toHaveTextContent('Few attractions')
  })
})

describe('Crowd outlook — missing inputs are said, not hidden', () => {
  it('says when weather and holiday data were not available', async () => {
    mock.onGet('/crowd/outlook').reply(200, OUTLOOK({ inputs: { weather: false, area: true, area_pending: false, holidays: false, season: true } }))
    await open()
    expect(screen.getByTestId('crowd-no-weather')).toBeInTheDocument()
    expect(screen.getByTestId('crowd-no-holidays')).toHaveTextContent(/holidays are not factored in/)
  })

  it('shows no such notes when everything was available', async () => {
    await open()
    expect(screen.queryByTestId('crowd-no-weather')).toBeNull()
    expect(screen.queryByTestId('crowd-no-holidays')).toBeNull()
  })

  it('re-checks by itself while the server is still looking up the surroundings, then updates', async () => {
    mock.reset() // so the one-shot reply below is the first handler
    mock.onGet('/crowd/outlook').replyOnce(200, OUTLOOK({ area: null, factors: [],
      inputs: { weather: true, area: false, area_pending: true, holidays: true, season: true } }))
    mock.onGet('/crowd/outlook').reply(200, OUTLOOK())
    await open()
    expect(screen.getByTestId('crowd-area-pending')).toBeInTheDocument()
    expect(screen.queryByTestId('crowd-factors')).toBeNull()
    await advance(10_500)
    expect(calls()).toHaveLength(2)
    expect(screen.queryByTestId('crowd-area-pending')).toBeNull()
    expect(screen.getByTestId('crowd-factors')).toHaveTextContent('busy visitor area')
  })

  it('does not keep asking forever', async () => {
    mock.onGet('/crowd/outlook').reply(200, OUTLOOK({ area: null, inputs: { weather: true, area: false, area_pending: true, holidays: true, season: true } }))
    await open()
    await advance(60_000)
    expect(calls().length).toBeLessThanOrEqual(4)
  })
})

describe('Crowd outlook — location and failures', () => {
  it('permission refused: names the problem, asks nothing, offers a manual place', async () => {
    await open('denied')
    expect(screen.getByText(/Location permission is off/)).toBeInTheDocument()
    expect(calls()).toHaveLength(0)
    expect(screen.queryByTestId('crowd-result')).toBeNull()
    expect(screen.getByPlaceholderText('Search a city or area…')).toBeInTheDocument()
  })

  it('GPS timeout is explained too', async () => {
    await open('timeout')
    expect(screen.getByText(/took too long/)).toBeInTheDocument()
    expect(calls()).toHaveLength(0)
  })

  it('a server problem (503) is an error with Try again — not an empty chart', async () => {
    mock.onGet('/crowd/outlook').reply(503, { detail: 'Crowd outlook is unavailable right now' })
    await open()
    expect(screen.getByTestId('crowd-error')).toHaveTextContent(/could not be worked out right now/)
    expect(screen.queryByTestId('crowd-result')).toBeNull()
    mock.onGet('/crowd/outlook').reply(200, OUTLOOK())
    fireEvent.click(screen.getByTestId('crowd-retry'))
    await flush(); await flush()
    expect(screen.queryByTestId('crowd-error')).toBeNull()
    expect(screen.getByTestId('crowd-now')).toBeInTheDocument()
  })

  it('no connection is reported as such', async () => {
    mock.onGet('/crowd/outlook').networkError()
    await open()
    expect(screen.getByTestId('crowd-error')).toHaveTextContent(/Check your internet connection/)
  })

  it('shows a skeleton while loading', async () => {
    mock.onGet('/crowd/outlook').reply(() => new Promise(() => {}))
    await open()
    expect(screen.getByTestId('crowd-skeleton')).toBeInTheDocument()
  })

  it('tells the tourist when the browser is offline', async () => {
    await open()
    await act(async () => { window.dispatchEvent(new Event('offline')) })
    expect(screen.getByTestId('crowd-offline')).toBeInTheDocument()
    await act(async () => { window.dispatchEvent(new Event('online')) })
    expect(screen.queryByTestId('crowd-offline')).toBeNull()
  })

  it('Refresh takes a fresh position and asks again', async () => {
    await open()
    const gps = geo.getCurrentPosition.mock.calls.length
    fireEvent.click(screen.getByTestId('crowd-refresh'))
    await flush(); await flush()
    expect(geo.getCurrentPosition.mock.calls.length).toBe(gps + 1)
    expect(calls().length).toBe(2)
  })
})
