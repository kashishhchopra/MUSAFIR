import { useEffect, useState } from 'react'
import {
  clearMedicalField, errorText, getMedicalAccessHistory, getMedicalOptions, getMedicalProfile,
  getMedicalQr, purposeLabel, regenerateMedicalQr, revokeMedicalQr, saveMedicalProfile,
} from '../lib/medicalProfileService'

const INPUT = 'w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-3 py-2 text-sm'
const BTN = 'text-xs font-semibold px-3 py-1.5 rounded-lg'
const GHOST = `${BTN} bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600`

const QR_STATE = {
  active: ['🟢', 'Active', 'text-green-600 dark:text-green-400'],
  expiring_soon: ['🟡', 'Active - trip ends soon', 'text-yellow-600 dark:text-yellow-400'],
  expired: ['⚫', 'Expired', 'text-slate-500'],
  invalidated: ['🔴', 'Disabled', 'text-red-600 dark:text-red-400'],
  revoked: ['🔴', 'Revoked', 'text-red-600 dark:text-red-400'],
}

const EMPTY_ALLERGY = { name: '', category: 'other', reaction: '' }
const EMPTY_CONDITION = { name: '', description: '' }
const EMPTY_MED = { name: '', dosage: '', frequency: '', notes: '' }
const EMPTY_CONTACT = { name: '', relationship: '', phone: '' }

// Medical Profile & Emergency QR (tourist side). Everything optional; the QR
// is the same secure Digital ID token -- it carries no medical data, and a
// responder only sees the profile after the backend verifies their account.
export default function MedicalProfileCard({ touristId }) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [profile, setProfile] = useState(null)
  const [opts, setOpts] = useState(null)
  const [qr, setQr] = useState(null)
  const [history, setHistory] = useState([])
  const [editing, setEditing] = useState(false)

  const load = async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [p, o, q, h] = await Promise.all([
        getMedicalProfile(touristId), getMedicalOptions(), getMedicalQr(touristId),
        getMedicalAccessHistory(touristId).catch(() => []),
      ])
      setProfile(p); setOpts(o); setQr(q); setHistory(h)
    } catch (e) {
      setLoadError(errorText(e, 'Could not load your medical profile.'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { setProfile(null); setQr(null) }, [touristId])

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (next && !profile) load()
  }

  const pct = profile?.completion?.percent

  return (
    <div>
      <button onClick={toggle} aria-expanded={open}
        className="w-full text-sm font-semibold text-rose-700 dark:text-rose-300 bg-rose-50 dark:bg-rose-900/30 rounded-xl py-2">
        {open ? 'Hide Medical Profile & Emergency QR ▲' : '🩺 Medical Profile & Emergency QR'}
        {!open && pct != null && <span className="ml-2 text-xs font-normal">({pct}% filled)</span>}
      </button>

      {open && (
        <div className="mt-3 rounded-[var(--theme-radius)] shadow-[var(--theme-shadow)] bg-white dark:bg-slate-800 p-4 space-y-4"
          data-testid="medical-card">
          {loading && !profile && <div role="status" className="text-sm text-slate-400 text-center py-4">Loading…</div>}
          {loadError && (
            <div role="alert" className="text-sm text-red-600">
              {loadError} <button onClick={load} className="underline font-semibold">Retry</button>
            </div>
          )}
          {profile && opts && (
            <>
              <Completion profile={profile} />
              <Privacy />
              {editing ? (
                <MedicalForm touristId={touristId} profile={profile} opts={opts}
                  onSaved={(p) => { setProfile(p); setEditing(false); getMedicalQr(touristId).then(setQr).catch(() => {}) }}
                  onCancel={() => setEditing(false)} />
              ) : (
                <ProfileSummary touristId={touristId} profile={profile} onEdit={() => setEditing(true)}
                  onChanged={setProfile} />
              )}
              <QrPanel touristId={touristId} qr={qr} onChange={setQr} />
              <AccessHistory history={history} />
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- pieces
function Completion({ profile }) {
  const c = profile.completion
  const labels = { blood_group: 'Blood group', allergies: 'Allergies', medical_conditions: 'Conditions',
    medications: 'Medications', emergency_contacts: 'Emergency contact' }
  return (
    <div data-testid="mp-completion">
      <div className="flex items-center justify-between text-xs font-semibold text-slate-600 dark:text-slate-300">
        <span>{c.completed} of {c.total} sections filled in</span><span>{c.percent}%</span>
      </div>
      <div className="h-2 rounded-full bg-slate-200 dark:bg-slate-700 mt-1 overflow-hidden"
        role="progressbar" aria-valuenow={c.percent} aria-valuemin={0} aria-valuemax={100} aria-label="Medical profile completion">
        <div className="h-full bg-emerald-500" style={{ width: `${c.percent}%` }} />
      </div>
      <div className="flex flex-wrap gap-1.5 mt-2">
        {Object.entries(c.sections).map(([k, done]) => (
          <span key={k} className={`text-[11px] px-2 py-0.5 rounded-full ${done
            ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
            : 'bg-slate-100 text-slate-500 dark:bg-slate-700 dark:text-slate-400'}`}>
            {done ? '✓' : '○'} {labels[k]}
          </span>
        ))}
      </div>
      <p className="text-[11px] text-slate-500 mt-1">Everything here is optional. Fill in only what you are comfortable sharing in an emergency.</p>
    </div>
  )
}

function Privacy() {
  return (
    <div className="text-xs rounded-lg bg-sky-50 dark:bg-sky-900/20 text-sky-900 dark:text-sky-200 p-3 space-y-1">
      <div className="font-semibold">Who can see this?</div>
      <ul className="list-disc ml-4 space-y-0.5">
        <li>Only logged-in police officers and emergency responders, and only after they scan your QR code.</li>
        <li>Your QR code contains no medical information - just a secure random code. A photo of it does not open your profile.</li>
        <li>Every time your profile is opened it is recorded, and you can see it below.</li>
        <li>This is what you report; it is not checked or verified by anyone.</li>
        <li>You can edit it, regenerate or revoke your QR at any time.</li>
      </ul>
    </div>
  )
}

function ProfileSummary({ touristId, profile: p, onEdit, onChanged }) {
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const clear = async (field) => {
    setBusy(field); setErr('')
    try { onChanged(await clearMedicalField(touristId, field)) } catch (e) { setErr(errorText(e)) } finally { setBusy('') }
  }
  const Row = ({ label, field, empty, children, has }) => (
    <div className="py-2 border-b border-slate-100 dark:border-slate-700 last:border-0">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">{label}</div>
          <div className="text-sm text-slate-800 dark:text-slate-100 break-words">{has ? children : <span className="italic text-slate-400">{empty}</span>}</div>
        </div>
        {has && (
          <button onClick={() => clear(field)} disabled={busy === field} aria-label={`Remove ${label}`}
            className="text-[11px] text-red-600 hover:underline shrink-0 disabled:opacity-40">Remove</button>
        )}
      </div>
    </div>
  )
  const statusText = (s, none) => (s === 'none_known' ? none : null)
  return (
    <div data-testid="mp-summary">
      <div className="flex items-center justify-between mb-1">
        <h4 className="text-sm font-bold text-slate-800 dark:text-slate-100">Your medical profile</h4>
        <button onClick={onEdit} className={`${BTN} bg-sky-600 text-white`}>{p.exists ? 'Edit' : 'Add information'}</button>
      </div>
      {err && <div role="alert" className="text-xs text-red-600">{err}</div>}
      <Row label="Blood group" field="blood_group" has={!!p.blood_group} empty="Not provided">{p.blood_group}</Row>
      <Row label="Allergies" field="allergies" has={p.allergies_status !== 'not_provided'} empty="Not provided">
        {statusText(p.allergies_status, 'No known allergies') || p.allergies.map((a) => `${a.name} (${a.category})`).join(', ')}
      </Row>
      <Row label="Medical conditions" field="medical_conditions" has={p.conditions_status !== 'not_provided'} empty="Not provided">
        {statusText(p.conditions_status, 'No known conditions') || p.medical_conditions.map((c) => c.name).join(', ')}
      </Row>
      <Row label="Current medications" field="medications" has={p.medications_status !== 'not_provided'} empty="Not provided">
        {statusText(p.medications_status, 'No current medication') || p.medications.map((m) => [m.name, m.dosage].filter(Boolean).join(' ')).join(', ')}
      </Row>
      <Row label="Emergency contacts" field="emergency_contacts" has={p.emergency_contacts.length > 0} empty="Not provided">
        {p.emergency_contacts.map((c) => `${c.name} (${c.relationship}) ${c.phone}`).join(' · ')}
      </Row>
      <Row label="Additional notes" field="additional_notes" has={!!p.additional_notes} empty="Not provided">{p.additional_notes}</Row>
      <div className="text-[11px] text-slate-400 mt-2" data-testid="mp-updated">
        {p.profile_updated_at ? `Last updated ${new Date(p.profile_updated_at).toLocaleString()}` : 'Not saved yet'}
      </div>
    </div>
  )
}

// A list section with an explicit "not provided / none known / listed" choice,
// so a blank is never mistaken for "no allergies".
function ListSection({ label, status, setStatus, items, setItems, empty, render, addLabel, noneLabel, max }) {
  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-semibold text-slate-600 dark:text-slate-300">{label}</legend>
      <select aria-label={`${label} status`} value={status} className={INPUT}
        onChange={(e) => {
          const s = e.target.value
          setStatus(s)
          if (s === 'listed' && items.length === 0) setItems([{ ...empty }])
          if (s !== 'listed') setItems([])
        }}>
        <option value="not_provided">Not provided</option>
        <option value="none_known">{noneLabel}</option>
        <option value="listed">I will list them</option>
      </select>
      {status === 'listed' && (
        <div className="space-y-2">
          {items.map((it, i) => (
            <div key={i} className="rounded-lg border border-slate-200 dark:border-slate-600 p-2 space-y-1.5">
              {render(it, (patch) => setItems(items.map((x, j) => (j === i ? { ...x, ...patch } : x))), i)}
              <button type="button" onClick={() => setItems(items.filter((_, j) => j !== i))}
                aria-label={`Remove ${label} entry ${i + 1}`} className="text-[11px] text-red-600 hover:underline">Remove</button>
            </div>
          ))}
          {items.length < max && (
            <button type="button" onClick={() => setItems([...items, { ...empty }])} className={GHOST}>+ {addLabel}</button>
          )}
        </div>
      )}
    </fieldset>
  )
}

const clean = (obj) => Object.fromEntries(Object.entries(obj).map(([k, v]) => [k, typeof v === 'string' ? v.trim() : v]))
const nonEmpty = (it) => (it.name || '').trim() !== ''

function MedicalForm({ touristId, profile, opts, onSaved, onCancel }) {
  const [bg, setBg] = useState(profile.blood_group || '')
  const [aStatus, setAStatus] = useState(profile.allergies_status)
  const [allergies, setAllergies] = useState(profile.allergies.map((a) => ({ reaction: '', ...a })))
  const [cStatus, setCStatus] = useState(profile.conditions_status)
  const [conditions, setConditions] = useState(profile.medical_conditions.map((c) => ({ description: '', ...c })))
  const [mStatus, setMStatus] = useState(profile.medications_status)
  const [meds, setMeds] = useState(profile.medications.map((m) => ({ dosage: '', frequency: '', notes: '', ...m })))
  const [contacts, setContacts] = useState(
    profile.emergency_contacts.length ? profile.emergency_contacts : [{ ...EMPTY_CONTACT }])
  const [notes, setNotes] = useState(profile.additional_notes || '')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setSaving(true); setErr('')
    const listed = (status, arr) => (status === 'listed' ? arr.filter(nonEmpty).map(clean) : [])
    const a = listed(aStatus, allergies), c = listed(cStatus, conditions), m = listed(mStatus, meds)
    // an emptied "listed" section means the tourist has nothing to report there
    const st = (status, arr) => (status === 'listed' && arr.length === 0 ? 'not_provided' : status)
    const body = {
      blood_group: bg || null,
      allergies_status: st(aStatus, a), allergies: a.map((x) => ({ ...x, reaction: x.reaction || null })),
      conditions_status: st(cStatus, c), medical_conditions: c.map((x) => ({ ...x, description: x.description || null })),
      medications_status: st(mStatus, m),
      medications: m.map((x) => ({ ...x, dosage: x.dosage || null, frequency: x.frequency || null, notes: x.notes || null })),
      emergency_contacts: contacts.filter((x) => x.name.trim() || x.phone.trim() || x.relationship.trim()).map(clean),
      additional_notes: notes.trim() || null,
    }
    try {
      onSaved(await saveMedicalProfile(touristId, body))
    } catch (e2) {
      setErr(errorText(e2))
    } finally {
      setSaving(false)
    }
  }

  const lim = opts.limits
  const addSuggested = (name) => {
    if (conditions.some((c) => c.name.toLowerCase() === name.toLowerCase())) return
    setCStatus('listed')
    setConditions([...conditions.filter(nonEmpty), { name, description: '' }])
  }

  return (
    <form onSubmit={submit} className="space-y-4" data-testid="mp-form" aria-label="Edit medical profile">
      <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
        Blood group
        <select value={bg} onChange={(e) => setBg(e.target.value)} aria-label="Blood group" className={`${INPUT} mt-1`}>
          <option value="">Not provided</option>
          {opts.blood_groups.map((g) => <option key={g} value={g}>{g === 'Unknown' ? 'Unknown / not sure' : g}</option>)}
        </select>
      </label>

      <ListSection label="Allergies" status={aStatus} setStatus={setAStatus} items={allergies} setItems={setAllergies}
        empty={EMPTY_ALLERGY} addLabel="Add allergy" noneLabel="No known allergies" max={lim.allergies}
        render={(it, set, i) => (
          <>
            <input value={it.name} onChange={(e) => set({ name: e.target.value })} placeholder="Allergy (e.g. Penicillin, Peanuts)"
              aria-label={`Allergy ${i + 1} name`} maxLength={80} className={INPUT} />
            <div className="flex gap-2">
              <select value={it.category} onChange={(e) => set({ category: e.target.value })} aria-label={`Allergy ${i + 1} type`} className={INPUT}>
                {opts.allergy_categories.map((c) => <option key={c} value={c}>{purposeLabel(c)}</option>)}
              </select>
              <input value={it.reaction || ''} onChange={(e) => set({ reaction: e.target.value })} placeholder="Reaction (optional)"
                aria-label={`Allergy ${i + 1} reaction`} maxLength={120} className={INPUT} />
            </div>
          </>
        )} />

      <div>
        <ListSection label="Medical conditions" status={cStatus} setStatus={setCStatus} items={conditions} setItems={setConditions}
          empty={EMPTY_CONDITION} addLabel="Add condition" noneLabel="No known conditions" max={lim.medical_conditions}
          render={(it, set, i) => (
            <>
              <input value={it.name} onChange={(e) => set({ name: e.target.value })} placeholder="Condition"
                aria-label={`Condition ${i + 1} name`} maxLength={80} className={INPUT} />
              <input value={it.description || ''} onChange={(e) => set({ description: e.target.value })} placeholder="Details (optional)"
                aria-label={`Condition ${i + 1} details`} maxLength={200} className={INPUT} />
            </>
          )} />
        {opts.suggested_conditions.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2" aria-label="Common conditions">
            {opts.suggested_conditions.map((s) => (
              <button key={s} type="button" onClick={() => addSuggested(s)} className={GHOST}>+ {s}</button>
            ))}
          </div>
        )}
      </div>

      <ListSection label="Current medications" status={mStatus} setStatus={setMStatus} items={meds} setItems={setMeds}
        empty={EMPTY_MED} addLabel="Add medication" noneLabel="No current medication" max={lim.medications}
        render={(it, set, i) => (
          <>
            <input value={it.name} onChange={(e) => set({ name: e.target.value })} placeholder="Medication"
              aria-label={`Medication ${i + 1} name`} maxLength={80} className={INPUT} />
            <div className="flex gap-2">
              <input value={it.dosage || ''} onChange={(e) => set({ dosage: e.target.value })} placeholder="Dose (as you take it)"
                aria-label={`Medication ${i + 1} dose`} maxLength={60} className={INPUT} />
              <input value={it.frequency || ''} onChange={(e) => set({ frequency: e.target.value })} placeholder="How often"
                aria-label={`Medication ${i + 1} frequency`} maxLength={60} className={INPUT} />
            </div>
            <input value={it.notes || ''} onChange={(e) => set({ notes: e.target.value })} placeholder="Notes (optional)"
              aria-label={`Medication ${i + 1} notes`} maxLength={200} className={INPUT} />
          </>
        )} />

      <fieldset className="space-y-2">
        <legend className="text-xs font-semibold text-slate-600 dark:text-slate-300">Emergency contacts (up to {lim.emergency_contacts})</legend>
        {contacts.map((c, i) => {
          const set = (patch) => setContacts(contacts.map((x, j) => (j === i ? { ...x, ...patch } : x)))
          return (
            <div key={i} className="rounded-lg border border-slate-200 dark:border-slate-600 p-2 space-y-1.5">
              <div className="text-[11px] font-semibold text-slate-500">{i === 0 ? 'Primary' : 'Secondary (optional)'}</div>
              <input value={c.name} onChange={(e) => set({ name: e.target.value })} placeholder="Name"
                aria-label={`Contact ${i + 1} name`} maxLength={80} className={INPUT} />
              <div className="flex gap-2">
                <input value={c.relationship} onChange={(e) => set({ relationship: e.target.value })} placeholder="Relationship"
                  aria-label={`Contact ${i + 1} relationship`} maxLength={40} className={INPUT} />
                <input value={c.phone} onChange={(e) => set({ phone: e.target.value })} placeholder="Phone"
                  aria-label={`Contact ${i + 1} phone`} inputMode="tel" maxLength={20} className={INPUT} />
              </div>
              {contacts.length > 1 && (
                <button type="button" onClick={() => setContacts(contacts.filter((_, j) => j !== i))}
                  aria-label={`Remove contact ${i + 1}`} className="text-[11px] text-red-600 hover:underline">Remove</button>
              )}
            </div>
          )
        })}
        {contacts.length < lim.emergency_contacts && (
          <button type="button" onClick={() => setContacts([...contacts, { ...EMPTY_CONTACT }])} className={GHOST}>+ Add secondary contact</button>
        )}
      </fieldset>

      <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
        Additional notes (only what helps in an emergency)
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} maxLength={lim.notes_chars}
          aria-label="Additional notes" className={`${INPUT} mt-1`} />
        <span className="text-[11px] font-normal text-slate-400">{notes.length}/{lim.notes_chars}</span>
      </label>

      {err && <div role="alert" className="text-sm text-red-600">{err}</div>}
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className={`${BTN} bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white flex-1 py-2.5`}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" onClick={onCancel} className={`${GHOST} py-2.5`}>Cancel</button>
      </div>
    </form>
  )
}

function QrPanel({ touristId, qr, onChange }) {
  const [confirm, setConfirm] = useState('') // '' | 'regenerate' | 'revoke'
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  if (!qr) return null
  const [icon, label, cls] = QR_STATE[qr.state] || QR_STATE.active
  const act = async (fn) => {
    setBusy(true); setErr('')
    try { onChange(await fn(touristId)); setConfirm('') } catch (e) { setErr(errorText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="border-t border-slate-100 dark:border-slate-700 pt-3" data-testid="mp-qr">
      <h4 className="text-sm font-bold text-slate-800 dark:text-slate-100 mb-2">Emergency QR</h4>
      <div className="flex items-center gap-4">
        {qr.qr_png_base64 ? (
          <img src={qr.qr_png_base64} alt="Emergency QR code" className="w-28 h-28 bg-white p-1 rounded-lg border border-slate-200 dark:border-slate-600 shrink-0" />
        ) : (
          <div className="w-28 h-28 rounded-lg bg-slate-100 dark:bg-slate-700 text-xs text-slate-500 flex items-center justify-center text-center p-2 shrink-0">
            No active QR code
          </div>
        )}
        <div className="text-sm space-y-1 min-w-0">
          <div className={`font-bold ${cls}`}>{icon} {label}</div>
          <div className="text-xs text-slate-500">ID <span className="font-mono">{qr.digital_id}</span></div>
          {qr.expires_at && <div className="text-xs text-slate-500">Expires {new Date(qr.expires_at).toLocaleDateString()}</div>}
          {!qr.medical_profile_present && <div className="text-xs text-amber-700 dark:text-amber-300">No medical profile saved yet.</div>}
        </div>
      </div>
      {err && <div role="alert" className="text-xs text-red-600 mt-2">{err}</div>}
      <div className="flex flex-wrap items-center gap-2 mt-3">
        {qr.qr_png_base64 && (
          <a href={qr.qr_png_base64} download={`musafir-emergency-qr-${qr.digital_id}.png`} className={GHOST}>Download QR</a>
        )}
        {confirm === '' && (
          <>
            <button onClick={() => setConfirm('regenerate')} className={GHOST}>{qr.state === 'revoked' ? 'Create new QR' : 'Regenerate QR'}</button>
            {qr.state !== 'revoked' && <button onClick={() => setConfirm('revoke')} className={`${BTN} bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300`}>Revoke access</button>}
          </>
        )}
        {confirm !== '' && (
          <>
            <span className="text-xs text-slate-500">
              {confirm === 'revoke' ? 'Responders will no longer be able to open your profile. Continue?'
                : 'The old QR will stop working immediately. Continue?'}
            </span>
            <button onClick={() => act(confirm === 'revoke' ? revokeMedicalQr : regenerateMedicalQr)} disabled={busy}
              className={`${BTN} bg-red-600 hover:bg-red-700 text-white disabled:opacity-50`}>{busy ? 'Working…' : 'Confirm'}</button>
            <button onClick={() => setConfirm('')} className="text-xs text-slate-500 hover:underline">Cancel</button>
          </>
        )}
      </div>
    </div>
  )
}

function AccessHistory({ history }) {
  return (
    <div className="border-t border-slate-100 dark:border-slate-700 pt-3" data-testid="mp-history">
      <h4 className="text-sm font-bold text-slate-800 dark:text-slate-100 mb-1">Who has opened your profile</h4>
      {history.length === 0 ? (
        <p className="text-xs text-slate-500">Nobody has opened your medical profile.</p>
      ) : (
        <ul className="text-xs text-slate-600 dark:text-slate-300 space-y-1">
          {history.map((h) => (
            <li key={h.id}>{new Date(h.scanned_at).toLocaleString()} - {h.responder_role}{h.purpose ? ` (${purposeLabel(h.purpose)})` : ''}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
