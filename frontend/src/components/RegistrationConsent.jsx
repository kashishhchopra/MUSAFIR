import { useEffect, useState } from 'react'
import { getNotice } from '../lib/privacyService'

// Registration privacy step. Every box starts UNTICKED, the two required
// acknowledgements are separate from the optional purposes, and nothing optional
// is bundled into them. What is shown comes from the server's configured
// notice (the fallback below is only used if it cannot be fetched).
const FALLBACK = [
  { key: 'location_tracking', title: 'Location tracking', kind: 'consent',
    description: 'Your phone location is sent while the app is open to compute your safety score and warn you about risky zones.',
    withdraw_effect: 'You can switch this off any time; safety scoring then stops but SOS still works.' },
  { key: 'guardian_sharing', title: 'Share live status with a guardian', kind: 'consent',
    description: 'A family member you invite can see your live status.', withdraw_effect: 'Links stop working when you withdraw.' },
  { key: 'notifications', title: 'Push notifications', kind: 'consent',
    description: 'Alerts and SOS confirmations on this phone.', withdraw_effect: 'Stops push alerts only.' },
]

export const EMPTY_CONSENT = {
  privacy_notice_ack: false, adult_declaration: false,
  location_tracking: false, guardian_sharing: false, notifications: false,
}
export const acksComplete = (v) => !!(v.privacy_notice_ack && v.adult_declaration)
// Every purpose is sent with an explicit true/false -- never inferred.
export const consentPayload = (v) => Object.entries(v).map(([purpose, granted]) => ({ purpose, granted: !!granted }))

export default function RegistrationConsent({ value, onChange }) {
  const [notice, setNotice] = useState(null)
  useEffect(() => {
    let live = true
    try {
      Promise.resolve(getNotice()).then((n) => live && setNotice(n)).catch(() => {})
    } catch { /* offline / not available: the fallback copy is shown */ }
    return () => { live = false }
  }, [])

  const optional = (notice?.purposes || FALLBACK).filter((p) => p.kind === 'consent')
  const basics = (notice?.purposes || []).filter((p) => p.kind === 'notice')
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.checked })
  const box = 'mt-0.5 h-4 w-4 shrink-0 accent-cyan-400'

  return (
    <fieldset className="space-y-3 border border-white/10 rounded-xl p-3" data-testid="registration-consent">
      <legend className="px-1 text-sm font-semibold text-slate-100">Your privacy</legend>
      <p className="text-xs text-slate-300">
        We collect your name, ID document, phone, photo, trip dates and (if you allow it) your location, to
        issue your Digital ID and keep you safe. {basics.length > 0 && 'During an SOS your identity, last location and emergency contacts go to the responders handling it. '}
        You can see, download or delete your data and withdraw any optional permission later from
        Me → Privacy Dashboard.{notice?.version && <> Notice version {notice.version}.</>}
      </p>

      <label className="flex gap-2 text-sm text-slate-100">
        <input type="checkbox" className={box} checked={value.privacy_notice_ack} onChange={set('privacy_notice_ack')} />
        <span>I have read how MUSAFIR uses my data <span className="text-red-300">(required)</span></span>
      </label>
      <label className="flex gap-2 text-sm text-slate-100">
        <input type="checkbox" className={box} checked={value.adult_declaration} onChange={set('adult_declaration')} />
        <span>I am 18 years of age or older <span className="text-red-300">(required)</span></span>
      </label>

      <div className="pt-1 border-t border-white/10">
        <p className="text-xs font-semibold text-slate-200 mb-1">Optional - you choose each one separately</p>
        {optional.map((p) => (
          <label key={p.key} className="flex gap-2 text-sm text-slate-100 py-1">
            <input type="checkbox" className={box} checked={!!value[p.key]} onChange={set(p.key)} />
            <span>
              {p.title}
              <span className="block text-xs text-slate-400">{p.description} {p.withdraw_effect}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
