import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import { formatDuration } from '../lib/responseService'
import ResponseChip from './ResponseChip.jsx'
import ResponseBoard from './ResponseBoard.jsx'
import ResponseSettings from './ResponseSettings.jsx'
import ResponseSlo from './ResponseSlo.jsx'

vi.mock('../useWebSocket', () => ({ default: vi.fn() }))
vi.mock('recharts', () => {
  const Pass = ({ children }) => <div>{children}</div>
  return {
    ResponsiveContainer: Pass, BarChart: Pass, LineChart: Pass, Bar: Pass, Line: Pass, XAxis: () => null,
    YAxis: () => null, CartesianGrid: () => null, Tooltip: () => null, Legend: () => null, Cell: () => null,
  }
})

const mock = new MockAdapter(api)
beforeEach(() => mock.reset())
afterEach(() => { vi.useRealTimers() })

const CLOCK = ['setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'Date', 'performance']

describe('formatDuration', () => {
  it('formats MM:SS and HH:MM:SS and never goes negative', () => {
    expect(formatDuration(75)).toBe('01:15')
    expect(formatDuration(3725)).toBe('1:02:05')
    expect(formatDuration(0)).toBe('00:00')
    expect(formatDuration(-30)).toBe('00:30')
    expect(formatDuration(null)).toBe('00:00')
  })
})

describe('ResponseChip (countdown anchored on the server clock)', () => {
  const base = {
    severity: 'critical', response_stage: 1, escalation_status: 'active', acknowledged: false,
    server_time: '2026-11-14T10:00:00+00:00', response_deadline: '2026-11-14T10:01:00+00:00',
  }

  it('counts down from the SERVER time, ignoring the device clock', () => {
    vi.useFakeTimers({ toFake: CLOCK })
    vi.setSystemTime(new Date('2031-01-01T00:00:00Z')) // a wildly wrong phone clock
    render(<ResponseChip inc={base} stageLabels={['Primary responder', 'Supervisor', 'Higher authority']} />)
    const chip = screen.getByTestId('response-chip')
    expect(chip.textContent).toContain('01:00 to respond')
    expect(chip.textContent).toContain('Primary responder')
    act(() => { vi.advanceTimersByTime(1000) })
    expect(chip.textContent).toContain('00:59')
    expect(chip).toHaveAttribute('data-state', 'counting')
  })

  it('turns to OVERDUE after the deadline', () => {
    vi.useFakeTimers({ toFake: CLOCK })
    render(<ResponseChip inc={base} />)
    act(() => { vi.advanceTimersByTime(65_000) })
    const chip = screen.getByTestId('response-chip')
    expect(chip).toHaveAttribute('data-state', 'overdue')
    expect(chip.textContent).toContain('OVERDUE +00:05')
  })

  it('shows acknowledged within / after target, exhausted and resolved states', () => {
    const { rerender } = render(<ResponseChip inc={{ ...base, acknowledged: true, slo_met: true }} />)
    expect(screen.getByTestId('response-chip').textContent).toContain('within target')
    rerender(<ResponseChip inc={{ ...base, acknowledged: true, slo_met: false }} />)
    expect(screen.getByTestId('response-chip')).toHaveAttribute('data-state', 'acked-late')
    rerender(<ResponseChip inc={{ ...base, escalation_status: 'exhausted' }} />)
    expect(screen.getByTestId('response-chip').textContent).toContain('manual action')
    rerender(<ResponseChip inc={{ ...base, escalation_status: 'closed' }} />)
    expect(screen.getByTestId('response-chip')).toHaveAttribute('data-state', 'closed')
  })

  it('accepts the IncidentOut shape and renders nothing without a deadline', () => {
    const { container, rerender } = render(
      <ResponseChip inc={{ ...base, acknowledged: undefined, acknowledged_at: '2026-11-14T10:00:30', slo_met: true }} />)
    expect(screen.getByTestId('response-chip').textContent).toContain('Acknowledged')
    rerender(<ResponseChip inc={{ severity: 'high' }} />)
    expect(container.textContent).toBe('')
  })
})

const item = (over = {}) => ({
  incident_id: 7, type: 'crash_detected', severity: 'high', status: 'detected', needs_review: false,
  classification_reason: 'Classified as HIGH because a possible crash was detected (suspected, not confirmed).',
  assigned_unit_name: 'Alpha', detected_at: '2026-11-14T10:00:00+00:00', response_deadline: '2026-11-14T10:03:00+00:00',
  response_stage: 1, stage_label: 'Primary responder', next_stage_label: 'Supervisor',
  next_escalation_deadline: '2026-11-14T10:03:00+00:00', global_timeout_deadline: '2026-11-14T10:30:00+00:00',
  escalation_status: 'active', acknowledged: false, server_time: '2026-11-14T10:00:10+00:00', ...over,
})

describe('ResponseBoard', () => {
  beforeEach(() => {
    mock.onGet('/response/config').reply(200, { stage_labels: ['Primary responder', 'Supervisor', 'Higher authority'] })
  })

  it('shows severity, classification reason, stage, deadlines and the summary', async () => {
    mock.onGet('/response/board').reply(200, { server_time: '2026-11-14T10:00:10+00:00', unacknowledged: 1, overdue: 0,
      items: [item()] })
    render(<ResponseBoard />)
    await screen.findByTestId('response-item')
    expect(screen.getByTestId('response-reason').textContent).toMatch(/suspected, not confirmed/)
    expect(screen.getByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText(/Stage 1\/3/)).toBeInTheDocument()
    expect(screen.getByText(/global timeout in/)).toBeInTheDocument()
    expect(screen.getByTestId('response-summary').textContent).toMatch(/1\s*unacknowledged/)
  })

  it('flags items that need human review and shows an empty state', async () => {
    mock.onGet('/response/board').replyOnce(200, { unacknowledged: 0, overdue: 0, items: [item({ needs_review: true })] })
    const { unmount } = render(<ResponseBoard />)
    await screen.findByText('needs review')
    unmount()
    mock.onGet('/response/board').reply(200, { unacknowledged: 0, overdue: 0, items: [] })
    render(<ResponseBoard />)
    await screen.findByText(/No open incidents are waiting/)
  })

  it('acknowledge posts to the existing endpoint and reloads', async () => {
    mock.onGet('/response/board').replyOnce(200, { unacknowledged: 1, overdue: 0, items: [item()] })
    mock.onPost('/incidents/7/acknowledge').reply(200, {})
    mock.onGet('/response/board').reply(200, { unacknowledged: 0, overdue: 0,
      items: [item({ acknowledged: true, slo_met: true, acknowledged_by_name: 'Officer', acknowledged_at: '2026-11-14T10:00:20+00:00' })] })
    render(<ResponseBoard />)
    fireEvent.click(await screen.findByText('Acknowledge'))
    await screen.findByText(/Acknowledged by Officer/)
    expect(mock.history.post.map((r) => r.url)).toContain('/incidents/7/acknowledge')
    expect(screen.queryByText('Acknowledge')).toBeNull()
  })

  it('severity override needs a written reason and sends it', async () => {
    mock.onGet('/response/board').reply(200, { unacknowledged: 1, overdue: 0, items: [item()] })
    mock.onPost('/incidents/7/severity').reply(200, {})
    render(<ResponseBoard isAdmin />)
    fireEvent.click(await screen.findByText('Override severity'))
    const apply = screen.getByText('Apply')
    expect(apply).toBeDisabled()
    fireEvent.change(screen.getByLabelText(/Severity/), { target: { value: 'critical' } })
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: 'witness confirms danger' } })
    expect(apply).not.toBeDisabled()
    fireEvent.click(apply)
    await waitFor(() => expect(mock.history.post.length).toBe(1))
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ severity: 'critical', reason: 'witness confirms danger' })
  })

  it('hides the override control for non-admins and surfaces server errors', async () => {
    mock.onGet('/response/board').reply(200, { unacknowledged: 1, overdue: 0, items: [item()] })
    mock.onPost('/incidents/7/acknowledge').reply(403, { detail: 'Forbidden' })
    render(<ResponseBoard isAdmin={false} />)
    await screen.findByTestId('response-item')
    expect(screen.queryByText('Override severity')).toBeNull()
    fireEvent.click(screen.getByText('Acknowledge'))
    await screen.findByText('Forbidden')
  })

  it('filters by severity', async () => {
    mock.onGet('/response/board').reply(200, { unacknowledged: 2, overdue: 0,
      items: [item(), item({ incident_id: 8, severity: 'low' })] })
    render(<ResponseBoard />)
    await waitFor(() => expect(screen.getAllByTestId('response-item')).toHaveLength(2))
    fireEvent.change(screen.getByLabelText('Filter by severity'), { target: { value: 'low' } })
    expect(screen.getAllByTestId('response-item')).toHaveLength(1)
    expect(screen.getByTestId('response-item')).toHaveAttribute('data-incident', '8')
  })
})

describe('ResponseSettings', () => {
  const cfg = {
    response_targets_seconds: { critical: 60, high: 180, medium: 300, low: 600 }, stage2_seconds: 120,
    global_timeout_seconds: 1800, warning_seconds: 0, stage_labels: ['Primary responder', 'Supervisor', 'Higher authority'],
    audiences: { 1: ['assigned_unit'], 2: ['responders'], 3: ['admins'], global: ['admins'] }, updated_at: null,
  }

  it('loads the current values, labels them illustrative, and saves what was typed', async () => {
    mock.onGet('/response/config').reply(200, cfg)
    mock.onPut('/response/config').reply(200, { ...cfg, response_targets_seconds: { ...cfg.response_targets_seconds, critical: 45 } })
    render(<ResponseSettings />)
    const critical = await screen.findByLabelText(/critical/i)
    expect(critical.value).toBe('60')
    expect(screen.getByText(/Illustrative demonstration values/)).toBeInTheDocument()
    fireEvent.change(critical, { target: { value: '45' } })
    fireEvent.click(screen.getByText('Save settings'))
    await screen.findByRole('status')
    const sent = JSON.parse(mock.history.put[0].data)
    expect(sent.response_targets_seconds.critical).toBe(45)
    expect(sent.global_timeout_seconds).toBe(1800)
    expect(screen.getByLabelText(/critical/i).value).toBe('45')
  })

  it('shows the server validation message and keeps the form', async () => {
    mock.onGet('/response/config').reply(200, cfg)
    mock.onPut('/response/config').reply(422, { detail: 'targets must not get longer for a more serious severity' })
    render(<ResponseSettings />)
    fireEvent.click(await screen.findByText('Save settings'))
    await screen.findByRole('alert')
    expect(screen.getByRole('alert').textContent).toMatch(/must not get longer/)
  })
})

describe('ResponseSlo', () => {
  const slo = (over = {}) => ({
    overall: { received: 4, acknowledged: 3, within_target: 2, after_target: 1, unacknowledged: 1, overdue: 1, escalated: 1,
      global_timeouts: 0, compliance_pct: 66.7, avg_ack_seconds: 75, median_ack_seconds: 60 },
    active_incidents: 2, active_unacknowledged: 1, active_overdue: 1,
    by_severity: { critical: { compliance_pct: 50, avg_ack_seconds: 40 }, high: { compliance_pct: 100, avg_ack_seconds: 90 },
      medium: { compliance_pct: null, avg_ack_seconds: null }, low: { compliance_pct: null, avg_ack_seconds: null } },
    by_type: [{ type: 'crash_detected', received: 2, acknowledged: 2, compliance_pct: 50, avg_ack_seconds: 70 }],
    trend: [{ date: '2026-11-14', received: 4, acknowledged: 3, avg_ack_seconds: 75, compliance_pct: 66.7, escalations: 1 }],
    distribution: [{ label: '≤30s', count: 1 }], ...over,
  })

  it('shows overall and per-severity SLO, with — where nothing was acknowledged', async () => {
    mock.onGet('/response/slo').reply(200, slo())
    mock.onGet('/police-units').reply(200, [{ id: 3, name: 'Alpha' }])
    render(<ResponseSlo />)
    await waitFor(() => expect(screen.getByTestId('slo-stats').textContent).toContain('66.7%'))
    const stats = screen.getByTestId('slo-stats').textContent
    expect(stats).toContain('50%')      // critical
    expect(stats).toContain('100%')     // high
    expect(stats).toContain('01:15')    // average response
    expect(stats).toContain('—')        // medium/low: no data, NOT 0% or 100%
    expect(screen.getByTestId('slo-method').textContent).toMatch(/never counted as successes/)
    expect(screen.getByTestId('slo-by-type').textContent).toContain('crash detected')
  })

  it('refetches with the chosen filters', async () => {
    mock.onGet('/response/slo').reply(200, slo())
    mock.onGet('/police-units').reply(200, [{ id: 3, name: 'Alpha' }])
    render(<ResponseSlo />)
    await screen.findByTestId('slo-stats')
    fireEvent.change(screen.getByLabelText('Severity filter'), { target: { value: 'high' } })
    fireEvent.change(screen.getByLabelText('Team filter'), { target: { value: '3' } })
    await waitFor(() => {
      const last = mock.history.get.filter((r) => r.url === '/response/slo').at(-1)
      expect(last.params).toMatchObject({ severity: 'high', unit_id: '3' })
      expect(last.params.from).toMatch(/T00:00:00Z$/)
    })
  })

  it('shows the server error for an invalid range', async () => {
    mock.onGet('/response/slo').reply(422, { detail: 'start must not be after end' })
    mock.onGet('/police-units').reply(200, [])
    render(<ResponseSlo />)
    await screen.findByText('start must not be after end')
  })
})
