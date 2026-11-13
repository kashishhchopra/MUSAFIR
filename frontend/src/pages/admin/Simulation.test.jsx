import { describe, it, beforeEach, expect, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../../api'
import Simulation from './Simulation'

// Leaflet needs real layout; the page logic (controls, queue, events) is what's under test.
vi.mock('react-leaflet', () => {
  const Pass = ({ children }) => <div>{children}</div>
  return {
    MapContainer: Pass, TileLayer: () => null, CircleMarker: Pass, Marker: Pass, Polygon: Pass,
    Polyline: () => null, Tooltip: Pass, useMap: () => ({ flyTo() {}, fitBounds() {}, getZoom: () => 13 }),
  }
})
vi.mock('../../useWebSocket', () => ({ default: () => ({ connected: false }) }))

const mock = new MockAdapter(api)
const agents = [
  { id: 1, digital_id: 'SIM-0001', name: 'Aarav Das', nationality: 'Indian', lat: 26.14, lng: 91.73,
    status: 'normal', mode: 'route', score: 95, flags: [], speed_kmh: 4.5, anomaly_score: 0.1 },
  { id: 2, digital_id: 'SIM-0002', name: 'Emma Rossi', nationality: 'Italian', lat: 26.15, lng: 91.74,
    status: 'sos', mode: 'sos', score: 40, flags: [], speed_kmh: 0, anomaly_score: 0.2 },
]
const state = {
  running: false, speed: 1, sim_time: '2026-11-13T09:00:00', elapsed_s: 0, scenario: null, scenario_pending: 0,
  counts: { total: 2, active: 2, inactive: 0, normal: 1, sos: 1, off_route: 0, danger_zone: 0, anomaly: 0 },
  agents, speeds: [1, 2, 5, 10, 30, 60], events: {}, log: [],
}

beforeEach(() => {
  mock.reset()
  mock.onGet('/simulation/state').reply(200, state)
  mock.onGet('/simulation/alerts?limit=60').reply(200, [])
  mock.onGet('/zones').reply(200, [])
})

describe('Simulation control room', () => {
  it('shows stats and lists simulated tourists, SOS first', async () => {
    const { findByText, getAllByRole } = render(<Simulation />)
    await findByText('Emma Rossi')
    expect(await findByText('Aarav Das')).toBeTruthy()
    const names = getAllByRole('listitem').map((li) => li.textContent)
    expect(names[0]).toContain('Emma Rossi')
  })

  it('Play posts to /simulation/play; speed buttons post the chosen speed', async () => {
    mock.onPost('/simulation/play').reply(200, { running: true, speed: 1 })
    mock.onPost('/simulation/speed').reply(200, { speed: 60 })
    const { findByText, getByRole } = render(<Simulation />)
    await findByText('Aarav Das')
    fireEvent.click(getByRole('button', { name: /Play/ }))
    await waitFor(() => expect(mock.history.post.some((r) => r.url === '/simulation/play')).toBe(true))
    fireEvent.click(getByRole('button', { name: '60x' }))
    await waitFor(() => {
      const r = mock.history.post.find((x) => x.url === '/simulation/speed')
      expect(JSON.parse(r.data)).toEqual({ speed: 60 })
    })
  })

  it('firing an event targets the selected tourist', async () => {
    mock.onGet('/simulation/tourists/2').reply(200, { ...agents[1], route: [], trail: [], pings: [], alerts: [], phone: '+91' })
    mock.onPost('/simulation/events').reply(200, {})
    const { findByText, getByRole } = render(<Simulation />)
    fireEvent.click(await findByText('Aarav Das'))     // select -> switches to Tourist tab
    fireEvent.click(getByRole('tab', { name: 'Events' }))
    fireEvent.click(getByRole('button', { name: /Go off-route/ }))
    await waitFor(() => {
      const r = mock.history.post.find((x) => x.url === '/simulation/events')
      expect(JSON.parse(r.data)).toMatchObject({ event: 'off_route', tourist_id: 1 })
    })
  })
})
