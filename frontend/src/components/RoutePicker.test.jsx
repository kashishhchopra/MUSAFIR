import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, act, renderHook, fireEvent } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import RoutePicker, { RouteLayer, useRoutePicker } from './RoutePicker'

// jsdom doesn't implement the SVG/layout internals Leaflet's renderer needs,
// so real Polyline/CircleMarker throw on mount here -- stub to inert
// placeholders and assert on props instead (same approach as TrajectoryOverlay.test.jsx).
vi.mock('react-leaflet', () => ({
  Polyline: (props) => (
    <div data-testid="polyline" className={props.className}
      data-positions={JSON.stringify(props.positions)}
      data-color={props.pathOptions.color} data-weight={props.pathOptions.weight} />
  ),
  CircleMarker: (props) => <div data-testid="circle-marker" data-center={JSON.stringify(props.center)} />,
  useMapEvents: (handlers) => { global.__mapClickHandler = handlers.click; return null },
}))

const mock = new MockAdapter(api)
beforeEach(() => mock.reset())

const routeResponse = {
  tourist_id: 1,
  recommended: { points: [[26.1, 91.7], [26.11, 91.705], [26.12, 91.71]], risk_score: 5, length_km: 1.5, risk_level: 'low' },
  candidates: [
    { points: [[26.1, 91.7], [26.11, 91.71], [26.12, 91.72]], risk_score: 80, length_km: 1.4, risk_level: 'high' },
    { points: [[26.1, 91.7], [26.11, 91.705], [26.12, 91.71]], risk_score: 5, length_km: 1.5, risk_level: 'low' },
  ],
}
// candidates[1] === recommended by value but RouteLayer compares by reference,
// so build the fixture with the SAME object reference, matching what the
// real API/JSON.parse response naturally yields via `result.recommended`.
routeResponse.candidates[1] = routeResponse.recommended

describe('RoutePicker', () => {
  it('renders nothing outside the map when not active', () => {
    const { result } = renderHook(() => useRoutePicker(1))
    const { container } = render(<RoutePicker active={false} onToggle={() => {}} state={result.current} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows a destination prompt and disabled button before a destination is picked', () => {
    const { result } = renderHook(() => useRoutePicker(1))
    const { getByText, getByPlaceholderText } = render(<RoutePicker active={true} onToggle={() => {}} state={result.current} />)
    expect(getByText(/Search for a place above, or tap a spot on the map/)).toBeInTheDocument()
    expect(getByPlaceholderText('Where do you want to go?')).toBeInTheDocument()
    expect(getByText('Find safe route').closest('button')).toBeDisabled()
  })

  it('RouteLayer renders a destination marker once picked', () => {
    const { getByTestId } = render(
      <RouteLayer active={true} dest={[26.15, 91.72]} result={null} onPick={() => {}} />
    )
    const center = JSON.parse(getByTestId('circle-marker').dataset.center)
    expect(center).toEqual([26.15, 91.72])
  })

  it('renders all candidate routes and visually emphasizes the recommended one', () => {
    const { getAllByTestId } = render(
      <RouteLayer active={true} dest={[26.12, 91.71]} result={routeResponse} onPick={() => {}} />
    )
    const polylines = getAllByTestId('polyline')
    expect(polylines).toHaveLength(2)

    const recommended = polylines.find((p) => p.className === 'route-recommended')
    const other = polylines.find((p) => p.className === 'route-candidate')
    expect(recommended).toBeTruthy()
    expect(other).toBeTruthy()
    // thicker line for the recommended route
    expect(Number(recommended.dataset.weight)).toBeGreaterThan(Number(other.dataset.weight))
    // colored by risk level via mapIcons.riskColor
    expect(recommended.dataset.color).toBe('#22c55e') // low
    expect(other.dataset.color).toBe('#f97316') // high
  })

  it('fetches and displays the recommendation, including the disclaimer', async () => {
    mock.onGet('/tourists/1/route-recommendation').reply(200, routeResponse)
    const { result } = renderHook(() => useRoutePicker(1))
    act(() => { result.current.pick([26.12, 91.71]) })

    const { rerender, getByText } = render(<RoutePicker active={true} onToggle={() => {}} state={result.current} />)
    rerender(<RoutePicker active={true} onToggle={() => {}} state={result.current} />)

    await act(async () => { await result.current.findRoute() })
    rerender(<RoutePicker active={true} onToggle={() => {}} state={result.current} />)

    expect(getByText(/Approximate route, not turn-by-turn navigation/)).toBeInTheDocument()
    expect(getByText(/1.5 km/)).toBeInTheDocument()
  })

  it('surfaces a 400 error (e.g. no known tourist location) instead of crashing', async () => {
    mock.onGet('/tourists/1/route-recommendation').reply(400, { detail: 'Tourist has no known location yet' })
    const { result } = renderHook(() => useRoutePicker(1))
    act(() => { result.current.pick([26.12, 91.71]) })

    const { rerender, getByText } = render(<RoutePicker active={true} onToggle={() => {}} state={result.current} />)
    await act(async () => { await result.current.findRoute() })
    rerender(<RoutePicker active={true} onToggle={() => {}} state={result.current} />)

    expect(getByText('Tourist has no known location yet')).toBeInTheDocument()
  })

  it('has no "Save route" any more -- only Open in Google Maps', async () => {
    mock.onGet('/tourists/1/route-recommendation').reply(200, routeResponse)
    const { result } = renderHook(() => useRoutePicker(1))
    act(() => { result.current.pick([26.12, 91.71]) })
    const { rerender, getByText, queryByText, queryByLabelText } = render(
      <RoutePicker active={true} onToggle={() => {}} state={result.current} origin={[26.1, 91.7]} />)
    await act(async () => { await result.current.findRoute() })
    rerender(<RoutePicker active={true} onToggle={() => {}} state={result.current} origin={[26.1, 91.7]} />)
    expect(getByText(/Open in Google Maps/)).toBeInTheDocument()
    expect(queryByText(/Save route/)).not.toBeInTheDocument()
    expect(queryByLabelText('Route name')).not.toBeInTheDocument()
    expect(mock.history.post).toHaveLength(0)
  })

  // The clickable map is on the Home screen, so on the Plan screen the destination must be choosable by name.
  describe('choosing the destination by name (no map on this screen)', () => {
    const SUGGEST = { results: [{ name: 'India Gate, New Delhi, Delhi, India', lat: 28.6129, lng: 77.2295, demo: false }] }

    function Harness({ origin = [28.6315, 77.2167] }) {
      const state = useRoutePicker(1)
      return <RoutePicker active onToggle={() => {}} state={state} origin={origin} />
    }

    it('searching and picking a place enables Find safe route, which asks for exactly that place', async () => {
      mock.onGet('/maps/suggest').reply(200, SUGGEST)
      mock.onGet('/tourists/1/route-recommendation').reply(200, routeResponse)
      const user = render(<Harness />)
      expect(user.getByText('Find safe route').closest('button')).toBeDisabled()
      fireEvent.change(user.getByPlaceholderText('Where do you want to go?'), { target: { value: 'India Gate' } })
      fireEvent.click(await user.findByText(/India Gate/, {}, { timeout: 2000 }))
      expect(user.getByText('Find safe route').closest('button')).not.toBeDisabled()
      expect(user.queryByTestId('route-hint')).toBeNull()
      fireEvent.click(user.getByText('Find safe route'))
      await user.findByText(/Approximate route/)
      const call = mock.history.get.find((c) => c.url === '/tourists/1/route-recommendation')
      expect(call.params).toEqual({ dest_lat: 28.6129, dest_lng: 77.2295 })
      // the suggestions were ranked towards where the tourist is
      expect(mock.history.get.find((c) => c.url === '/maps/suggest').params).toMatchObject({ lat: 28.6315, lng: 77.2167 })
    })

    it('shows the chosen place by name, and "Change" clears it again', async () => {
      mock.onGet('/maps/suggest').reply(200, SUGGEST)
      const user = render(<Harness />)
      fireEvent.change(user.getByPlaceholderText('Where do you want to go?'), { target: { value: 'India Gate' } })
      fireEvent.click(await user.findByText(/India Gate/, {}, { timeout: 2000 }))
      expect(user.getByText(/India Gate, New Delhi/)).toBeInTheDocument()
      fireEvent.click(user.getByText('Change'))
      expect(user.getByText('Find safe route').closest('button')).toBeDisabled()
      expect(user.getByPlaceholderText('Where do you want to go?')).toBeInTheDocument()
    })

    it('a destination tapped on the Home map (no name) is still shown, as a pin', () => {
      const { result } = renderHook(() => useRoutePicker(1))
      act(() => { result.current.pick([26.1234, 91.7654]) })
      const { getByText } = render(<RoutePicker active onToggle={() => {}} state={result.current} />)
      expect(getByText(/Pin 26\.1234, 91\.7654/)).toBeInTheDocument()
      expect(getByText('Find safe route').closest('button')).not.toBeDisabled()
    })

    it('Reset clears the destination and its name', async () => {
      const { result } = renderHook(() => useRoutePicker(1))
      act(() => { result.current.pick([1, 2], 'Somewhere') })
      expect(result.current.destName).toBe('Somewhere')
      act(() => { result.current.reset() })
      expect(result.current.dest).toBeNull()
      expect(result.current.destName).toBeNull()
    })

    it('a place that cannot be found says so and leaves the button disabled', async () => {
      mock.onGet('/maps/suggest').reply(200, { results: [] })
      mock.onGet('/maps/search').reply(200, { results: [] })
      const user = render(<Harness />)
      fireEvent.change(user.getByPlaceholderText('Where do you want to go?'), { target: { value: 'qqzzxx nowhere' } })
      expect(await user.findByText(/No matching place found/, {}, { timeout: 2000 })).toBeInTheDocument()
      expect(user.getByText('Find safe route').closest('button')).toBeDisabled()
    })
  })
})
