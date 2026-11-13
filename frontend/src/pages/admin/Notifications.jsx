import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import api from '../../api'
import { Card, Stat } from '../../components/ui.jsx'
import PushToggle from '../../components/PushToggle.jsx'
import { relativeTime } from '../../lib/relativeTime'
import { currentSubscription } from '../../lib/pushService'

/**
 * Push & SMS alert history for the control room (Firebase-console style table),
 * with delivery status, retries, a test sender, and previews of what recipients
 * actually see. Data: /notifications/{summary,logs,test} (backend
 * services/notifications.py).
 */

const POLL_MS = 5000
const PAGE = 25

const STATUS = {
  sent: { icon: '✓', label: 'Sent', cls: 'text-emerald-600' },
  simulated: { icon: '◌', label: 'Simulated (demo)', cls: 'text-slate-500' },
  failed: { icon: '✕', label: 'Failed', cls: 'text-red-600' },
  retrying: { icon: '⟳', label: 'Retrying', cls: 'text-amber-600' },
  queued: { icon: '◔', label: 'Queued', cls: 'text-sky-600' },
  sending: { icon: '◔', label: 'Sending', cls: 'text-sky-600' },
  suppressed: { icon: '⊘', label: 'Not sent', cls: 'text-slate-500' },
}
const EVENTS = ['sos', 'incident_update', 'critical_incident', 'escalation', 'test']
const SEV_DOT = { critical: 'bg-red-500', high: 'bg-orange-500', info: 'bg-sky-500' }

function StatusCell({ n }) {
  const s = STATUS[n.status] || STATUS.queued
  return (
    <div>
      <span className={`font-semibold ${s.cls}`}>{s.icon} {s.label}</span>
      {n.status === 'retrying' && (
        <div className="text-[11px] text-slate-500">attempt {n.attempts} · next {n.next_retry_at ? relativeTime(n.next_retry_at)?.replace(' ago', ' (due)') : 'soon'}</div>
      )}
      {(n.status === 'failed' || n.status === 'suppressed' || n.status === 'retrying') && n.last_error && (
        <div className="text-[11px] text-slate-500 max-w-[220px] truncate" title={n.last_error}>{n.last_error}</div>
      )}
    </div>
  )
}

// ---- what the recipient sees ------------------------------------------------
function PushPreview({ title, body, sample }) {
  return (
    <div className="rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-sky-900 p-5">
      <div className="rounded-2xl bg-white/95 shadow-xl p-3 max-w-sm">
        <div className="flex items-center gap-2 text-xs text-slate-500 mb-1.5">
          <img src="/icon-192.png" alt="" className="w-5 h-5 rounded" />
          <span className="font-semibold tracking-wide">MUSAFIR</span>
          <span className="ml-auto">now</span>
        </div>
        <div className="font-bold text-slate-900 text-sm">{title}</div>
        <div className="text-slate-700 text-sm leading-snug">{body}</div>
      </div>
      <div className="text-[11px] text-slate-300 mt-2">{sample ? 'Sample — no alert has been sent yet' : 'Latest SOS push, as shown on a lock screen'}</div>
    </div>
  )
}

function SmsPreview({ body, sample }) {
  return (
    <div className="rounded-2xl bg-slate-100 dark:bg-slate-700/60 p-5">
      <div className="text-center text-[11px] text-slate-500 mb-2">Text message · today</div>
      <div className="rounded-2xl rounded-bl-sm bg-white dark:bg-slate-800 shadow p-3 max-w-sm text-sm text-slate-800 dark:text-slate-100 whitespace-pre-wrap">{body}</div>
      <div className="text-[11px] text-slate-500 mt-2">{sample ? 'Sample — no SMS has been queued yet' : 'Latest SOS SMS, as an emergency contact receives it'}</div>
    </div>
  )
}

function ConfigChip({ ok, children }) {
  return (
    <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${ok
      ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300'
      : 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'}`}>{children}</span>
  )
}

export default function Notifications() {
  const [summary, setSummary] = useState(null)
  const [page, setPage] = useState({ total: 0, items: [] })
  const [channel, setChannel] = useState('')
  const [status, setStatus] = useState('')
  const [eventType, setEventType] = useState('')
  const [offset, setOffset] = useState(0)
  const [latest, setLatest] = useState({ push: null, sms: null })
  const [testPhone, setTestPhone] = useState('')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [subscribed, setSubscribed] = useState(false)
  const refreshSub = useCallback(() => { currentSubscription().then((s) => setSubscribed(Boolean(s))).catch(() => setSubscribed(false)) }, [])
  useEffect(() => { refreshSub() }, [refreshSub])

  const load = useCallback(async () => {
    const params = { limit: PAGE, offset }
    if (channel) params.channel = channel
    if (status) params.status = status
    if (eventType) params.event = eventType
    try {
      const [s, l, p, m] = await Promise.all([
        api.get('/notifications/summary'),
        api.get('/notifications/logs', { params }),
        api.get('/notifications/logs', { params: { channel: 'push', event: 'sos', limit: 1 } }),
        api.get('/notifications/logs', { params: { channel: 'sms', event: 'sos', limit: 1 } }),
      ])
      setSummary(s.data); setPage(l.data)
      setLatest({ push: p.data.items[0] || null, sms: m.data.items[0] || null })
    } finally { setLoading(false) }
  }, [channel, status, eventType, offset])

  useEffect(() => {
    load()
    const iv = setInterval(load, POLL_MS)
    return () => clearInterval(iv)
  }, [load])

  const flash = (text, ok = true) => { setMsg({ text, ok }); setTimeout(() => setMsg(null), 6000) }
  // `okText` may be a string or a function of the API response, so the message can report what really happened.
  const act = async (fn, okText) => {
    setBusy(true)
    try {
      const res = await fn()
      const text = typeof okText === 'function' ? okText(res?.data) : okText
      flash(text.text || text, text.ok !== false)
      await load()
    } catch (e) {
      flash(e?.response?.data?.detail || e.message || 'Request failed', false)
    } finally { setBusy(false) }
  }

  const by = summary?.by_status || {}
  const cfg = summary?.config
  const subs = summary?.subscribers
  const sampleSms = useMemo(() => latest.sms?.body
    || 'MUSAFIR SOS: Rahul has triggered an SOS. Last known location: 26.14450, 91.73620 (https://maps.google.com/?q=26.14450,91.73620). Message: Help, I feel unsafe.', [latest.sms])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-bold text-slate-100">Push &amp; SMS Alerts</h2>
          <p className="text-sm text-slate-300">
            Emergency notifications to tourists, guardians, responders and emergency contacts — with delivery status.
          </p>
        </div>
        {cfg && (
          <div className="flex flex-wrap gap-2 items-center">
            <ConfigChip ok={cfg.push.enabled}>Push: {cfg.push.mode}</ConfigChip>
            <ConfigChip ok={cfg.sms.live}>SMS: {cfg.sms.mode}</ConfigChip>
            {cfg.sms.allowlist_active && <ConfigChip ok>SMS allowlist on</ConfigChip>}
          </div>
        )}
      </div>

      {msg && <div role="status" className={`rounded-lg border px-3 py-2 text-sm ${msg.ok
        ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-red-300 bg-red-50 text-red-700'}`}>{msg.text}</div>}

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <Stat label="Notifications" value={summary?.total ?? '—'} />
        <Stat label="Delivered" value={(by.sent || 0)} accent="text-emerald-600" />
        <Stat label="Simulated (demo)" value={by.simulated || 0} accent="text-slate-500" />
        <Stat label="Failed" value={by.failed || 0} accent="text-red-600" />
        <Stat label="Retrying" value={(by.retrying || 0) + (by.queued || 0) + (by.sending || 0)} accent="text-amber-600" />
        <Stat label="Push devices" value={subs?.total ?? '—'} />
      </div>
      {subs?.total > 0 && (
        <div className="text-xs text-slate-400 -mt-2">
          Subscribed devices: {Object.entries(subs.by_role).map(([r, n]) => `${n} ${r}`).join(' · ')}
          {by.suppressed ? ` · ${by.suppressed} not sent (duplicates / blocked)` : ''}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Card title="This device & test">
          <div className="space-y-3 text-sm">
            <PushToggle onChange={() => { load(); refreshSub() }} />
            <div className="flex flex-wrap gap-2">
              <button disabled={busy || !subscribed} onClick={() => act(() => api.post('/notifications/test', { channel: 'push' }), (d) => {
                const s = d?.items?.[0]
                return s?.status === 'sent' ? 'Test push sent to your devices' : { text: `Test push ${s?.status || 'requested'}${s?.last_error ? `: ${s.last_error}` : ''}`, ok: s?.status !== 'failed' }
              })}
                className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-sm font-semibold hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50 disabled:cursor-not-allowed">
                Send test push
              </button>
            </div>
            {!subscribed && (
              <p className="text-xs text-amber-700 dark:text-amber-300">
                Turn on alerts for this device first. Push only works in the built app
                (<code>npm run build &amp;&amp; npm run preview</code>, then open localhost:4173) — the service worker doesn’t run under <code>npm run dev</code>.
              </p>
            )}
            <div className="flex gap-2">
              <input value={testPhone} onChange={(e) => setTestPhone(e.target.value)} placeholder="+91 98765 43210" aria-label="Test phone number"
                className="flex-1 min-w-0 px-2 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800 text-sm" />
              <button disabled={busy || !testPhone.trim()}
                onClick={() => act(() => api.post('/notifications/test', { channel: 'sms', to: testPhone.trim() }), (d) => {
                  const s = d?.items?.[0]
                  if (!s) return { text: 'Test SMS requested', ok: true }
                  if (s.status === 'sent') return { text: 'Test SMS sent — check your phone.', ok: true }
                  if (s.status === 'simulated') return { text: 'Recorded as Simulated: SMS is in demo mode, so nothing was texted. Set up Twilio to send real SMS.', ok: false }
                  if (s.status === 'suppressed') return { text: `Not sent: ${s.last_error || 'blocked'}`, ok: false }
                  if (s.status === 'failed' || s.status === 'retrying') return { text: `SMS ${s.status}: ${s.last_error || 'see history'}`, ok: false }
                  return { text: `Test SMS ${s.status} — see history below.`, ok: true }
                })}
                className="px-3 py-1.5 rounded-lg border border-slate-300 dark:border-slate-600 text-sm font-semibold hover:bg-slate-50 dark:hover:bg-slate-700 disabled:opacity-50">
                Send test SMS
              </button>
            </div>
            <p className="text-xs text-slate-500">
              {cfg && !cfg.sms.live ? 'SMS is in demo mode: messages are logged as “Simulated”, nothing is texted. Set NOTIFICATION_CHANNEL=twilio and the TWILIO_* variables to send real SMS.' : 'SMS goes out through Twilio.'}
            </p>
          </div>
        </Card>
        <PushPreview title={latest.push?.title || '🚨 SOS ALERT — Rahul'} sample={!latest.push}
          body={latest.push?.body || 'Rahul has triggered an emergency SOS. Location: Old Market High-Risk Zone. Status: dispatched to Unit Alpha.'} />
        <SmsPreview body={sampleSms} sample={!latest.sms} />
      </div>

      <Card title="Notification history" actions={
        <div className="flex flex-wrap gap-2 text-xs">
          {[['Channel', channel, setChannel, [['', 'All channels'], ['push', 'Push'], ['sms', 'SMS']]],
            ['Status', status, setStatus, [['', 'All statuses'], ...Object.keys(STATUS).map((k) => [k, STATUS[k].label])]],
            ['Event', eventType, setEventType, [['', 'All events'], ...EVENTS.map((e) => [e, e.replace('_', ' ')])]]].map(([label, val, set, opts]) => (
            <select key={label} aria-label={label} value={val} onChange={(e) => { set(e.target.value); setOffset(0) }}
              className="px-2 py-1 rounded-lg border border-slate-300 dark:border-slate-600 bg-white dark:bg-slate-800">
              {opts.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select>
          ))}
        </div>}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-slate-500 border-b border-slate-200 dark:border-slate-700">
                {['Notification', 'Status', 'Channel', 'Recipient', 'Incident', 'Sent', ''].map((h) => <th key={h} className="py-2 pr-3 font-semibold">{h}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-700/60">
              {loading && <tr><td colSpan={7} className="py-6 text-center text-slate-400">Loading…</td></tr>}
              {!loading && page.items.length === 0 && (
                <tr><td colSpan={7} className="py-8 text-center text-slate-500">
                  No notifications yet. They appear here when someone triggers an SOS — try the Simulation page, or send a test above.
                </td></tr>
              )}
              {page.items.map((n) => (
                <tr key={n.id} className="align-top">
                  <td className="py-2 pr-3 max-w-[380px]">
                    <div className="flex items-start gap-2">
                      <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${SEV_DOT[n.severity] || SEV_DOT.info}`} title={n.severity} />
                      <div className="min-w-0">
                        <div className="font-semibold text-slate-800 dark:text-slate-100 truncate">{n.title}</div>
                        <div className="text-xs text-slate-500 truncate" title={n.body}>{n.body}</div>
                        <div className="text-[10px] uppercase tracking-wide text-slate-400">{n.event_type.replace('_', ' ')}</div>
                      </div>
                    </div>
                  </td>
                  <td className="py-2 pr-3"><StatusCell n={n} /></td>
                  <td className="py-2 pr-3 whitespace-nowrap">{n.channel === 'push' ? '🔔 Push' : '💬 SMS'}</td>
                  <td className="py-2 pr-3 max-w-[180px] truncate" title={n.recipient}>{n.recipient}</td>
                  <td className="py-2 pr-3">
                    {n.incident_id ? <Link to={`/admin/incidents?incident=${n.incident_id}`} className="text-sky-600 hover:underline font-semibold">#{n.incident_id}</Link> : '—'}
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap text-xs text-slate-500">
                    {n.sent_at ? relativeTime(n.sent_at) : relativeTime(n.created_at)}
                    {n.provider_id && <div className="font-mono text-[10px]" title="Provider message id">{n.provider_id}</div>}
                  </td>
                  <td className="py-2">
                    {['failed', 'retrying', 'simulated'].includes(n.status) && (
                      <button disabled={busy} onClick={() => act(() => api.post(`/notifications/logs/${n.id}/retry`), `Notification #${n.id} re-sent`)}
                        className="text-xs font-semibold text-sky-600 hover:text-sky-700 disabled:opacity-50">Retry</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between text-xs text-slate-500 pt-3">
          <span>{page.total === 0 ? '0' : `${offset + 1}–${Math.min(offset + PAGE, page.total)}`} of {page.total}</span>
          <div className="flex gap-2">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="px-2 py-1 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-40">‹ Prev</button>
            <button disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)} className="px-2 py-1 rounded border border-slate-300 dark:border-slate-600 disabled:opacity-40">Next ›</button>
          </div>
        </div>
      </Card>
    </div>
  )
}
