import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../../api'
import PrivacyAdmin from './PrivacyAdmin.jsx'

const mock = new MockAdapter(api)

const policies = [
  { category: 'location_history', title: 'Location history', legal_basis: 'Consent', retention_days: 90, deletion_action: 'delete', account_deletion_action: 'delete', is_active: true, requires_review: true, automated: true, updated_at: 'a' },
  { category: 'notification_logs', title: 'Notification log', legal_basis: 'Operational', retention_days: null, deletion_action: 'delete', account_deletion_action: 'anonymize', is_active: false, requires_review: true, automated: true, updated_at: 'b' },
]

function seed() {
  mock.onGet('/admin/privacy/retention/policies').reply(200, policies)
  mock.onGet('/admin/privacy/retention/exceptions').reply(200, [
    { id: 4, category: 'location_history', tourist_id: 9, reason: 'Police request', basis: 'Notice 9', review_by: '2026-12-01T00:00:00', active: true, review_overdue: true }])
  mock.onGet('/admin/privacy/retention/jobs').reply(200, {
    recent_jobs: [{ job_id: 'DEL-1', category: 'location_history', started_at: '2026-11-15T10:00:00', status: 'failed', failure_reason: 'RuntimeError', records_processed: 0, records_deleted: 0, records_retained: 0 }],
    failed_jobs: 1, active_exceptions: 1, exceptions_overdue_for_review: 1, policies_needing_review: 2 })
  mock.onGet('/admin/privacy/deletion-requests').reply(200, [
    { request_id: 'DEL-9', label: 'Partially retained under a documented exception', attempts: 1, retained: [{ category: 'sos_incident_records' }], failure_reason: null }])
  mock.onGet('/admin/privacy/grievances').reply(200, [
    { grievance_id: 'GRV-1', category: 'location', submitted_at: '2026-11-14T10:00:00', status: 'open', overdue: true }])
}

beforeEach(() => { mock.reset(); seed() })

describe('PrivacyAdmin', () => {
  it('lists policies with "none" for categories without a period, and flags overdue reviews/failures', async () => {
    render(<PrivacyAdmin />)
    const days = await screen.findByLabelText('Retention days for notification_logs')
    expect(days).toHaveValue(null)
    expect(screen.getByLabelText('Retention days for location_history')).toHaveValue(90)
    expect(screen.getByText(/REVIEW OVERDUE/)).toBeInTheDocument()
    expect(screen.getByText(/RuntimeError/)).toBeInTheDocument()
    expect(screen.getByText(/1 failed/)).toBeInTheDocument()
  })

  it('saving a policy sends only the edited configuration to the backend', async () => {
    render(<PrivacyAdmin />)
    const days = await screen.findByLabelText('Retention days for notification_logs')
    fireEvent.change(days, { target: { value: '30' } })
    fireEvent.click(screen.getByLabelText('Scheduled deletion for notification_logs'))
    fireEvent.click(screen.getByLabelText('Legal review done for notification_logs'))
    mock.onPatch('/admin/privacy/retention/policies/notification_logs').reply(200, {})
    const row = days.closest('tr')
    fireEvent.click(within(row).getByText('Save'))
    await screen.findByText('Privacy & Data Retention')
    await new Promise((r) => setTimeout(r, 20))
    expect(JSON.parse(mock.history.patch[0].data)).toEqual({ retention_days: 30, is_active: true, requires_review: false })
  })

  it('shows the backend validation error when a policy is rejected', async () => {
    render(<PrivacyAdmin />)
    const days = await screen.findByLabelText('Retention days for notification_logs')
    fireEvent.click(screen.getByLabelText('Scheduled deletion for notification_logs'))
    mock.onPatch('/admin/privacy/retention/policies/notification_logs').reply(400, { detail: 'Set retention_days before switching scheduled deletion on.' })
    fireEvent.click(within(days.closest('tr')).getByText('Save'))
    await screen.findByText(/Set retention_days before switching/)
  })

  it('records an exception with reason, basis and a review window', async () => {
    render(<PrivacyAdmin />)
    await screen.findByLabelText('Exception reason')
    fireEvent.change(screen.getByLabelText('Exception reason'), { target: { value: 'Court hold' } })
    fireEvent.change(screen.getByLabelText('Exception basis'), { target: { value: 'Order 7' } })
    mock.onPost('/admin/privacy/retention/exceptions').reply(201, {})
    fireEvent.click(screen.getByText('Add exception'))
    await screen.findByText('Exception recorded.')
    expect(JSON.parse(mock.history.post[0].data)).toMatchObject({ category: 'location_history', reason: 'Court hold', basis: 'Order 7', review_days: 90, tourist_id: null })
  })

  it('runs retention and processes due deletions through the real endpoints', async () => {
    render(<PrivacyAdmin />)
    mock.onPost('/admin/privacy/retention/run').reply(200, { jobs: [{}, {}] })
    mock.onPost('/admin/privacy/deletion-requests/process').reply(200, { processed: ['DEL-9:completed'] })
    fireEvent.click(await screen.findByText('Run retention now'))
    await screen.findByText('Ran 2 job(s).')
    fireEvent.click(screen.getByText('Process due now'))
    await screen.findByText('Processed 1 deletion request(s).')
  })

  it('responds to a grievance and changes its status', async () => {
    render(<PrivacyAdmin />)
    fireEvent.click(await screen.findByText(/GRV-1/))
    mock.onGet('/admin/privacy/grievances/GRV-1').reply(200, { grievance_id: 'GRV-1', description: 'Still tracked', responses: [] })
    await screen.findByText('Still tracked')
    fireEvent.change(screen.getByLabelText('Response message'), { target: { value: 'Fixed.' } })
    fireEvent.change(screen.getByLabelText('New status'), { target: { value: 'resolved' } })
    mock.onPost('/admin/privacy/grievances/GRV-1/respond').reply(200, { grievance_id: 'GRV-1', description: 'Still tracked', responses: [{ author_role: 'admin', message: 'Fixed.', created_at: '2026-11-15T10:00:00' }] })
    fireEvent.click(screen.getByText('Respond'))
    await screen.findByText('Fixed.')
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ message: 'Fixed.', status: 'resolved' })
  })
})
