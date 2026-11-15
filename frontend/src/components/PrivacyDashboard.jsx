import { useCallback, useEffect, useState } from 'react'
import { errorText, getOverview } from '../lib/privacyService'
import ConsentPanel from './privacy/ConsentPanel.jsx'
import DataPanel from './privacy/DataPanel.jsx'
import GrievancePanel from './privacy/GrievancePanel.jsx'
import LocationPanel from './privacy/LocationPanel.jsx'

const TABS = [
  ['overview', 'Overview'], ['consents', 'Consents'], ['location', 'Location'],
  ['data', 'My data'], ['retention', 'Retention'], ['help', 'Support'],
]
const BOX = 'bg-white dark:bg-slate-800 rounded-[var(--theme-radius)] shadow-[var(--theme-shadow)] p-4'

function Overview({ ov }) {
  return (
    <div className="space-y-3 text-xs text-slate-600 dark:text-slate-300">
      <p><b>Privacy notice</b> version {ov.notice.version}, updated {ov.notice.updated}.</p>
      <div>
        <b>Information we collect, and why</b>
        <ul className="mt-1 space-y-1" aria-label="Data collected">
          {ov.data_inventory.map((c) => (
            <li key={c.category} className="border border-slate-200 dark:border-slate-700 rounded-lg p-2">
              <span className="font-semibold text-slate-800 dark:text-slate-100">{c.items.join(', ')}</span>
              <span className="ml-1 text-[10px] uppercase tracking-wide">{c.necessary === 'required' ? 'needed' : 'optional'}</span>
              <div>{c.purpose}</div>
              <div className="text-slate-500">Shared with: {c.shared_with}</div>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <b>Your rights here</b>
        <ul className="list-disc ml-4">{ov.notice.rights.map((r) => <li key={r}>{r}</li>)}</ul>
      </div>
      <p className="text-[11px] text-slate-500 border-t border-slate-200 dark:border-slate-700 pt-2">{ov.notice.legal_note}</p>
    </div>
  )
}

function Retention({ ov }) {
  return (
    <div className="space-y-2 text-xs">
      <p className="text-slate-500 dark:text-slate-400">How long each kind of data is kept and why. "No period established" means it is not deleted on a timer; it stays until you delete it or your account.</p>
      {ov.holds_on_my_data.length > 0 && (
        <div className="border border-orange-300 rounded-lg p-2 text-orange-900 dark:text-orange-200 bg-orange-50 dark:bg-orange-900/20">
          <b>Your data is under a documented hold</b>
          <ul className="list-disc ml-4">{ov.holds_on_my_data.map((h, i) => <li key={i}>{h.category}: {h.reason}</li>)}</ul>
        </div>
      )}
      <ul className="space-y-1.5" aria-label="Retention periods">
        {ov.retention.map((p) => (
          <li key={p.category} className="border border-slate-200 dark:border-slate-700 rounded-lg p-2">
            <div className="flex justify-between gap-2">
              <span className="font-semibold text-slate-800 dark:text-slate-100">{p.title}</span>
              <span>{p.retention_days === null ? 'No period established' : `${p.retention_days} day(s) after ${p.start_condition.replace(/_/g, ' ')}`}</span>
            </div>
            <div className="text-slate-500">{p.purpose} · Basis: {p.legal_basis}</div>
            <div className="text-slate-500">{p.state}{p.per_tourist && ' · you can choose your own period'}{p.requires_review && ' · under review'}</div>
          </li>
        ))}
      </ul>
    </div>
  )
}

// Privacy Dashboard: notice, consents, location history, "my data" (export and
// account deletion), retention information and a grievance channel -- every
// control calls its real backend endpoint; nothing is simulated here.
export default function PrivacyDashboard({ touristId, onSignOut }) {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState('overview')
  const [ov, setOv] = useState(null)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try { setOv(await getOverview(touristId)); setError('') }
    catch (e) { setError(errorText(e, 'Could not load your privacy information.')) }
  }, [touristId])
  useEffect(() => { if (open && !ov) load() }, [open, ov, load])

  return (
    <div className={BOX} data-testid="privacy-dashboard">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="w-full flex items-center justify-between gap-3 text-left">
        <span className="flex items-center gap-3 min-w-0">
          <span className="w-9 h-9 rounded-full bg-indigo-50 dark:bg-indigo-900/30 flex items-center justify-center text-base shrink-0">🔒</span>
          <span className="min-w-0">
            <span className="block font-medium text-slate-900 dark:text-slate-100">Privacy Dashboard</span>
            <span className="block text-xs text-slate-500 dark:text-slate-400">Consents, your data, download &amp; delete</span>
          </span>
        </span>
        <span className="text-slate-400">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="mt-3 space-y-3">
          {error && <div role="alert" className="text-xs text-red-600">{error}</div>}
          {!ov && !error && <p className="text-xs text-slate-500">Loading…</p>}
          {ov && (
            <>
              <div role="tablist" aria-label="Privacy sections" className="flex gap-1 overflow-x-auto pb-1">
                {TABS.map(([k, label]) => (
                  <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                    className={`text-xs font-semibold px-3 py-1.5 rounded-full whitespace-nowrap ${tab === k ? 'bg-indigo-600 text-white' : 'bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200'}`}>
                    {label}
                  </button>
                ))}
              </div>
              <div role="tabpanel">
                {tab === 'overview' && <Overview ov={ov} />}
                {tab === 'consents' && <ConsentPanel touristId={touristId} consents={ov.consents} onChanged={load} />}
                {tab === 'location' && <LocationPanel touristId={touristId} onChanged={load} />}
                {tab === 'data' && <DataPanel touristId={touristId} deletion={ov.deletion} onChanged={load} onSignOut={onSignOut} />}
                {tab === 'retention' && <Retention ov={ov} />}
                {tab === 'help' && <GrievancePanel touristId={touristId} contactEmail={ov.notice.contact_email} responseDays={ov.grievance_response_days} />}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
