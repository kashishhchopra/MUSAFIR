import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import '../i18n'
import api from '../api'
import FairPriceCard from './FairPriceCard'

const mock = new MockAdapter(api)
const stubGeo = (result) => {
  const getCurrentPosition = vi.fn((ok, fail) => (result.error
    ? fail({ code: result.error })
    : ok({ coords: { latitude: result.lat, longitude: result.lng, accuracy: 8 }, timestamp: Date.now() })))
  Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition }, configurable: true })
  return getCurrentPosition
}
const check = (over = {}) => ({
  id: 1, tourist_id: 1, transport_type: 'auto_rickshaw', pickup_name: 'Current location',
  pickup_lat: 27.18, pickup_lng: 78.01, destination_name: 'Taj Mahal',
  destination_lat: 27.1751, destination_lng: 78.0421, route_estimated: false,
  distance_km: 3.1, duration_min: 9, estimated_min: 60, estimated_max: 90, quoted_fare: 150,
  verdict: 'suspicious', percent_diff: 66, reported: false, report_note: '', created_at: '2026-11-18T00:00:00', ...over,
})

beforeEach(() => {
  mock.reset()
  mock.onGet('/fare/transport-types').reply(200, ['auto_rickshaw', 'taxi'])
  mock.onGet('/tourists/1/fare-checks').reply(200, [])
  mock.onGet('/maps/search').reply(200, { results: [{ name: 'Taj Mahal, Agra', lat: 27.1751, lng: 78.0421, demo: false }] })
  delete navigator.permissions
})
afterEach(() => vi.restoreAllMocks())

async function pickDestinationAndQuote(utils, quote = '150') {
  fireEvent.change(utils.getByLabelText('Destination (place name)'), { target: { value: 'Taj Mahal' } })
  fireEvent.click(utils.getByText('Search'))
  fireEvent.click(await utils.findByText(/Taj Mahal/)) // result row: name, with the address on a second line
  fireEvent.change(utils.getByPlaceholderText('Quoted fare (₹)'), { target: { value: quote } })
}

describe('FairPriceCard destination search', () => {
  it('has no form inside a form (that made the browser reload the page on Search)', () => {
    const { container } = render(<FairPriceCard touristId={1} />)
    expect(container.querySelectorAll('form form')).toHaveLength(0)
    expect(container.querySelectorAll('form')).toHaveLength(1)
  })

  it('Enter in the destination box searches and does not submit the fare form', async () => {
    const utils = render(<FairPriceCard touristId={1} />)
    const submit = vi.fn((e) => e.preventDefault())
    utils.container.querySelector('form').addEventListener('submit', submit)
    const input = utils.getByLabelText('Destination (place name)')
    fireEvent.change(input, { target: { value: 'Taj Mahal' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(await utils.findByText(/Taj Mahal/)).toBeInTheDocument()
    expect(submit).not.toHaveBeenCalled()
  })
})

describe('FairPriceCard', () => {
  it('asks permission with an explanation and never reads a location before the tourist agrees', () => {
    const geo = stubGeo({ lat: 27.18, lng: 78.01 })
    const { getByText } = render(<FairPriceCard touristId={1} />)
    expect(getByText(/needs your current location/)).toBeInTheDocument()
    expect(geo).not.toHaveBeenCalled()
    expect(getByText('Check fare').closest('button')).toBeDisabled()
  })

  it('sends the REAL device coordinates and the picked place -- no stored/default location', async () => {
    stubGeo({ lat: 27.18123, lng: 78.01456 })
    mock.onPost('/tourists/1/fare-checks').reply(201, check())
    const utils = render(<FairPriceCard touristId={1} />)
    fireEvent.click(utils.getByText(/Use my current location/))
    await utils.findByText(/Using your live location/)
    await pickDestinationAndQuote(utils)
    fireEvent.click(utils.getByText('Check fare'))
    await waitFor(() => expect(mock.history.post).toHaveLength(1))
    expect(JSON.parse(mock.history.post[0].data)).toMatchObject({
      pickup_lat: 27.18123, pickup_lng: 78.01456,
      destination_name: 'Taj Mahal, Agra', destination_lat: 27.1751, destination_lng: 78.0421,
      quoted_fare: 150, transport_type: 'auto_rickshaw',
    })
  })

  it('explains denied permission and does not allow a check without a real location', async () => {
    stubGeo({ error: 1 })
    const utils = render(<FairPriceCard touristId={1} />)
    fireEvent.click(utils.getByText(/Use my current location/))
    expect(await utils.findByText(/Location permission is off/)).toBeInTheDocument()
    await pickDestinationAndQuote(utils)
    expect(utils.getByText('Check fare').closest('button')).toBeDisabled()
    expect(mock.history.post).toHaveLength(0)
  })

  it.each([
    [2, /couldn't be determined/], [3, /took too long/],
  ])('handles location error code %i', async (code, msg) => {
    stubGeo({ error: code })
    const utils = render(<FairPriceCard touristId={1} />)
    fireEvent.click(utils.getByText(/Use my current location/))
    expect(await utils.findByText(msg)).toBeInTheDocument()
    expect(utils.getByText('Try again')).toBeInTheDocument()
  })

  it('tells the tourist when no place matches and when search fails', async () => {
    stubGeo({ lat: 1, lng: 2 })
    mock.onGet('/maps/search').reply(200, { results: [] })
    const utils = render(<FairPriceCard touristId={1} />)
    fireEvent.change(utils.getByLabelText('Destination (place name)'), { target: { value: 'asdfgh' } })
    fireEvent.click(utils.getByText('Search'))
    expect(await utils.findByText(/No matching place found/)).toBeInTheDocument()
    mock.onGet('/maps/search').networkError()
    fireEvent.click(utils.getByText('Search'))
    expect(await utils.findByText(/Couldn't search for places/)).toBeInTheDocument()
  })

  it('shows results with a real-route distance, offers Open in Maps, and flags straight-line estimates', async () => {
    stubGeo({ lat: 27.18, lng: 78.01 })
    mock.onGet('/tourists/1/fare-checks').reply(200, [check(), check({ id: 2, route_estimated: true, destination_name: 'Far place' })])
    const open = vi.spyOn(window, 'open').mockReturnValue({})
    const { findByText, getAllByText, getByText } = render(<FairPriceCard touristId={1} />)
    await findByText(/Taj Mahal/)
    expect(getByText(/straight-line estimate/)).toBeInTheDocument()
    expect(getAllByText(/3\.1 km/)).toHaveLength(2)
    fireEvent.click(getAllByText(/Open route in Maps/)[0])
    const url = new URL(open.mock.calls[0][0])
    expect(url.searchParams.get('origin')).toBe('27.18,78.01')
    expect(url.searchParams.get('destination')).toBe('27.1751,78.0421')
  })

  it('reports an overpriced fare', async () => {
    stubGeo({ lat: 27.18, lng: 78.01 })
    mock.onGet('/tourists/1/fare-checks').reply(200, [check()])
    mock.onPost('/tourists/1/fare-checks/1/report').reply(200, check({ reported: true }))
    const { findByText } = render(<FairPriceCard touristId={1} />)
    fireEvent.click(await findByText('Report this fare'))
    await waitFor(() => expect(mock.history.post).toHaveLength(1))
  })

  it('surfaces a server error message', async () => {
    stubGeo({ lat: 27.18, lng: 78.01 })
    mock.onPost('/tourists/1/fare-checks').reply(400, { detail: 'Unknown transport type: rocket' })
    const utils = render(<FairPriceCard touristId={1} />)
    fireEvent.click(utils.getByText(/Use my current location/))
    await utils.findByText(/Using your live location/)
    await pickDestinationAndQuote(utils)
    fireEvent.click(utils.getByText('Check fare'))
    expect(await utils.findByText('Unknown transport type: rocket')).toBeInTheDocument()
  })
})
