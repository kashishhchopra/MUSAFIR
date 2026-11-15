import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import PrivacyDashboard from './PrivacyDashboard.jsx'
import RegistrationConsent, { EMPTY_CONSENT, acksComplete, consentPayload } from './RegistrationConsent.jsx'

const mock = new MockAdapter(api)

const consents = [
  { key: 'basic_profile', title: 'Basic profile & Digital ID', kind: 'notice', description: 'd', shared_with: 'Responders', basis_note: 'Needed', withdraw_effect: 'n/a', status: 'not_recorded' },
  { key: 'location_tracking', title: 'Location tracking', kind: 'consent', description: 'Phone location', shared_with: 'Nobody', basis_note: 'Optional', withdraw_effect: 'Safety scoring stops. SOS still works.', status: 'granted', consented_at: '2026-11-15T10:00:00', notice_version: '1.0', needs_review: false },
  { key: 'guardian_sharing', title: 'Share with a guardian', kind: 'consent', description: 'g', shared_with: 'Guardian', basis_note: 'Optional', withdraw_effect: 'Links stop.', status: 'not_recorded' },
]
const overview = {
  notice: { version: '1.0', updated: '2026-11-15', legal_note: 'Not a certificate of legal compliance.', contact_email: 'privacy@example.org', rights: ['See what we hold about you.'] },
  consents,
  data_inventory: [{ category: 'location_history', items: ['Location history'], tables: ['location_pings'], purpose: 'Safety scoring', necessary: 'optional', who_can_access: 'You', shared_with: 'Not shared', retention: null }],
  retention: [
    { category: 'location_history', title: 'Location history', purpose: 'Safety scoring', legal_basis: 'Consent', retention_days: 90, start_condition: 'trip_end', state: 'Scheduled deletion on', per_tourist: true, requires_review: true },
    { category: 'tourist_profile', title: 'Tourist profile', purpose: 'Digital ID', legal_basis: 'Service', retention_days: null, start_condition: 'trip_end', state: 'No period established - not deleted automatically', per_tourist: false, requires_review: true },
  ],
  holds_on_my_data: [],
  location: { tracking_enabled: true, data_retention_days: 90 },
  grievance_response_days: 30,
  deletion: null,
}

async function openDashboard() {
  mock.onGet('/tourists/1/privacy/overview').reply(200, overview)
  render(<PrivacyDashboard touristId={1} onSignOut={vi.fn()} />)
  fireEvent.click(screen.getByText('Privacy Dashboard'))
  await screen.findByRole('tablist')
}
const tab = (name) => fireEvent.click(screen.getByRole('tab', { name }))

beforeEach(() => { mock.reset(); window.URL.createObjectURL = vi.fn(() => 'blob:x'); window.URL.revokeObjectURL = vi.fn() })

describe('Privacy Dashboard', () => {
  it('loads nothing until opened, then shows notice, data collected and the no-certification note', async () => {
    mock.onGet('/tourists/1/privacy/overview').reply(200, overview)
    render(<PrivacyDashboard touristId={1} />)
    expect(mock.history.get).toHaveLength(0)
    fireEvent.click(screen.getByText('Privacy Dashboard'))
    await screen.findByText(/version 1.0/)
    expect(screen.getByText('Location history')).toBeInTheDocument()
    expect(screen.getByText(/Not a certificate of legal compliance/)).toBeInTheDocument()
  })

  it('shows consent status per purpose; notice-only purposes have no withdraw button', async () => {
    await openDashboard()
    tab('Consents')
    expect(within(screen.getByTestId('consent-location_tracking')).getByText('Allowed')).toBeInTheDocument()
    expect(within(screen.getByTestId('consent-guardian_sharing')).getByText('Not set')).toBeInTheDocument()
    expect(within(screen.getByTestId('consent-basic_profile')).queryByText(/Withdraw/)).toBeNull()
    expect(within(screen.getByTestId('consent-basic_profile')).getByText('Required')).toBeInTheDocument()
  })

  it('withdrawal warns what stops first, then calls the backend', async () => {
    await openDashboard()
    tab('Consents')
    mock.onPost('/tourists/1/consents/location_tracking/withdraw').reply(200, { status: 'withdrawn', message: 'Background location updates stop.', effects: ['location_collection_stopped'] })
    fireEvent.click(within(screen.getByTestId('consent-location_tracking')).getByText('Withdraw consent'))
    expect(screen.getByText(/Safety scoring stops. SOS still works./)).toBeInTheDocument()
    expect(mock.history.post).toHaveLength(0)                               // nothing sent until confirmed
    fireEvent.click(screen.getByText('Yes, withdraw'))
    await screen.findByText('Background location updates stop.')
    expect(mock.history.post[0].url).toBe('/tourists/1/consents/location_tracking/withdraw')
  })

  it('granting is an explicit click and shows backend errors', async () => {
    await openDashboard()
    tab('Consents')
    mock.onPost('/tourists/1/consents').reply(400, { detail: 'Unknown consent purpose.' })
    fireEvent.click(within(screen.getByTestId('consent-guardian_sharing')).getByText('Allow'))
    await screen.findByText('Unknown consent purpose.')
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ purpose: 'guardian_sharing' })
  })

  it('shows consent history', async () => {
    await openDashboard()
    tab('Consents')
    mock.onGet('/tourists/1/consents/history').reply(200, [
      { consent_id: 'CNS-1', purpose: 'location_tracking', status: 'withdrawn', consent_version: 1, notice_version: '1.0', consented_at: '2026-11-15T10:00:00', withdrawn_at: '2026-11-15T11:00:00', source: 'privacy_dashboard' }])
    fireEvent.click(screen.getByText('View consent history'))
    await screen.findByText('CNS-1')
  })

  it('location: lists records, asks for confirmation, then deletes everything via the backend', async () => {
    await openDashboard()
    mock.onGet('/tourists/1/location-history').reply(200, {
      total: 2, limit: 25, offset: 0, available_from: '2026-11-14T00:00:00', available_to: '2026-11-15T00:00:00', tracking_enabled: true,
      items: [{ id: 7, lat: 26.1, lng: 91.7, timestamp: '2026-11-15T00:00:00', speed_kmh: 1, is_anomaly: false }, { id: 8, lat: 26.2, lng: 91.8, timestamp: '2026-11-14T00:00:00', speed_kmh: 1, is_anomaly: false }] })
    tab('Location')
    await screen.findByLabelText('Select record 7')
    expect(screen.getByLabelText('Location history').children).toHaveLength(2)
    mock.onPost('/tourists/1/location-history/delete').reply(200, { deleted: 2, retained: 0 })
    fireEvent.click(screen.getByText('Delete all'))
    expect(screen.getByRole('alertdialog')).toHaveTextContent(/cannot be undone/)
    expect(mock.history.post).toHaveLength(0)
    fireEvent.click(screen.getByText('Yes, delete'))
    await screen.findByText(/2 location record\(s\) deleted/)
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ delete_all: true })
  })

  it('location: deletes only the ticked records and reports what an open emergency kept', async () => {
    await openDashboard()
    mock.onGet('/tourists/1/location-history').reply(200, {
      total: 2, limit: 25, offset: 0, available_from: null, available_to: null, tracking_enabled: true,
      items: [{ id: 7, lat: 26.1, lng: 91.7, timestamp: '2026-11-15T00:00:00' }, { id: 8, lat: 26.2, lng: 91.8, timestamp: '2026-11-14T00:00:00' }] })
    tab('Location')
    await screen.findByLabelText('Select record 7')
    fireEvent.click(screen.getByLabelText('Select record 7'))
    mock.onPost('/tourists/1/location-history/delete').reply(200, { deleted: 0, retained: 1, retained_reason: 'it is part of an emergency that is still open.' })
    fireEvent.click(screen.getByText(/Delete selected \(1\)/))
    fireEvent.click(screen.getByText('Yes, delete'))
    await screen.findByText(/1 kept: it is part of an emergency/)
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ ids: [7] })
  })

  it('export: needs the password, then offers an authenticated download', async () => {
    await openDashboard()
    mock.onGet('/tourists/1/data-export').reply(200, [])
    tab('My data')
    const btn = await screen.findByText('Download My Data', { selector: 'button' })
    expect(btn).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Password to confirm export'), { target: { value: 'pw' } })
    mock.onPost('/tourists/1/data-export').reply(201, { export_id: 'EXP-1', status: 'ready', format: 'json', expires_at: '2026-11-16T10:00:00' })
    mock.onGet('/tourists/1/data-export').reply(200, [{ export_id: 'EXP-1', status: 'ready', format: 'json', expires_at: '2026-11-16T10:00:00' }])
    fireEvent.click(btn)
    await screen.findByText(/Export EXP-1 is ready/)
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ format: 'json', password: 'pw' })
    mock.onGet('/tourists/1/data-export/EXP-1/download').reply(200, new Blob(['{}']), { 'content-disposition': 'attachment; filename="musafir-data-EXP-1.json"' })
    fireEvent.click(await screen.findByText('Download'))
    await waitFor(() => expect(window.URL.createObjectURL).toHaveBeenCalled())
  })

  it('export: shows a wrong-password error from the server', async () => {
    await openDashboard()
    mock.onGet('/tourists/1/data-export').reply(200, [])
    tab('My data')
    fireEvent.change(await screen.findByLabelText('Password to confirm export'), { target: { value: 'bad' } })
    mock.onPost('/tourists/1/data-export').reply(403, { detail: 'Password is incorrect.' })
    fireEvent.click(screen.getByText('Download My Data', { selector: 'button' }))
    await screen.findByText('Password is incorrect.')
  })

  it('account deletion: preview, password, typed DELETE, then honest status', async () => {
    await openDashboard()
    mock.onGet('/tourists/1/data-export').reply(200, [])
    tab('My data')
    mock.onGet('/tourists/1/account-deletion/preview').reply(200, {
      will_be_erased: [{ item: 'Location history', count: 3, result: 'deleted' }],
      will_be_retained: [{ item: 'SOS & incident records', count: 1, reason: 'An unresolved emergency or complaint still needs it' }],
      also: ['Your login and all sessions'], backups: 'Backups are overwritten on rotation.', external_processors: [], confirm_phrase: 'DELETE' })
    fireEvent.click(await screen.findByText('Delete My Account', { selector: 'button' }))
    await screen.findByText(/Must be kept for now/)
    expect(screen.getByText(/Backups are overwritten on rotation/)).toBeInTheDocument()

    mock.onPost('/tourists/1/account-deletion').reply(201, { request_id: 'DEL-ABC', status: 'awaiting_confirmation', steps: [], retained: [], external_processors: [], backups: 'b' })
    fireEvent.change(screen.getByLabelText('Password to start deletion'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByText('Continue'))
    const final = await screen.findByText('Permanently delete my account')
    expect(final).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Password to confirm deletion'), { target: { value: 'pw' } })
    fireEvent.change(screen.getByLabelText('Type DELETE to confirm'), { target: { value: 'delete' } })
    expect(final).toBeDisabled()                                            // exact phrase only
    fireEvent.change(screen.getByLabelText('Type DELETE to confirm'), { target: { value: 'DELETE' } })
    expect(final).not.toBeDisabled()

    mock.onPost('/tourists/1/account-deletion/DEL-ABC/confirm').reply(200, { request_id: 'DEL-ABC', status: 'in_progress', steps: [], retained: [], external_processors: [], backups: 'b' })
    mock.onGet('/privacy/deletion-status/DEL-ABC').reply(200, { request_id: 'DEL-ABC', status: 'partially_retained', steps: [], backups: 'b', external_processors: [],
      retained: [{ category: 'sos_incident_records', item: 'SOS & incident records', count: 1, reason: 'open emergency' }] })
    fireEvent.click(final)
    await screen.findByText('Deletion in progress')
    expect(JSON.parse(mock.history.post.at(-1).data)).toEqual({ password: 'pw', confirmation: 'DELETE' })
    await screen.findByText('Partially retained under a documented exception', {}, { timeout: 5000 })
    expect(screen.getByText(/open emergency/)).toBeInTheDocument()
    expect(screen.getByText('Sign out')).toBeInTheDocument()
  }, 15000)

  it('retention tab shows periods, and "No period established" instead of inventing one', async () => {
    await openDashboard()
    tab('Retention')
    expect(screen.getByText(/90 day\(s\) after trip end/)).toBeInTheDocument()
    expect(screen.getByText('No period established')).toBeInTheDocument()
  })

  it('support tab submits a grievance and lists it', async () => {
    await openDashboard()
    mock.onGet('/tourists/1/grievances').reply(200, [])
    tab('Support')
    await screen.findByText(/You have not raised any concerns/)
    expect(screen.getByText('privacy@example.org')).toBeInTheDocument()
    mock.onPost('/tourists/1/grievances').reply(201, { grievance_id: 'GRV-1', status: 'open' })
    fireEvent.change(screen.getByLabelText('Describe your concern'), { target: { value: 'Please explain what you hold' } })
    fireEvent.click(screen.getByText('Submit concern'))
    await screen.findByText(/Your reference is GRV-1/)
  })

  it('shows an error if the overview cannot load', async () => {
    mock.onGet('/tourists/1/privacy/overview').reply(500)
    render(<PrivacyDashboard touristId={1} />)
    fireEvent.click(screen.getByText('Privacy Dashboard'))
    await screen.findByRole('alert')
  })
})

describe('Registration consent', () => {
  it('starts with every box unticked and the required ones gate registration', () => {
    mock.onGet('/privacy/notice').reply(404)
    const onChange = vi.fn()
    render(<RegistrationConsent value={EMPTY_CONSENT} onChange={onChange} />)
    screen.getAllByRole('checkbox').forEach((c) => expect(c).not.toBeChecked())
    expect(screen.getAllByRole('checkbox').length).toBeGreaterThanOrEqual(5)
    expect(acksComplete(EMPTY_CONSENT)).toBe(false)
    expect(acksComplete({ ...EMPTY_CONSENT, privacy_notice_ack: true })).toBe(false)
    expect(acksComplete({ ...EMPTY_CONSENT, privacy_notice_ack: true, adult_declaration: true })).toBe(true)
  })

  it('ticking one optional box does not tick anything else (no bundling)', () => {
    mock.onGet('/privacy/notice').reply(404)
    const onChange = vi.fn()
    render(<RegistrationConsent value={EMPTY_CONSENT} onChange={onChange} />)
    fireEvent.click(screen.getByLabelText(/Location tracking/))
    expect(onChange).toHaveBeenCalledWith({ ...EMPTY_CONSENT, location_tracking: true })
  })

  it('sends an explicit true/false for every purpose', () => {
    const p = consentPayload({ ...EMPTY_CONSENT, location_tracking: true })
    expect(p).toContainEqual({ purpose: 'location_tracking', granted: true })
    expect(p).toContainEqual({ purpose: 'guardian_sharing', granted: false })
  })
})
