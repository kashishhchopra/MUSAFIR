import { describe, it, beforeEach, expect } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import MockAdapter from 'axios-mock-adapter'
import api from '../../api'
import Notifications from './Notifications'

const mock = new MockAdapter(api)

const row = (o) => ({
  id: 1, created_at: new Date().toISOString(), event_type: 'sos', channel: 'push', recipient_type: 'admin',
  recipient: 'Admin Control Room Officer', incident_id: 7, tourist_id: 3, title: '🚨 SOS ALERT — Rahul',
  body: 'Rahul has triggered an emergency SOS.', url: '/admin/incidents?incident=7', severity: 'critical',
  status: 'sent', attempts: 1, last_error: null, next_retry_at: null, provider_id: null, demo: false,
  sent_at: new Date().toISOString(), ...o,
})
const items = [
  row({ id: 1 }),
  row({ id: 2, channel: 'sms', recipient: 'Dad · •••• 3210', status: 'simulated', demo: true, title: 'MUSAFIR SOS',
    body: 'MUSAFIR SOS: Rahul has triggered an SOS.' }),
  row({ id: 3, status: 'failed', last_error: 'subscription expired (410)', recipient: 'Guardian Mom' }),
  row({ id: 4, status: 'retrying', attempts: 1, last_error: 'push service 503', next_retry_at: new Date(Date.now() + 30000).toISOString() }),
]
const summary = {
  total: 4, by_status: { sent: 1, simulated: 1, failed: 1, retrying: 1, suppressed: 2 }, by_channel: { push: 3, sms: 1 },
  subscribers: { total: 3, by_role: { admin: 1, tourist: 1, guardian: 1 } },
  config: { push: { enabled: true, mode: 'auto-generated (dev)' },
    sms: { live: false, mode: 'demo (console only — nothing is texted)', allowlist_active: false } },
}

const renderPage = () => render(<MemoryRouter><Notifications /></MemoryRouter>)

beforeEach(() => {
  mock.reset()
  mock.onGet('/notifications/summary').reply(200, summary)
  mock.onGet('/notifications/logs').reply((cfg) => {
    const p = cfg.params || {}
    if (p.limit === 1) return [200, { total: 1, items: p.channel === 'sms' ? [items[1]] : [items[0]] }]
    return [200, { total: items.length, items: p.status ? items.filter((i) => i.status === p.status) : items }]
  })
})

describe('Push & SMS alerts page', () => {
  it('shows delivery status per notification, honestly labelling demo SMS', async () => {
    const { findAllByText, getByText, getAllByText } = renderPage()
    expect((await findAllByText(/🚨 SOS ALERT — Rahul/)).length).toBeGreaterThan(0)
    expect(getByText(/Simulated \(demo\)/, { selector: 'span.font-semibold' })).toBeTruthy()
    expect(getByText(/Failed/, { selector: 'span.font-semibold' })).toBeTruthy()
    expect(getByText(/Retrying/, { selector: 'span.font-semibold' })).toBeTruthy()
    expect(getByText('subscription expired (410)')).toBeTruthy()
    expect(getAllByText('#7')[0].getAttribute('href')).toBe('/admin/incidents?incident=7')
  })

  it('shows the push and SMS config and the previews of what recipients see', async () => {
    const { findByText, getByText } = renderPage()
    await findByText('Push: auto-generated (dev)')
    expect(getByText('SMS: demo (console only — nothing is texted)')).toBeTruthy()
    expect(getByText(/as shown on a lock screen/)).toBeTruthy()
    expect(getByText(/as an emergency contact receives it/)).toBeTruthy()
  })

  it('filtering by status re-queries the log', async () => {
    const { findAllByText, getByLabelText } = renderPage()
    await findAllByText(/SOS ALERT/)
    fireEvent.change(getByLabelText('Status'), { target: { value: 'failed' } })
    await waitFor(() => expect(mock.history.get.some((r) => r.url === '/notifications/logs' && r.params?.status === 'failed')).toBe(true))
  })

  it('offers Retry for failed, retrying and simulated rows, and posts to that row\'s endpoint', async () => {
    mock.onPost(/\/notifications\/logs\/\d+\/retry/).reply(200, {})
    const { findAllByText } = renderPage()
    const retries = await findAllByText('Retry')
    expect(retries).toHaveLength(3)                      // rows 2 (simulated), 3 (failed), 4 (retrying); not row 1 (sent)
    fireEvent.click(retries[1])                          // the failed row
    await waitFor(() => expect(mock.history.post.some((r) => r.url === '/notifications/logs/3/retry')).toBe(true))
  })

  it('sends a test SMS to the typed number', async () => {
    mock.onPost('/notifications/test').reply(200, { items: [] })
    const { findAllByText, getByLabelText, getByText } = renderPage()
    await findAllByText(/SOS ALERT/)
    fireEvent.change(getByLabelText('Test phone number'), { target: { value: '+919876500000' } })
    fireEvent.click(getByText('Send test SMS'))
    await waitFor(() => {
      const r = mock.history.post.find((x) => x.url === '/notifications/test')
      expect(JSON.parse(r.data)).toEqual({ channel: 'sms', to: '+919876500000' })
    })
  })
})
