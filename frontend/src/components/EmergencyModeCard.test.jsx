import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, waitFor, screen, act, fireEvent } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import '../i18n' // initializes i18next so t() resolves real strings, not raw keys
import api from '../api'
import EmergencyModeCard, { RESOLVED_DISMISS_MS, CLOSE_STEP_DELAY_MS } from './EmergencyModeCard'

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

  it('offers a close-SOS button, but never closes anything without the three confirmations', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    const { findByText } = render(
      <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
    )
    await findByText('🚨 Emergency Mode')
    expect(screen.getByRole('button', { name: /I'm safe/ })).toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByText('Call Emergency Services')).toBeInTheDocument()
    // The old "only the control room can close this" note is gone; nothing is sent by merely rendering.
    expect(screen.queryByText(/Only the control room can close this emergency/)).not.toBeInTheDocument()
    expect(mock.history.post.some((r) => r.url.includes('stop-tracking') || r.url.includes('/sos/cancel'))).toBe(false)
  })

  it('a repeat SOS press on the same open case restarts sharing after it was stopped', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    const first = { ...sosSent, pressed_at: 1000 }
    const { findByText, rerender } = render(
      <EmergencyModeCard sosSent={first} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
    )
    await findByText('🚨 Emergency Mode')
    act(() => wsCallback({ event: 'emergency_tracking_stopped', incident_id: 7, reason: 'stopped by the control room' }))
    await findByText('Emergency Resolved')
    const before = mock.history.post.filter((r) => r.url === '/incidents/7/location').length

    // Same incident id (the server merged the tap), but a new press.
    rerender(<EmergencyModeCard sosSent={{ ...sosSent, pressed_at: 2000 }} tid={1} posRef={{ current: [26.1445, 91.7362] }} />)
    await findByText('🚨 Emergency Mode')
    await waitFor(() => expect(
      mock.history.post.filter((r) => r.url === '/incidents/7/location').length).toBeGreaterThan(before))
  })

  it('shows "Emergency Resolved" once police/operator stop tracking remotely', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    const { findByText } = render(
      <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
    )
    await findByText('🚨 Emergency Mode')
    act(() => wsCallback({ event: 'emergency_tracking_stopped', incident_id: 7, reason: 'incident resolved' }))
    await findByText(/The control room has closed this emergency\. Press SOS again/)
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


describe('closing the SOS from the tourist side (three confirmations)', () => {
  const renderCard = () => render(
    <EmergencyModeCard sosSent={sosSent} tid={1} posRef={{ current: [26.1445, 91.7362] }} />
  )
  const cancelCalls = () => mock.history.post.filter((r) => r.url === '/tourists/1/sos/cancel')
  // A "continue" button is deliberately disabled for a moment after each screen appears.
  const press = async (name) => {
    const btn = await screen.findByRole('button', { name })
    await waitFor(() => expect(btn).toBeEnabled(), { timeout: CLOSE_STEP_DELAY_MS + 3000 })
    fireEvent.click(btn)
  }
  const openDialog = async () => {
    fireEvent.click(await screen.findByRole('button', { name: /I'm safe/ }))
    await screen.findByRole('alertdialog')
  }

  it('asks three separate times, and only the third calls the server', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    mock.onPost('/tourists/1/sos/cancel').reply(200, { incident_id: 7, status: 'resolved' })
    renderCard()
    await openDialog()

    expect(screen.getByText('Confirmation 1 of 3')).toBeInTheDocument()
    expect(screen.getByText('Close this SOS?')).toBeInTheDocument()
    await press('Yes, continue')

    expect(await screen.findByText('Confirmation 2 of 3')).toBeInTheDocument()
    expect(screen.getByText('Are you really safe?')).toBeInTheDocument()
    expect(cancelCalls()).toHaveLength(0)
    await press('Yes, continue')

    expect(await screen.findByText('Confirmation 3 of 3')).toBeInTheDocument()
    expect(screen.getByText('Final confirmation')).toBeInTheDocument()
    expect(cancelCalls()).toHaveLength(0)
    await press('Yes, close SOS now')

    await waitFor(() => expect(cancelCalls()).toHaveLength(1))
    expect(JSON.parse(cancelCalls()[0].data)).toEqual({ confirmations: 3 })
    await screen.findByText('Emergency Resolved')
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  }, 20000)

  it('a rapid triple-tap cannot skip the steps: continue is disabled right after each screen appears', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    renderCard()
    await openDialog()
    const btn = screen.getByRole('button', { name: 'Yes, continue' })
    expect(btn).toBeDisabled()
    fireEvent.click(btn)
    fireEvent.click(btn)
    fireEvent.click(btn)
    expect(screen.getByText('Confirmation 1 of 3')).toBeInTheDocument()
    expect(cancelCalls()).toHaveLength(0)
  })

  it('"No, keep SOS active" closes the dialog and sends nothing', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    renderCard()
    await openDialog()
    await press('Yes, continue')
    await screen.findByText('Confirmation 2 of 3')
    fireEvent.click(screen.getByRole('button', { name: 'No, keep SOS active' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.getByText('🚨 Emergency Mode')).toBeInTheDocument()
    expect(cancelCalls()).toHaveLength(0)
  }, 15000)

  it('Escape also keeps the SOS active', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    renderCard()
    await openDialog()
    fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(cancelCalls()).toHaveLength(0)
  })

  it('when the server cannot close it, says the SOS is still active and does not pretend', async () => {
    mock.onPost('/incidents/7/location').reply(201, { status: 'ok' })
    mock.onPost('/tourists/1/sos/cancel').reply(500, { detail: 'boom' })
    renderCard()
    await openDialog()
    await press('Yes, continue')
    await screen.findByText('Confirmation 2 of 3')
    await press('Yes, continue')
    await screen.findByText('Confirmation 3 of 3')
    await press('Yes, close SOS now')

    expect(await screen.findByRole('alert')).toHaveTextContent(/still active/)
    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    expect(screen.queryByText('Emergency Resolved')).not.toBeInTheDocument()
  }, 20000)
})
