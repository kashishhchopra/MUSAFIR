import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import '../i18n'
import api from '../api'
import SavedRoutesCard from './SavedRoutesCard'

vi.mock('./TripMap.jsx', () => ({
  default: ({ stops, path }) => <div data-testid="trip-map" data-stops={stops.length} data-path={path ? path.length : 0} />,
}))

const mock = new MockAdapter(api)
const route = (over = {}) => ({
  id: 1, name: 'Hotel to Taj', mode: 'walking',
  origin: { name: 'My location', lat: 27.18, lng: 78.01 },
  destination: { name: 'Taj Mahal', lat: 27.1751, lng: 78.0421 },
  stops: [{ name: 'Agra Fort', lat: 27.1795, lng: 78.0211 }],
  distance_km: 3.4, duration_min: 41, geometry: [[27.18, 78.01], [27.1751, 78.0421]],
  risk_level: '', created_at: '2026-11-18T10:00:00', last_opened_at: null, ...over,
})

beforeEach(() => mock.reset())
afterEach(() => vi.restoreAllMocks())

describe('SavedRoutesCard', () => {
  it('shows an empty state when nothing is saved', async () => {
    mock.onGet('/tourists/1/saved-routes').reply(200, [])
    const { findByText } = render(<SavedRoutesCard touristId={1} />)
    expect(await findByText(/No saved routes yet/)).toBeInTheDocument()
  })

  it('lists routes from the server with distance, time and stops', async () => {
    mock.onGet('/tourists/1/saved-routes').reply(200, [route()])
    const { findByText, getByText } = render(<SavedRoutesCard touristId={1} />)
    expect(await findByText(/Hotel to Taj/)).toBeInTheDocument()
    expect(getByText(/My location → Taj Mahal/)).toBeInTheDocument()
    expect(getByText(/1 stop\)/)).toBeInTheDocument()
    expect(getByText(/3\.4 km · 41 min/)).toBeInTheDocument()
  })

  it('shows an error with retry when loading fails', async () => {
    mock.onGet('/tourists/1/saved-routes').networkError()
    const { findByRole } = render(<SavedRoutesCard touristId={1} />)
    expect(await findByRole('alert')).toHaveTextContent(/Couldn't load your saved routes/)
  })

  it('opens a route on the map (using its saved line) and records that it was opened', async () => {
    mock.onGet('/tourists/1/saved-routes').reply(200, [route()])
    mock.onPost('/tourists/1/saved-routes/1/open').reply(200, route({ last_opened_at: '2026-11-18T11:00:00' }))
    const { findByText, findByTestId, queryByTestId } = render(<SavedRoutesCard touristId={1} />)
    fireEvent.click(await findByText(/View on map/))
    const map = await findByTestId('trip-map')
    expect(map.dataset.stops).toBe('3') // origin + 1 stop + destination
    expect(map.dataset.path).toBe('2')
    await waitFor(() => expect(mock.history.post).toHaveLength(1))
    fireEvent.click(await findByText('Hide map'))
    expect(queryByTestId('trip-map')).not.toBeInTheDocument()
  })

  it('resumes navigation in Google Maps from the live location through the saved stops', async () => {
    mock.onGet('/tourists/1/saved-routes').reply(200, [route()])
    mock.onPost('/tourists/1/saved-routes/1/open').reply(200, route())
    const open = vi.spyOn(window, 'open').mockReturnValue({})
    const { findByText } = render(<SavedRoutesCard touristId={1} />)
    fireEvent.click(await findByText(/Navigate/))
    const url = new URL(open.mock.calls[0][0])
    expect(url.searchParams.has('origin')).toBe(false)
    expect(url.searchParams.get('destination')).toBe('27.1751,78.0421')
    expect(url.searchParams.get('waypoints')).toBe('27.1795,78.0211')
    expect(url.searchParams.get('travelmode')).toBe('walking')
  })

  it('deletes only after confirmation, and removes it from the list', async () => {
    mock.onGet('/tourists/1/saved-routes').reply(200, [route(), route({ id: 2, name: 'Other' })])
    mock.onDelete('/tourists/1/saved-routes/1').reply(204)
    const { findByText, queryByText, getAllByText } = render(<SavedRoutesCard touristId={1} />)
    fireEvent.click((await findByText(/Hotel to Taj/)) && getAllByText(/Delete/)[0])
    expect(mock.history.delete).toHaveLength(0) // first tap only asks
    fireEvent.click(await findByText('Yes, delete'))
    await waitFor(() => expect(queryByText(/Hotel to Taj/)).not.toBeInTheDocument())
    expect(queryByText(/Other/)).toBeInTheDocument()
    expect(mock.history.delete).toHaveLength(1)
  })

  it('keeps the route and tells the tourist when a delete fails', async () => {
    mock.onGet('/tourists/1/saved-routes').reply(200, [route()])
    mock.onDelete('/tourists/1/saved-routes/1').reply(500)
    const { findByText, getAllByText, findByRole } = render(<SavedRoutesCard touristId={1} />)
    await findByText(/Hotel to Taj/)
    fireEvent.click(getAllByText(/Delete/)[0])
    fireEvent.click(await findByText('Yes, delete'))
    expect(await findByRole('alert')).toHaveTextContent(/Couldn't delete/)
    expect(await findByText(/Hotel to Taj/)).toBeInTheDocument()
  })

  it('reloads when refreshKey changes (a route was saved elsewhere)', async () => {
    mock.onGet('/tourists/1/saved-routes').replyOnce(200, [])
    const { findByText, rerender } = render(<SavedRoutesCard touristId={1} refreshKey={0} />)
    await findByText(/No saved routes yet/)
    mock.onGet('/tourists/1/saved-routes').reply(200, [route({ name: 'Fresh one' })])
    rerender(<SavedRoutesCard touristId={1} refreshKey={1} />)
    expect(await findByText(/Fresh one/)).toBeInTheDocument()
  })
})
