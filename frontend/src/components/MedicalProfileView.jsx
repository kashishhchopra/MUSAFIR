import { purposeLabel } from '../lib/medicalProfileService'

// The responder's medical profile page. Plain, high-contrast, single column so
// it reads on a phone in poor light. It renders only what the backend returned
// for an authorized, audited scan; everything is labelled tourist-reported.
//
// "Not provided" and "none known" are deliberately different messages: a blank
// field must never read as the absence of an allergy or condition.

const NOT_PROVIDED = 'Not provided by tourist'

function Section({ title, children, tone = 'plain', testid }) {
  const tones = {
    plain: 'bg-white dark:bg-slate-800 border-slate-200 dark:border-slate-700',
    alert: 'bg-red-50 dark:bg-red-950/40 border-red-400 dark:border-red-700',
  }
  return (
    <section data-testid={testid} aria-label={title}
      className={`rounded-xl border-2 p-4 ${tones[tone]}`}>
      <h3 className="text-xs font-bold uppercase tracking-wider text-slate-600 dark:text-slate-300 mb-2">{title}</h3>
      {children}
    </section>
  )
}

const Empty = ({ children = NOT_PROVIDED }) => (
  <p className="text-sm italic text-slate-500 dark:text-slate-400">{children}</p>
)

export default function MedicalProfileView({ result, onBack }) {
  const { tourist, medical: m, provenance, accessed_at: accessedAt, purpose, request_id: rid } = result
  const allergyAlert = m.allergies_status === 'listed' && m.allergies.length > 0

  return (
    <div className="space-y-3" data-testid="medical-profile-view">
      <button onClick={onBack}
        className="w-full text-sm font-semibold bg-slate-100 dark:bg-slate-700 text-slate-800 dark:text-slate-100 rounded-xl py-3">
        ← Back to scanner
      </button>

      <Section title="Tourist" testid="mp-identity">
        <div className="flex gap-3 items-center">
          {tourist.photo ? (
            <img src={tourist.photo} alt={tourist.full_name} className="w-16 h-16 rounded-xl object-cover shrink-0" />
          ) : (
            <div aria-hidden="true" className="w-16 h-16 rounded-xl bg-slate-100 dark:bg-slate-700 flex items-center justify-center text-2xl shrink-0">🪪</div>
          )}
          <div className="min-w-0">
            <div className="text-lg font-bold text-slate-900 dark:text-slate-100 break-words">{tourist.full_name}</div>
            <div className="text-sm text-slate-600 dark:text-slate-300">
              MUSAFIR ID <span className="font-mono font-semibold">{tourist.digital_id}</span>
            </div>
          </div>
        </div>
      </Section>

      <div role="note" className="text-xs rounded-lg bg-amber-50 dark:bg-amber-900/30 text-amber-900 dark:text-amber-200 border border-amber-300 dark:border-amber-700 p-2.5">
        ⚠ {provenance?.note || 'Reported by the tourist. Not verified by any medical authority.'}
      </div>

      <Section title="Blood group" testid="mp-blood">
        {m.blood_group ? (
          <div className="text-5xl font-extrabold text-red-700 dark:text-red-400 leading-none" aria-label={`Blood group ${m.blood_group}`}>
            {m.blood_group}
          </div>
        ) : <Empty />}
      </Section>

      <Section title={allergyAlert ? '⚠ Allergies' : 'Allergies'} tone={allergyAlert ? 'alert' : 'plain'} testid="mp-allergies">
        {m.allergies_status === 'none_known' && (
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Tourist reports NO known allergies</p>
        )}
        {m.allergies_status === 'not_provided' && <Empty />}
        {allergyAlert && (
          <ul className="space-y-1.5">
            {m.allergies.map((a, i) => (
              <li key={i} className="text-base font-bold text-red-800 dark:text-red-200">
                {a.name}
                <span className="ml-2 text-xs font-semibold uppercase text-red-700 dark:text-red-300">{a.category}</span>
                {a.reaction && <span className="block text-sm font-normal">Reaction: {a.reaction}</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Medical conditions" testid="mp-conditions">
        {m.conditions_status === 'none_known' && (
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Tourist reports NO known conditions</p>
        )}
        {m.conditions_status === 'not_provided' && <Empty />}
        {m.conditions_status === 'listed' && (
          <ul className="space-y-1.5">
            {m.medical_conditions.map((c, i) => (
              <li key={i} className="text-base font-semibold text-slate-900 dark:text-slate-100">
                {c.name}
                {c.description && <span className="block text-sm font-normal text-slate-600 dark:text-slate-300">{c.description}</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Current medications" testid="mp-medications">
        {m.medications_status === 'none_known' && (
          <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Tourist reports NO current medication</p>
        )}
        {m.medications_status === 'not_provided' && <Empty />}
        {m.medications_status === 'listed' && (
          <ul className="space-y-1.5">
            {m.medications.map((d, i) => (
              <li key={i} className="text-base font-semibold text-slate-900 dark:text-slate-100">
                {d.name}
                <span className="block text-sm font-normal text-slate-600 dark:text-slate-300">
                  {[d.dosage, d.frequency].filter(Boolean).join(' · ') || 'Dose not stated'}
                  {d.notes ? ` — ${d.notes}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
        {m.medications_status === 'listed' && (
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1">Doses as reported by the tourist.</p>
        )}
      </Section>

      <Section title="Emergency contacts" testid="mp-contacts">
        {m.emergency_contacts.length === 0 ? <Empty /> : (
          <ul className="space-y-2">
            {m.emergency_contacts.map((c, i) => (
              <li key={i} className="flex items-center justify-between gap-2">
                <span className="text-base font-semibold text-slate-900 dark:text-slate-100">
                  {c.name} <span className="text-sm font-normal text-slate-500 dark:text-slate-400">({c.relationship})</span>
                </span>
                <a href={`tel:${c.phone.replace(/[^+\d]/g, '')}`}
                  className="shrink-0 bg-green-600 text-white text-sm font-semibold rounded-lg px-3 py-2">
                  📞 {c.phone}
                </a>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Additional notes" testid="mp-notes">
        {m.additional_notes
          ? <p className="text-sm whitespace-pre-wrap text-slate-800 dark:text-slate-100">{m.additional_notes}</p>
          : <Empty />}
      </Section>

      <div className="text-[11px] text-slate-500 dark:text-slate-400 space-y-0.5 px-1">
        <div>Profile last updated: {m.profile_updated_at ? new Date(m.profile_updated_at).toLocaleString() : 'unknown'}</div>
        <div>Accessed: {accessedAt ? new Date(accessedAt).toLocaleString() : '—'}{purpose ? ` · ${purposeLabel(purpose)}` : ''}</div>
        <div>This access has been recorded. Ref {rid}</div>
      </div>

      <button onClick={onBack}
        className="w-full text-sm font-semibold bg-sky-600 text-white rounded-xl py-3">
        Scan another QR
      </button>
    </div>
  )
}
