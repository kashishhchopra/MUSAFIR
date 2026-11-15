import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import MedicalQrScanner from './MedicalQrScanner'
import MedicalProfileView from './MedicalProfileView'
import MedicalProfileCard from './MedicalProfileCard'

vi.mock('../lib/qrImage', () => ({ decodeQrFromFile: vi.fn() }))
import { decodeQrFromFile } from '../lib/qrImage'

const mock = new MockAdapter(api)

const OPTIONS = {
  blood_groups: ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-', 'Unknown'],
  allergy_categories: ['food', 'medicine', 'other'],
  suggested_conditions: ['Asthma', 'Diabetes'],
  access_purposes: ['medical_emergency', 'accident'],
  access_purpose_required: false,
  limits: { allergies: 30, medical_conditions: 30, medications: 30, emergency_contacts: 2, notes_chars: 1000 },
}

const granted = (over = {}) => ({
  status: 'granted',
  accessed_at: '2026-11-15T10:00:00+00:00',
  request_id: 'abc123',
  purpose: 'medical_emergency',
  tourist: { full_name: 'Kashish Chopra', digital_id: 'STS-ABC123', photo: null },
  medical: {
    blood_group: 'O+',
    allergies_status: 'listed',
    allergies: [{ name: 'Penicillin', category: 'medicine', reaction: 'Rash' }],
    conditions_status: 'listed',
    medical_conditions: [{ name: 'Asthma', description: 'Mild' }],
    medications_status: 'listed',
    medications: [{ name: 'Salbutamol', dosage: '100mcg', frequency: 'as needed', notes: null }],
    emergency_contacts: [{ name: 'Asha Rao', relationship: 'Spouse', phone: '+91-9876543210' }],
    additional_notes: 'Inhaler in left pocket',
    profile_updated_at: '2026-11-14T09:00:00',
    ...over,
  },
  provenance: { source: 'tourist_reported', verified: false, note: 'Reported by the tourist. Not verified by any medical authority.' },
})

beforeEach(() => {
  mock.reset()
  mock.onGet('/medical-profile/options').reply(200, OPTIONS)
})
afterEach(() => { delete navigator.mediaDevices })

const paste = (utils, text) => {
  fireEvent.change(utils.getByLabelText('QR code text'), { target: { value: text } })
  fireEvent.click(utils.getByText('Open'))
}

describe('MedicalQrScanner (responder)', () => {
  it('shows the medical profile after a granted scan and sends the pasted token + purpose', async () => {
    mock.onPost('/tourist-id/medical').reply(200, granted())
    const u = render(<MedicalQrScanner />)
    await u.findByLabelText('Reason for access')
    paste(u, 'TSID:abcdefghijklmnopqrstuv')
    await u.findByTestId('medical-profile-view')
    await u.findByText('Kashish Chopra')
    await u.findByText('Penicillin')
    expect(u.getByLabelText('Blood group O+')).toBeInTheDocument()
    expect(JSON.parse(mock.history.post[0].data)).toEqual({ token: 'TSID:abcdefghijklmnopqrstuv', purpose: 'medical_emergency' })
  })

  it('can return to the scanner', async () => {
    mock.onPost('/tourist-id/medical').reply(200, granted())
    const u = render(<MedicalQrScanner />)
    paste(u, 'TSID:abcdefghijklmnopqrstuv')
    await u.findByTestId('medical-profile-view')
    fireEvent.click(u.getAllByText(/Back to scanner/)[0])
    await u.findByTestId('medical-scanner')
  })

  it.each([
    ['invalid', /Invalid QR code/],
    ['revoked', /QR code revoked/],
    ['expired', /QR code expired/],
  ])('shows a clear message for a %s token and no medical data', async (status, title) => {
    mock.onPost('/tourist-id/medical').reply(200, { status, reason: 'x', request_id: 'r' })
    const u = render(<MedicalQrScanner />)
    paste(u, 'TSID:abcdefghijklmnopqrstuv')
    await u.findByText(title)
    expect(u.queryByTestId('medical-profile-view')).not.toBeInTheDocument()
    expect(u.queryByText('Penicillin')).not.toBeInTheDocument()
  })

  it('reports a tourist without a profile, showing identity only', async () => {
    mock.onPost('/tourist-id/medical').reply(200, {
      status: 'no_profile', reason: 'This tourist has not provided a medical profile.',
      tourist: { full_name: 'Kashish Chopra', digital_id: 'STS-ABC123', photo: null },
    })
    const u = render(<MedicalQrScanner />)
    paste(u, 'TSID:abcdefghijklmnopqrstuv')
    await u.findByText('No medical profile')
    await u.findByText(/STS-ABC123/)
  })

  it('tells an unauthorized account it may not view medical profiles', async () => {
    mock.onPost('/tourist-id/medical').reply(403, { detail: 'Not authorized to view medical profiles' })
    const u = render(<MedicalQrScanner />)
    paste(u, 'TSID:abcdefghijklmnopqrstuv')
    const alert = await u.findByTestId('scan-problem')
    expect(alert).toHaveAttribute('data-kind', 'unauthorized')
    expect(u.queryByTestId('medical-profile-view')).not.toBeInTheDocument()
  })

  it('handles being rate limited', async () => {
    mock.onPost('/tourist-id/medical').reply(429, { detail: 'Too many failed lookups - try again later.' })
    const u = render(<MedicalQrScanner />)
    paste(u, 'TSID:abcdefghijklmnopqrstuv')
    expect((await u.findByTestId('scan-problem'))).toHaveAttribute('data-kind', 'throttled')
  })

  it('handles the backend being unavailable', async () => {
    mock.onPost('/tourist-id/medical').networkError()
    const u = render(<MedicalQrScanner />)
    paste(u, 'TSID:abcdefghijklmnopqrstuv')
    expect((await u.findByTestId('scan-problem'))).toHaveAttribute('data-kind', 'network')
    await u.findByText(/Could not reach the server/)
  })

  it('reads a QR from an uploaded image and opens the profile', async () => {
    decodeQrFromFile.mockResolvedValue('TSID:abcdefghijklmnopqrstuv')
    mock.onPost('/tourist-id/medical').reply(200, granted())
    const u = render(<MedicalQrScanner />)
    fireEvent.change(u.getByLabelText('Upload QR image'), { target: { files: [new File(['x'], 'qr.png', { type: 'image/png' })] } })
    await u.findByTestId('medical-profile-view')
    expect(JSON.parse(mock.history.post[0].data).token).toBe('TSID:abcdefghijklmnopqrstuv')
  })

  it('says so when an uploaded image has no readable QR', async () => {
    decodeQrFromFile.mockResolvedValue(null)
    const u = render(<MedicalQrScanner />)
    fireEvent.change(u.getByLabelText('Upload QR image'), { target: { files: [new File(['x'], 'p.png', { type: 'image/png' })] } })
    await u.findByText(/No QR code could be read/)
    expect(mock.history.post.length).toBe(0)
  })

  it('shows a camera-denied message and keeps manual entry available', async () => {
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: () => Promise.reject(new DOMException('no', 'NotAllowedError')) },
    })
    const u = render(<MedicalQrScanner />)
    fireEvent.click(u.getByText(/Start camera/))
    await u.findByText(/Camera permission was denied/)
    expect(u.getByLabelText('QR code text')).toBeInTheDocument()
  })

  it('shows a fallback when the camera API is unavailable', async () => {
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: undefined })
    const u = render(<MedicalQrScanner />)
    fireEvent.click(u.getByText(/Start camera/))
    await u.findByText(/Camera is not available/)
  })
})

describe('MedicalProfileView', () => {
  it('highlights allergies and labels the data as tourist-reported, not verified', () => {
    const u = render(<MedicalProfileView result={granted()} onBack={() => {}} />)
    expect(u.getByTestId('mp-allergies').className).toMatch(/red/)
    expect(u.getByRole('note')).toHaveTextContent(/Not verified/)
    expect(u.queryByText(/verified by/i, { selector: ':not([role=note])' })).toBeNull()
    expect(u.getByText(/This access has been recorded/)).toBeInTheDocument()
    expect(u.getByText(/Profile last updated/)).toBeInTheDocument()
  })

  it('distinguishes "not provided" from "no known allergies"', () => {
    const none = render(<MedicalProfileView onBack={() => {}}
      result={granted({ allergies_status: 'none_known', allergies: [] })} />)
    expect(none.getByTestId('mp-allergies')).toHaveTextContent(/NO known allergies/)
    none.unmount()
    const missing = render(<MedicalProfileView onBack={() => {}}
      result={granted({ allergies_status: 'not_provided', allergies: [], blood_group: null,
        conditions_status: 'not_provided', medical_conditions: [], medications_status: 'not_provided',
        medications: [], emergency_contacts: [], additional_notes: null })} />)
    expect(missing.getByTestId('mp-allergies')).toHaveTextContent('Not provided by tourist')
    expect(missing.getByTestId('mp-allergies')).not.toHaveTextContent(/no known/i)
    expect(missing.getByTestId('mp-blood')).toHaveTextContent('Not provided by tourist')
    expect(missing.getByTestId('mp-contacts')).toHaveTextContent('Not provided by tourist')
  })

  it('offers a tap-to-call link for the emergency contact', () => {
    const u = render(<MedicalProfileView result={granted()} onBack={() => {}} />)
    expect(u.getByText(/9876543210/).closest('a')).toHaveAttribute('href', 'tel:+919876543210')
  })
})

// ------------------------------------------------------------------ tourist
const emptyProfile = {
  exists: false, blood_group: null,
  allergies_status: 'not_provided', allergies: [], conditions_status: 'not_provided', medical_conditions: [],
  medications_status: 'not_provided', medications: [], emergency_contacts: [], additional_notes: null,
  profile_updated_at: null,
  completion: { sections: { blood_group: false, allergies: false, medical_conditions: false, medications: false, emergency_contacts: false }, completed: 0, total: 5, percent: 0 },
}
const qrActive = {
  state: 'active', qr_png_base64: 'data:image/png;base64,qr', issued_at: '2026-11-15T09:00:00',
  expires_at: null, digital_id: 'STS-ABC123', trip_end: '2026-12-01T00:00:00', medical_profile_present: false,
}

function mountTourist({ profile = emptyProfile, qr = qrActive, history = [] } = {}) {
  mock.onGet('/tourists/1/medical-profile').reply(200, profile)
  mock.onGet('/tourists/1/medical-qr').reply(200, qr)
  mock.onGet('/tourists/1/medical-access').reply(200, history)
  const u = render(<MedicalProfileCard touristId={1} />)
  fireEvent.click(u.getByText(/Medical Profile & Emergency QR/))
  return u
}

describe('MedicalProfileCard (tourist)', () => {
  it('is collapsed until opened, then shows completion, privacy text and the QR', async () => {
    const u = mountTourist()
    await u.findByTestId('mp-completion')
    expect(u.getByText('0 of 5 sections filled in')).toBeInTheDocument()
    expect(u.getByText(/Who can see this\?/)).toBeInTheDocument()
    expect(u.getByAltText('Emergency QR code')).toHaveAttribute('src', 'data:image/png;base64,qr')
    expect(u.getByText('Download QR').closest('a')).toHaveAttribute('download', expect.stringContaining('STS-ABC123'))
    expect(u.getByText(/Nobody has opened your medical profile/)).toBeInTheDocument()
  })

  it('saves a partial profile without requiring every field', async () => {
    const saved = { ...emptyProfile, exists: true, blood_group: 'B+', profile_updated_at: '2026-11-15T10:00:00',
      completion: { ...emptyProfile.completion, completed: 1, percent: 20 } }
    mock.onPut('/tourists/1/medical-profile').reply(200, saved)
    const u = mountTourist()
    await u.findByTestId('mp-summary')
    fireEvent.click(u.getByText('Add information'))
    await u.findByTestId('mp-form')
    fireEvent.change(u.getByLabelText('Blood group'), { target: { value: 'B+' } })
    fireEvent.click(u.getByText('Save'))
    await waitFor(() => expect(u.getByTestId('mp-summary')).toBeInTheDocument())
    const body = JSON.parse(mock.history.put[0].data)
    expect(body.blood_group).toBe('B+')
    expect(body.allergies_status).toBe('not_provided')
    expect(body.emergency_contacts).toEqual([])
    await u.findByText('B+')
    expect(u.getByText(/Last updated/)).toBeInTheDocument()
  })

  it('submits allergies, conditions, medications and contacts', async () => {
    mock.onPut('/tourists/1/medical-profile').reply(200, emptyProfile)
    const u = mountTourist()
    await u.findByTestId('mp-summary')
    fireEvent.click(u.getByText('Add information'))
    await u.findByTestId('mp-form')
    fireEvent.change(u.getByLabelText('Allergies status'), { target: { value: 'listed' } })
    fireEvent.change(u.getByLabelText('Allergy 1 name'), { target: { value: 'Penicillin' } })
    fireEvent.change(u.getByLabelText('Allergy 1 type'), { target: { value: 'medicine' } })
    fireEvent.click(u.getByText('+ Asthma'))
    fireEvent.change(u.getByLabelText('Current medications status'), { target: { value: 'listed' } })
    fireEvent.change(u.getByLabelText('Medication 1 name'), { target: { value: 'Salbutamol' } })
    fireEvent.change(u.getByLabelText('Medication 1 dose'), { target: { value: '100mcg' } })
    fireEvent.change(u.getByLabelText('Contact 1 name'), { target: { value: 'Asha' } })
    fireEvent.change(u.getByLabelText('Contact 1 relationship'), { target: { value: 'Spouse' } })
    fireEvent.change(u.getByLabelText('Contact 1 phone'), { target: { value: '9876543210' } })
    fireEvent.click(u.getByText('Save'))
    await waitFor(() => expect(mock.history.put.length).toBe(1))
    const b = JSON.parse(mock.history.put[0].data)
    expect(b.allergies).toEqual([{ name: 'Penicillin', category: 'medicine', reaction: null }])
    expect(b.allergies_status).toBe('listed')
    expect(b.medical_conditions.map((c) => c.name)).toEqual(['Asthma'])
    expect(b.medications[0]).toMatchObject({ name: 'Salbutamol', dosage: '100mcg' })
    expect(b.emergency_contacts).toEqual([{ name: 'Asha', relationship: 'Spouse', phone: '9876543210' }])
  })

  it('"No known allergies" is sent as a distinct status with no entries', async () => {
    mock.onPut('/tourists/1/medical-profile').reply(200, emptyProfile)
    const u = mountTourist()
    await u.findByTestId('mp-summary')
    fireEvent.click(u.getByText('Add information'))
    fireEvent.change(await u.findByLabelText('Allergies status'), { target: { value: 'none_known' } })
    fireEvent.click(u.getByText('Save'))
    await waitFor(() => expect(mock.history.put.length).toBe(1))
    const b = JSON.parse(mock.history.put[0].data)
    expect(b.allergies_status).toBe('none_known')
    expect(b.allergies).toEqual([])
  })

  it('shows the server validation message when saving fails', async () => {
    mock.onPut('/tourists/1/medical-profile').reply(422, { detail: [{ msg: 'Value error, Enter a valid phone number' }] })
    const u = mountTourist()
    await u.findByTestId('mp-summary')
    fireEvent.click(u.getByText('Add information'))
    await u.findByTestId('mp-form')
    fireEvent.change(u.getByLabelText('Contact 1 name'), { target: { value: 'Asha' } })
    fireEvent.change(u.getByLabelText('Contact 1 relationship'), { target: { value: 'Spouse' } })
    fireEvent.change(u.getByLabelText('Contact 1 phone'), { target: { value: 'abc' } })
    fireEvent.click(u.getByText('Save'))
    await u.findByText('Enter a valid phone number')
  })

  it('removes a single section', async () => {
    const filled = { ...emptyProfile, exists: true, blood_group: 'A+',
      completion: { ...emptyProfile.completion, completed: 1, percent: 20 } }
    mock.onDelete('/tourists/1/medical-profile/blood_group').reply(200, { ...emptyProfile, exists: true })
    const u = mountTourist({ profile: filled })
    await u.findByText('A+')
    fireEvent.click(u.getByLabelText('Remove Blood group'))
    await waitFor(() => expect(u.queryByText('A+')).not.toBeInTheDocument())
  })

  it('regenerating asks for confirmation and replaces the QR', async () => {
    mock.onPost('/tourists/1/medical-qr/regenerate').reply(200, { ...qrActive, qr_png_base64: 'data:image/png;base64,NEW' })
    const u = mountTourist()
    await u.findByTestId('mp-qr')
    fireEvent.click(u.getByText('Regenerate QR'))
    expect(mock.history.post.length).toBe(0)
    fireEvent.click(u.getByText('Confirm'))
    await waitFor(() => expect(u.getByAltText('Emergency QR code')).toHaveAttribute('src', 'data:image/png;base64,NEW'))
  })

  it('revoking removes the QR image and offers to create a new one', async () => {
    mock.onPost('/tourists/1/medical-qr/revoke').reply(200, { ...qrActive, state: 'revoked', qr_png_base64: null })
    const u = mountTourist()
    await u.findByTestId('mp-qr')
    fireEvent.click(u.getByText('Revoke access'))
    fireEvent.click(u.getByText('Confirm'))
    await u.findByText(/No active QR code/)
    expect(u.queryByAltText('Emergency QR code')).not.toBeInTheDocument()
    expect(u.getByText('Create new QR')).toBeInTheDocument()
    expect(u.queryByText('Download QR')).not.toBeInTheDocument()
  })

  it('lists who opened the profile', async () => {
    const u = mountTourist({ history: [{ id: 1, scanned_at: '2026-11-15T10:00:00', responder_role: 'responder', purpose: 'accident' }] })
    await u.findByText(/responder \(Accident\)/)
  })

  it('shows an error with retry when loading fails', async () => {
    mock.onGet('/tourists/1/medical-profile').reply(500)
    mock.onGet('/tourists/1/medical-qr').reply(200, qrActive)
    const u = render(<MedicalProfileCard touristId={1} />)
    fireEvent.click(u.getByText(/Medical Profile & Emergency QR/))
    await u.findByText(/Could not load your medical profile/)
    expect(u.getByText('Retry')).toBeInTheDocument()
  })
})
