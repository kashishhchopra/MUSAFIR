import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, waitFor, screen, act } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import '../i18n' // initializes i18next so t() resolves real strings, not raw keys
import api from '../api'
import EmergencyModeCard, { RESOLVED_DISMISS_MS } from './EmergencyModeCard'

let wsCallback = null
vi.mock('../useWebSocket', () => ({
  default: (cb) => { wsCallback = cb; return { connected: true } },
}))

const mock = new MockAdapter(api)

beforeEach(() => {
  mock.reset()
  wsCallback = null
})

const sosSent = {
  incident_id: 7, silent: false, status: 'dispatched', live_tracking_active: true,
  station_id: 1, station_name: 'Central Police Station',
  nearest_unit: { name: 'Unit Alpha', station: 'Central PS', distance_km: 1.2 },
}

describe('EmergencyModeCard', () => {
  it('renders nothing when there is no active SOS', () => {
    const { container } = render(<EmergencyModeCard sosSent={null} tid={1} posRef={{ current: [26.1, 91.7] }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('never shows for a silent/duress SOS, even though tracking is still active underneath', () => {
    const { container } = render(
      <EmergencyModeCard sosSent={{ ...sosSent, silent: true }} tid={1} posRef={{ current: [26.1, 91.7] }} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows Emergency Mode with station, response and demo-mode disclosure for a visible SOS', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    const { findByText } = render(
      <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
    )
    await findByText('🚨 Emergency Mode')
    expect(screen.getByText('Central Police Station')).toBeInTheDocument()
    expect(screen.getByText('Officer assigned')).toBeInTheDocument()
    // SIMULATE_GPS defaults on in this test env -- must say so, never claim a real fix.
    await findByText(/Demo mode/)
  })

  it('goes LIVE once the first location post succeeds', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    render(
      <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
    )
    await waitFor(() => expect(screen.getAllByText('LIVE').length).toBeGreaterThan(0))
  })

  it('requires a second tap to actually cancel the emergency', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    mock.onPost('/incidents/7/stop-tracking').reply(200, { status: 'stopped' })
    const { findByText, getByText } = render(
      <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
    )
    await findByText('🚨 Emergency Mode')
    fireEvent.click(getByText('Cancel Emergency'))
    expect(getByText('Tap again to confirm')).toBeInTheDocument()
    expect(mock.history.post.filter((r) => r.url === '/incidents/7/stop-tracking')).toHaveLength(0)

    fireEvent.click(getByText('Tap again to confirm'))
    await waitFor(() => expect(mock.history.post.some((r) => r.url === '/incidents/7/stop-tracking')).toBe(true))
    await findByText('Emergency Resolved')
  })

  it('shows "Emergency Resolved" once police/operator stop tracking remotely', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    const { findByText } = render(
      <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
    )
    await findByText('🚨 Emergency Mode')
    act(() => wsCallback({ event: 'emergency_tracking_stopped', incident_id: 7, reason: 'incident resolved' }))
    await findByText('Live emergency location sharing has stopped.')
  })

  describe('after it is resolved', () => {
    afterEach(() => vi.useRealTimers())

    const renderAndResolve = async (props = {}) => {
      mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
      const utils = render(
        <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} {...props} />
      )
      await utils.findByText('🚨 Emergency Mode')
      vi.useFakeTimers()
      act(() => wsCallback({ event: 'emergency_tracking_stopped', incident_id: 7 }))
      expect(screen.getByText('Emergency Resolved')).toBeInTheDocument()
      return utils
    }

    it('hides the "Emergency Resolved" card after 5 seconds', async () => {
      const { container } = await renderAndResolve()
      act(() => vi.advanceTimersByTime(RESOLVED_DISMISS_MS - 100))
      expect(screen.getByText('Emergency Resolved')).toBeInTheDocument()
      act(() => vi.advanceTimersByTime(200))
      expect(container).toBeEmptyDOMElement()
    })

    it('stops posting locations once resolved', async () => {
      await renderAndResolve()
      const posts = () => mock.history.post.filter((r) => r.url === '/incidents/7/location').length
      const before = posts()
      act(() => vi.advanceTimersByTime(30000))
      expect(posts()).toBe(before)
    })

    it('still shows the card for a new SOS after an earlier one was dismissed', async () => {
      const { container, rerender } = await renderAndResolve()
      act(() => vi.advanceTimersByTime(RESOLVED_DISMISS_MS + 100))
      expect(container).toBeEmptyDOMElement()
      vi.useRealTimers()

      mock.onPost('/incidents/8/location').reply(201, { status: 'ok' })
      rerender(<EmergencyModeCard sosSent={{ ...sosSent, incident_id: 8 }} tid={1} posRef={{ current: [26.1445, 91.7362] }} />)
      await screen.findByText('🚨 Emergency Mode')
      await waitFor(() => expect(mock.history.post.some((r) => r.url === '/incidents/8/location')).toBe(true))
    })
  })
})
