import { useState } from 'react'
import { errorText, getConsentHistory, grantConsent, withdrawConsent } from '../../lib/privacyService'

const BADGE = {
  granted: 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
  withdrawn: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  not_recorded: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300',
  superseded: 'bg-slate-100 text-slate-500',
}
const LABEL = { granted: 'Allowed', withdrawn: 'Withdrawn', not_recorded: 'Not set', superseded: 'Replaced' }
const fmt = (d) => (d ? new Date(d).toLocaleString() : '-')

// Consent: one row per purpose, withdraw as easily as you grant. Withdrawal
// shows what stops *before* it happens, and reports what the server actually
// switched off afterwards.
export default function ConsentPanel({ touristId, consents, onChanged }) {
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState(null)
  const [confirming, setConfirming] = useState(null)
  const [history, setHistory] = useState(null)

  const act = async (fn, purpose) => {
    setBusy(purpose)
    setMsg(null)
    try {
      const res = await fn()
      setMsg({ ok: true, text: res.message || 'Saved.' })
      setConfirming(null)
      if (history) setHistory(await getConsentHistory(touristId))
      onChanged?.()
    } catch (e) {
      setMsg({ ok: false, text: errorText(e) })
    } finally {
      setBusy('')
    }
  }

  const toggleHistory = async () => {
    if (history) return setHistory(null)
    try { setHistory(await getConsentHistory(touristId)) } catch (e) { setMsg({ ok: false, text: errorText(e) }) }
  }

  return (
    <div className="space-y-3">
      {msg && (
        <div role="status" className={`text-xs rounded-lg p-2 ${msg.ok ? 'bg-green-50 text-green-800 dark:bg-green-900/20 dark:text-green-300' : 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-300'}`}>
          {msg.text}
        </div>
      )}
      {consents.map((c) => (
        <div key={c.key} className="border border-slate-200 dark:border-slate-700 rounded-xl p-3" data-testid={`consent-${c.key}`}>
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-slate-900 dark:text-slate-100">{c.title}</div>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">{c.description}</p>
            </div>
            {c.kind === 'consent'
              ? <span className={`shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded-full ${BADGE[c.status] || BADGE.not_recorded}`}>{LABEL[c.status] || c.status}</span>
              : <span className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300">Required</span>}
          </div>
          <p className="text-[11px] text-slate-500 dark:text-slate-400 mt-1"><b>Shared with:</b> {c.shared_with}</p>
          <p className="text-[11px] text-slate-500 dark:text-slate-400"><b>Basis:</b> {c.basis_note}</p>
          {c.kind === 'consent' && c.consented_at && <p className="text-[11px] text-slate-400">Given {fmt(c.consented_at)} (notice v{c.notice_version}){c.withdrawn_at && <> · withdrawn {fmt(c.withdrawn_at)}</>}</p>}
          {c.needs_review && <p className="text-[11px] text-orange-600 mt-1">The privacy notice has changed since you agreed. Please review and allow again.</p>}

          {c.kind === 'consent' && (
            <div className="mt-2">
              {confirming === c.key ? (
                <div className="bg-orange-50 dark:bg-orange-900/20 rounded-lg p-2 text-xs text-orange-900 dark:text-orange-200">
                  <p className="mb-2"><b>What stops:</b> {c.withdraw_effect}</p>
                  <div className="flex gap-2">
                    <button disabled={busy === c.key} onClick={() => act(() => withdrawConsent(touristId, c.key), c.key)}
                      className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-orange-600 text-white disabled:opacity-50">Yes, withdraw</button>
                    <button onClick={() => setConfirming(null)} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700">Keep it</button>
                  </div>
                </div>
              ) : c.status === 'granted' && !c.needs_review ? (
                <button onClick={() => setConfirming(c.key)}
                  className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-orange-300 text-orange-700 dark:text-orange-300">
                  Withdraw consent
                </button>
              ) : (
                <button disabled={busy === c.key} onClick={() => act(() => grantConsent(touristId, c.key), c.key)}
                  className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-sky-600 text-white disabled:opacity-50">
                  {c.status === 'granted' ? 'Allow again (new notice)' : 'Allow'}
                </button>
              )}
            </div>
          )}
        </div>
      ))}

      <button onClick={toggleHistory} className="text-xs font-semibold text-sky-600">
        {history ? 'Hide consent history' : 'View consent history'}
      </button>
      {history && (
        <ul className="text-xs space-y-1" aria-label="Consent history">
          {history.length === 0 && <li className="text-slate-500">No consent records yet.</li>}
          {history.map((h) => (
            <li key={h.consent_id} className="border-l-2 border-slate-300 dark:border-slate-600 pl-2">
              <span className="font-mono">{h.consent_id}</span> · {h.purpose} · <b>{LABEL[h.status] || h.status}</b> · v{h.consent_version}/notice {h.notice_version}
              <div className="text-slate-500">{fmt(h.consented_at)}{h.withdrawn_at && ` → withdrawn ${fmt(h.withdrawn_at)}`} · via {h.source}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
