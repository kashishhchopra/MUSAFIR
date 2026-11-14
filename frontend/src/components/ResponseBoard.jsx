import { useCallback, useEffect, useState } from 'react'
import useWebSocket from '../useWebSocket'
import useCountdown from '../hooks/useCountdown'
import { Card, SeverityBadge } from './ui.jsx'
import ResponseChip from './ResponseChip.jsx'
import {
  acknowledgeIncident, errorText, formatDuration, getBoard, getResponseConfig, localTime, overrideSeverity,
} from '../lib/responseService'

const EDGE = {
  critical: 'border-l-8 border-red-600 bg-red-50/70 dark:bg-red-950/30',
  high: 'border-l-8 border-orange-500',
  medium: 'border-l-4 border-yellow-400',
  low: 'border-l-4 border-slate-300 dark:border-slate-600',
}
const SEVERITIES = ['critical', 'high', 'medium', 'low']
const POLL_MS = 15000

function GlobalClock({ item }) {
  const active = item.escalation_status === 'active'
  const left = useCountdown(item.global_timeout_deadline, item.server_time, active)
  if (!item.global_timeout_deadline) return null
  if (item.escalation_status === 'exhausted') return <span>global timeout reached {localTime(item.global_timeout_at)}</span>
  if (!active) return <span>global timeout {localTime(item.global_timeout_deadline)}</span>
  return <span>global timeout in <b className="tabular-nums">{left == null ? '--:--' : formatDuration(left)}</b></span>
}

function Row({ item, labels, isAdmin, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [override, setOverride] = useState(false)
  const [sev, setSev] = useState(item.severity)
  const [reason, setReason] = useState('')

  const act = async (fn) => {
    setBusy(true)
    setErr(null)
    try { await fn(); setOverride(false); setReason(''); onChanged() } catch (e) { setErr(errorText(e)) } finally { setBusy(false) }
  }

  return (
    <div data-testid="response-item" data-incident={item.incident_id} data-state={item.escalation_status}
      className={`rounded-xl shadow-sm p-4 bg-white dark:bg-slate-800 ${EDGE[item.severity] || EDGE.medium}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-bold">#{item.incident_id}</span>
          <span className="capitalize">{item.type.replace('_', ' ')}</span>
          <SeverityBadge severity={item.severity} />
          {item.needs_review && (
            <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-purple-100 text-purple-800 dark:bg-purple-900/50 dark:text-purple-200">
              needs review
            </span>
          )}
          <span className="text-xs text-slate-500">{item.status}</span>
        </div>
        <ResponseChip inc={item} stageLabels={labels} />
      </div>

      <p className="text-sm text-slate-600 dark:text-slate-300 mt-2" data-testid="response-reason">
        {item.classification_reason || 'No classification recorded.'}
      </p>

      <div className="flex flex-wrap gap-x-6 gap-y-1 mt-2 text-xs text-slate-500 dark:text-slate-400">
        <span>Assigned: <b>{item.assigned_unit_name || 'unassigned'}</b></span>
        <span>Created {localTime(item.detected_at)}</span>
        <span>Respond by <b>{localTime(item.response_deadline)}</b></span>
        <span>Stage {item.response_stage}/3 · <b>{item.stage_label}</b>
          {item.next_stage_label && item.next_escalation_deadline && (
            <> → {item.next_stage_label} at {localTime(item.next_escalation_deadline)}</>
          )}
        </span>
        <GlobalClock item={item} />
        <span>{item.acknowledged
          ? `Acknowledged by ${item.acknowledged_by_name || 'staff'} at ${localTime(item.acknowledged_at)}`
          : 'Unacknowledged'}</span>
      </div>

      {err && <div role="alert" className="mt-2 text-xs text-red-600">{err}</div>}
      <div className="flex flex-wrap gap-2 mt-3">
        {!item.acknowledged && (
          <button disabled={busy} onClick={() => act(() => acknowledgeIncident(item.incident_id))}
            className="bg-yellow-500 hover:bg-yellow-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg disabled:opacity-50">
            Acknowledge
          </button>
        )}
        {isAdmin && (
          <button onClick={() => setOverride((o) => !o)}
            className="text-xs font-semibold text-sky-600 hover:text-sky-700 px-2 py-1.5">
            {override ? 'Cancel override' : 'Override severity'}
          </button>
        )}
      </div>
      {override && (
        <form className="mt-2 flex flex-wrap gap-2 items-end"
          onSubmit={(e) => { e.preventDefault(); act(() => overrideSeverity(item.incident_id, sev, reason)) }}>
          <label className="text-xs">Severity
            <select value={sev} onChange={(e) => setSev(e.target.value)}
              className="block border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600">
              {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
          <label className="text-xs flex-1 min-w-[12rem]">Reason (required)
            <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={500}
              className="block w-full border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600" />
          </label>
          <button type="submit" disabled={busy || reason.trim().length < 5}
            className="bg-sky-600 hover:bg-sky-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg disabled:opacity-40">
            Apply
          </button>
        </form>
      )}
    </div>
  )
}

// Live response board: every managed, unresolved incident with a backend-computed
// deadline. Reloads on the existing admin WebSocket feed and on a slow poll (so a
// missed push, or a refresh, always shows the stored backend state).
export default function ResponseBoard({ isAdmin = true }) {
  const [board, setBoard] = useState(null)
  const [labels, setLabels] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')

  const load = useCallback(async () => {
    try { setBoard(await getBoard()); setError(null) } catch (e) { setError(errorText(e, 'Could not load the response board')) }
  }, [])

  useEffect(() => {
    load()
    getResponseConfig().then((c) => setLabels(c.stage_labels)).catch(() => {})
    const iv = setInterval(load, POLL_MS)
    return () => clearInterval(iv)
  }, [load])
  useWebSocket((ev) => { if (ev.event === 'incident' || ev.event === 'incident_response') load() })

  const items = (board?.items || []).filter((i) => filter === 'all' || i.severity === filter)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div className="flex gap-4 text-sm" data-testid="response-summary">
          <span><b>{board?.unacknowledged ?? '–'}</b> unacknowledged</span>
          <span className={board?.overdue ? 'text-red-600 font-bold' : ''}><b>{board?.overdue ?? '–'}</b> overdue</span>
          <span><b>{board?.items?.length ?? '–'}</b> open</span>
        </div>
        <select aria-label="Filter by severity" value={filter} onChange={(e) => setFilter(e.target.value)}
          className="border rounded-lg px-2 py-1 text-sm dark:bg-slate-900 dark:border-slate-600">
          <option value="all">All severities</option>
          {SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>
      {error && <div role="alert" className="text-sm text-red-600">{error}</div>}
      {board && items.length === 0 && (
        <Card><div className="text-sm text-slate-400">No open incidents are waiting on a response.</div></Card>
      )}
      {items.map((it) => (
        <Row key={it.incident_id} item={it} labels={labels} isAdmin={isAdmin} onChanged={load} />
      ))}
      <p className="text-xs text-slate-400">
        Countdowns are anchored to the server clock. Deadlines and escalation are enforced by the backend, even
        with this page closed.
      </p>
    </div>
  )
}
