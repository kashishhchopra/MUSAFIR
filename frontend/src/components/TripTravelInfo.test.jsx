import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import '../i18n'
import api from '../api'
import TripTravelInfo from './TripTravelInfo'

vi.mock('./TripMap.jsx', () => ({ default: ({ stops }) => <div data-testid="trip-map">{stops.length} stops</div> }))

const mock = new MockAdapter(api)
const STOPS = [
  { name: 'Taj Mahal', lat: 27.1751, lng: 78.0421 },
  { name: 'Agra Fort', lat: 27.1795, lng: 78.0211 },
]
const TRIP = {
  mode: 'driving', demo: false, total_distance_km: 9.4, total_duration_min: 31,
  legs: [
    { distance_km: 6.1, duration_min: 19, demo: false, geometry: [[27.2, 78], [27.1751, 78.0421]] },
    { distance_km: 3.3, duration_min: 12, demo: false, geometry: [[27.1751, 78.0421], [27.1795, 78.0211]] },
  ],
}

function stubGeolocation(result) {
  const getCurrentPosition = vi.fn((ok, fail) => {
    if (result.error) fail({ code: result.error })
    else ok({ coords: { latitude: result.lat, longitude: result.lng, accuracy: 12 }, timestamp: Date.now() })
  })
  Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition }, configurable: true })
  return getCurrentPosition
}

beforeEach(() => {
  mock.reset()
  mock.onGet('/maps/modes').reply(200, { modes: ['driving', 'walking', 'bicycling'] })
})
afterEach(() => { vi.restoreAllMocks() })

describe('TripTravelInfo', () => {
  it('renders nothing when no stop has a map location', () => {
    const { container } = render(<TripTravelInfo touristId={1} stops={[{ name: 'Somewhere' }]} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('does not ask for location or call any routing API until the tourist taps Calculate', () => {
    const geo = stubGeolocation({ lat: 27.18, lng: 78.01 })
    render(<TripTravelInfo touristId={1} stops={STOPS} />)
    expect(geo).not.toHaveBeenCalled()
    expect(mock.history.post).toHaveLength(0)
  })

  it('uses the real GPS fix as the start of the trip and shows per-leg + total time', async () => {
    stubGeolocation({ lat: 27.18, lng: 78.01 })
    mock.onPost('/maps/trip').reply(200, TRIP)
    const { getByText, findByTestId, findByText } = render(<TripTravelInfo touristId={1} stops={STOPS} />)
    fireEvent.click(getByText('Calculate travel time'))
    await findByTestId('trip-result')
    const body = JSON.parse(mock.history.post[0].data)
    expect(body.mode).toBe('driving')
    expect(body.points).toEqual([
      { lat: 27.18, lng: 78.01 }, { lat: 27.1751, lng: 78.0421 }, { lat: 27.1795, lng: 78.0211 },
    ])
    expect(await findByText(/9\.4 km · 31 min/)).toBeInTheDocument()
    expect(getByText('You → Taj Mahal')).toBeInTheDocument()
    expect(getByText('Taj Mahal → Agra Fort')).toBeInTheDocument()
    expect(getByText(/6\.1 km · 19 min/)).toBeInTheDocument()
  })

  it('sends the chosen travel mode and clears stale numbers when it changes', async () => {
    stubGeolocation({ lat: 27.18, lng: 78.01 })
    mock.onPost('/maps/trip').reply(200, TRIP)
    const { getByText, findByTestId, queryByTestId, findByRole } = render(<TripTravelInfo touristId={1} stops={STOPS} />)
    fireEvent.click(getByText('Calculate travel time'))
    await findByTestId('trip-result')
    fireEvent.click(await findByRole('button', { name: /Walk/ }))
    await waitFor(() => expect(queryByTestId('trip-result')).not.toBeInTheDocument())
    fireEvent.click(getByText('Calculate travel time'))
    await findByTestId('trip-result')
    expect(JSON.parse(mock.history.post[1].data).mode).toBe('walking')
  })

  it('labels straight-line estimates honestly', async () => {
    stubGeolocation({ lat: 27.18, lng: 78.01 })
    mock.onPost('/maps/trip').reply(200, { ...TRIP, demo: true, legs: TRIP.legs.map((l) => ({ ...l, demo: true, geometry: null })) })
    const { getByText, findByText } = render(<TripTravelInfo touristId={1} stops={STOPS} />)
    fireEvent.click(getByText('Calculate travel time'))
    expect(await findByText(/straight-line estimates/)).toBeInTheDocument()
  })

  it('with location denied it still times stop-to-stop when there are two stops, and says why', async () => {
    stubGeolocation({ error: 1 })
    mock.onPost('/maps/trip').reply(200, { ...TRIP, legs: [TRIP.legs[1]], total_distance_km: 3.3, total_duration_min: 12 })
    const { getByText, findByTestId, findByRole } = render(<TripTravelInfo touristId={1} stops={STOPS} />)
    fireEvent.click(getByText('Calculate travel time'))
    await findByTestId('trip-result')
    expect(JSON.parse(mock.history.post[0].data).points).toHaveLength(2)
    expect(await findByRole('alert')).toHaveTextContent(/Location permission is off/)
  })

  it('with location denied and a single stop it asks the tourist to allow location instead of inventing a start', async () => {
    stubGeolocation({ error: 1 })
    const { getByText, findByText } = render(<TripTravelInfo touristId={1} stops={[STOPS[0]]} />)
    fireEvent.click(getByText('Calculate travel time'))
    expect(await findByText(/Allow your location, or add at least two stops/)).toBeInTheDocument()
    expect(mock.history.post).toHaveLength(0)
  })

  it('shows an error when routing fails', async () => {
    stubGeolocation({ lat: 27.18, lng: 78.01 })
    mock.onPost('/maps/trip').networkError()
    const { getByText, findByText } = render(<TripTravelInfo touristId={1} stops={STOPS} />)
    fireEvent.click(getByText('Calculate travel time'))
    expect(await findByText(/Couldn't calculate travel times/)).toBeInTheDocument()
  })

  it('opens Google Maps directions from the live location through the stops', async () => {
    stubGeolocation({ lat: 27.18, lng: 78.01 })
    mock.onPost('/maps/trip').reply(200, TRIP)
    const open = vi.spyOn(window, 'open').mockReturnValue({})
    const { getByText, findByText } = render(<TripTravelInfo touristId={1} stops={STOPS} />)
    fireEvent.click(getByText('Calculate travel time'))
    fireEvent.click(await findByText(/Navigate in Google Maps/))
    const url = new URL(open.mock.calls[0][0])
    expect(url.hostname).toBe('www.google.com')
    expect(url.searchParams.get('origin')).toBe('27.18,78.01')
    expect(url.searchParams.get('destination')).toBe('27.1795,78.0211')
    expect(url.searchParams.get('waypoints')).toBe('27.1751,78.0421')
    expect(url.searchParams.get('travelmode')).toBe('driving')
  })

  it('offers Navigate but no "Save route" (that feature was removed)', async () => {
    stubGeolocation({ lat: 27.18, lng: 78.01 })
    mock.onPost('/maps/trip').reply(200, TRIP)
    const { getByText, findByText, queryByText, queryByLabelText } = render(<TripTravelInfo stops={STOPS} />)
    fireEvent.click(getByText('Calculate travel time'))
    expect(await findByText(/Navigate in Google Maps/)).toBeInTheDocument()
    expect(queryByText(/Save route/)).not.toBeInTheDocument()
    expect(queryByLabelText('Route name')).not.toBeInTheDocument()
    expect(mock.history.post.filter((r) => r.url.includes('saved-routes'))).toHaveLength(0)
  })
})
