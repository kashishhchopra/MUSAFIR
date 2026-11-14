import { useEffect, useState } from 'react'
import { Card } from './ui.jsx'
import { errorText, getResponseConfig, localTime, updateResponseConfig } from '../lib/responseService'

const SEVERITIES = ['critical', 'high', 'medium', 'low']

// Admin editor for the response targets, stage-2 duration, global timeout and the
// optional deadline warning. The server validates the WHOLE configuration and
// refuses an inconsistent one (e.g. critical longer than high); this form only
// shows its message.
export default function ResponseSettings() {
  const [cfg, setCfg] = useState(null)
  const [form, setForm] = useState(null)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  const fill = (c) => {
    setCfg(c)
    setForm({ ...c.response_targets_seconds, stage2: c.stage2_seconds, global: c.global_timeout_seconds, warning: c.warning_seconds })
  }
  useEffect(() => { getResponseConfig().then(fill).catch((e) => setErr(errorText(e, 'Could not load settings'))) }, [])

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setErr(null)
    setMsg(null)
    try {
      const targets = Object.fromEntries(SEVERITIES.map((s) => [s, Number(form[s])]))
      fill(await updateResponseConfig({
        response_targets_seconds: targets, stage2_seconds: Number(form.stage2),
        global_timeout_seconds: Number(form.global), warning_seconds: Number(form.warning),
      }))
      setMsg('Saved. New incidents use these values; open incidents keep the deadlines they were given.')
    } catch (e2) { setErr(errorText(e2)) } finally { setBusy(false) }
  }

  if (!form) return <Card>{err ? <span role="alert" className="text-red-600 text-sm">{err}</span> : 'Loading…'}</Card>
  const num = (k) => (
    <input type="number" min={k === 'warning' ? 0 : 5} value={form[k]} id={`rs-${k}`}
      onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
      className="w-28 border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600" />
  )
  return (
    <form onSubmit={save} className="space-y-4" data-testid="response-settings">
      <Card title="Response-time targets (seconds to acknowledge)">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          {SEVERITIES.map((s) => (
            <label key={s} className="capitalize" htmlFor={`rs-${s}`}>{s}<div>{num(s)}</div></label>
          ))}
        </div>
        <p className="text-xs text-slate-500 mt-2">
          Illustrative demonstration values, not emergency-service standards. Must not get longer for a more serious severity.
        </p>
      </Card>
      <Card title="Escalation">
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
          <label htmlFor="rs-stage2">Stage 2 duration (s)<div>{num('stage2')}</div></label>
          <label htmlFor="rs-global">Global timeout (s)<div>{num('global')}</div></label>
          <label htmlFor="rs-warning">Deadline warning (s, 0 = off)<div>{num('warning')}</div></label>
        </div>
        <ul className="text-xs text-slate-500 mt-3 space-y-0.5">
          {cfg.stage_labels.map((l, i) => (
            <li key={l}>Stage {i + 1} · {l}: notifies {(cfg.audiences[String(i + 1)] || []).join(', ')}</li>
          ))}
          <li>Global timeout notifies: {(cfg.audiences.global || []).join(', ')} — the incident stays open.</li>
          {cfg.updated_at && <li>Last changed {localTime(cfg.updated_at)}</li>}
        </ul>
      </Card>
      {err && <div role="alert" className="text-sm text-red-600">{err}</div>}
      {msg && <div role="status" className="text-sm text-green-700">{msg}</div>}
      <button type="submit" disabled={busy}
        className="bg-sky-600 hover:bg-sky-700 text-white text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50">
        {busy ? 'Saving…' : 'Save settings'}
      </button>
    </form>
  )
}
