import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import '../../../i18n'
import ExploreTab from './ExploreTab.jsx'

// Heavy cards are stubbed -- this test is about which props/cards the tab gives them.
vi.mock('../../../components/TripPlannerCard.jsx', () => ({ default: (p) => <div data-testid="trip" data-lat={p.lat} data-lng={p.lng} /> }))
vi.mock('../../../components/CrowdForecastCard.jsx', () => ({ default: (p) => <div data-testid="crowd" data-lat={p.lat} data-lng={p.lng} /> }))
vi.mock('../../../components/DiscoveryCard.jsx', () => ({ default: (p) => <div data-testid="discovery" data-lat={p.lat} data-lng={p.lng} /> }))
vi.mock('../../../components/OfflineTripCard.jsx', () => ({ default: (p) => <div data-testid="offline" data-lat={p.lat} data-lng={p.lng} /> }))
vi.mock('../../../components/TwoWayVoiceTranslator.jsx', () => ({ default: (p) => <div data-testid="voice" data-lat={p.lat} data-lng={p.lng} /> }))
vi.mock('../../../components/FairPriceCard.jsx', () => ({ default: (p) => <div data-testid="fare" data-lat={p.lat} data-lng={p.lng} /> }))
vi.mock('../../../components/CulturalEtiquetteCard.jsx', () => ({ default: (p) => <div data-testid="etiquette" data-lat={p.lat} data-lng={p.lng} /> }))
vi.mock('../../../components/CurrencyCard.jsx', () => ({ default: (p) => <div data-testid="currency" data-lat={p.lat} data-lng={p.lng} /> }))

afterEach(() => vi.restoreAllMocks())

describe('ExploreTab removed features', () => {
  it('no longer shows the Multilingual Guide, Festival & Local Events or Permit & E-Pass cards', () => {
    const { queryByText, queryByTestId } = render(<ExploreTab data={{ me: { last_lat: 12.5, last_lng: 77.5 }, tid: 1 }} />)
    expect(queryByText('Multilingual Guide')).not.toBeInTheDocument()
    expect(queryByText(/Festival & Local Events/)).not.toBeInTheDocument()
    expect(queryByText(/Permit & E-Pass/)).not.toBeInTheDocument()
    expect(queryByTestId('permit')).not.toBeInTheDocument()
  })
})

describe('ExploreTab location handling', () => {
  it("passes the tourist's real tracked position to the cards that need it", () => {
    const { getByTestId, queryByText } = render(
      <ExploreTab data={{ me: { last_lat: 12.5, last_lng: 77.5 }, tid: 1 }} />)
    expect(getByTestId('offline').dataset.lat).toBe('12.5')
    expect(getByTestId('offline').dataset.lng).toBe('77.5')
    expect(queryByText('Near you')).not.toBeInTheDocument()
  })

  it('always shows Discovery, which finds its own position (never a stored or default one)', () => {
    const { getByTestId } = render(<ExploreTab data={{ me: { last_lat: 12.5, last_lng: 77.5 }, tid: 1 }} />)
    expect(getByTestId('discovery')).toBeInTheDocument()
    expect(getByTestId('discovery').dataset.lat).toBeUndefined() // given no coordinates by the tab
    const again = render(<ExploreTab data={{ me: { last_lat: null, last_lng: null }, tid: 1 }} />)
    expect(again.getAllByTestId('discovery').length).toBeGreaterThan(0)
  })

  it('without any real position the other location cards ask for it instead of using a default map centre', () => {
    const { queryByTestId, getByText, getByTestId } = render(
      <ExploreTab data={{ me: { last_lat: null, last_lng: null }, tid: 1 }} />)
    expect(getByText('Near you')).toBeInTheDocument()
    expect(queryByTestId('offline')).not.toBeInTheDocument()
    expect(getByTestId('fare')).toBeInTheDocument() // cards that don't need it still render
  })

  it('shows the other location-based cards once the device provides a fix', async () => {
    Object.defineProperty(navigator, 'geolocation', {
      value: { getCurrentPosition: (ok) => ok({ coords: { latitude: 9.9, longitude: 76.2, accuracy: 5 }, timestamp: Date.now() }) },
      configurable: true,
    })
    const { getByText, findByTestId } = render(
      <ExploreTab data={{ me: { last_lat: null, last_lng: null }, tid: 1 }} />)
    fireEvent.click(getByText(/Use my current location/))
    const o = await findByTestId('offline')
    await waitFor(() => expect(o.dataset.lat).toBe('9.9'))
  })
})
