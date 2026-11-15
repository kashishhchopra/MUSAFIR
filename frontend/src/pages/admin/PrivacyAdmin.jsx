import { useCallback, useEffect, useState } from 'react'
import {
  adminAddException, adminDeletionRequests, adminExceptions, adminGrievance, adminGrievances,
  adminJobs, adminPolicies, adminProcessDeletions, adminReleaseException, adminRespond,
  adminRunRetention, adminUpdatePolicy, errorText,
} from '../../lib/privacyService'

const card = 'bg-white dark:bg-slate-800 rounded-xl shadow p-4'
const input = 'border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2 py-1.5 text-sm'
const fmt = (d) => (d ? new Date(d).toLocaleString() : '-')

function PolicyRow({ p, onSaved, onError }) {
  const [days, setDays] = useState(p.retention_days ?? '')
  const [active, setActive] = useState(p.is_active)
  const [reviewed, setReviewed] = useState(!p.requires_review)
  const save = async () => {
    try {
      await adminUpdatePolicy(p.category, {
        retention_days: days === '' ? null : Number(days), is_active: active, requires_review: !reviewed,
      })
      onSaved()
    } catch (e) { onError(errorText(e)) }
  }
  return (
    <tr className="border-t border-slate-200 dark:border-slate-700 align-top">
      <td className="py-2 pr-2"><div className="font-semibold">{p.title}</div><div className="text-xs text-slate-500">{p.category} · {p.legal_basis}</div></td>
      <td className="pr-2"><input aria-label={`Retention days for ${p.category}`} type="number" min={0} className={`${input} w-24`}
        value={days} placeholder="none" onChange={(e) => setDays(e.target.value)} /></td>
      <td className="pr-2 text-xs">{p.deletion_action} / on erasure: {p.account_deletion_action}</td>
      <td className="pr-2"><label className="text-xs flex items-center gap-1">
        <input type="checkbox" aria-label={`Scheduled deletion for ${p.category}`} checked={active} disabled={!p.automated}
          onChange={(e) => setActive(e.target.checked)} />{p.automated ? 'on' : 'no auto job'}</label></td>
      <td className="pr-2"><label className="text-xs flex items-center gap-1">
        <input type="checkbox" aria-label={`Legal review done for ${p.category}`} checked={reviewed}
          onChange={(e) => setReviewed(e.target.checked)} />reviewed</label></td>
      <td><button onClick={save} className="text-xs font-semibold text-sky-600">Save</button></td>
    </tr>
  )
}

function GrievanceDetail({ gid, onClose, onChanged }) {
  const [g, setG] = useState(null)
  const [msg, setMsg] = useState('')
  const [status, setStatus] = useState('in_review')
  const [err, setErr] = useState('')
  useEffect(() => { adminGrievance(gid).then(setG).catch((e) => setErr(errorText(e))) }, [gid])
  const send = async () => {
    try { setG(await adminRespond(gid, { message: msg, status })); setMsg(''); onChanged() }
    catch (e) { setErr(errorText(e)) }
  }
  if (!g) return <p className="text-sm">{err || 'Loading…'}</p>
  return (
    <div className="border border-slate-200 dark:border-slate-700 rounded-lg p-3 mt-2 text-sm space-y-2">
      <div className="flex justify-between"><b>{g.grievance_id}</b><button onClick={onClose} className="text-xs">Close</button></div>
      <p className="whitespace-pre-wrap">{g.description}</p>
      {g.responses.map((r, i) => <div key={i} className="text-xs bg-slate-50 dark:bg-slate-700/40 rounded p-2"><b>{r.author_role}</b> · {fmt(r.created_at)}<p>{r.message}</p></div>)}
      <div className="flex flex-wrap gap-2">
        <input aria-label="Response message" className={`${input} flex-1 min-w-[200px]`} value={msg} onChange={(e) => setMsg(e.target.value)} placeholder="Response" />
        <select aria-label="New status" className={input} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="in_review">In review</option><option value="resolved">Resolved</option><option value="rejected">Rejected</option>
        </select>
        <button disabled={!msg.trim()} onClick={send} className="bg-sky-600 text-white text-sm px-3 rounded-lg disabled:opacity-40">Respond</button>
      </div>
      {err && <div role="alert" className="text-xs text-red-600">{err}</div>}
    </div>
  )
}

// Admin: retention policy configuration, documented exceptions, deletion job
// audit, account-deletion requests and privacy grievances.
export default function PrivacyAdmin() {
  const [policies, setPolicies] = useState([])
  const [exceptions, setExceptions] = useState([])
  const [jobs, setJobs] = useState(null)
  const [requests, setRequests] = useState([])
  const [grievances, setGrievances] = useState([])
  const [openG, setOpenG] = useState(null)
  const [ex, setEx] = useState({ category: 'location_history', reason: '', basis: '', review_days: 90, tourist_id: '' })
  const [msg, setMsg] = useState(null)

  const load = useCallback(async () => {
    try {
      const [p, e, j, r, g] = await Promise.all([adminPolicies(), adminExceptions(), adminJobs(), adminDeletionRequests(), adminGrievances()])
      setPolicies(p); setExceptions(e); setJobs(j); setRequests(r); setGrievances(g)
    } catch (e) { setMsg({ ok: false, text: errorText(e) }) }
  }, [])
  useEffect(() => { load() }, [load])

  const addException = async (e) => {
    e.preventDefault()
    try {
      await adminAddException({ ...ex, review_days: Number(ex.review_days), tourist_id: ex.tourist_id ? Number(ex.tourist_id) : null })
      setEx({ ...ex, reason: '', basis: '', tourist_id: '' })
      setMsg({ ok: true, text: 'Exception recorded.' })
      load()
    } catch (err) { setMsg({ ok: false, text: errorText(err) }) }
  }
  const run = async () => {
    try { const r = await adminRunRetention(); setMsg({ ok: true, text: `Ran ${r.jobs.length} job(s).` }); load() }
    catch (e) { setMsg({ ok: false, text: errorText(e) }) }
  }
  const processDeletions = async () => {
    try { const r = await adminProcessDeletions(); setMsg({ ok: true, text: `Processed ${r.processed.length} deletion request(s).` }); load() }
    catch (e) { setMsg({ ok: false, text: errorText(e) }) }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">Privacy &amp; Data Retention</h1>
      <p className="text-xs text-slate-600 dark:text-slate-300">Retention periods and legal bases are configuration. Nothing here is a statement of statutory compliance; set each period only after legal review. A category with no period is never deleted automatically.</p>
      {msg && <div role="status" className={`text-sm ${msg.ok ? 'text-green-700' : 'text-red-600'}`}>{msg.text}</div>}

      <section className={card} aria-label="Retention policies">
        <h2 className="font-semibold mb-2">Retention policies</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm"><thead className="text-left text-xs text-slate-500"><tr><th>Category</th><th>Days</th><th>Action</th><th>Auto-delete</th><th>Legal review</th><th /></tr></thead>
            <tbody>{policies.map((p) => <PolicyRow key={`${p.category}-${p.updated_at}`} p={p} onSaved={load} onError={(t) => setMsg({ ok: false, text: t })} />)}</tbody></table>
        </div>
      </section>

      <section className={card} aria-label="Retention exceptions">
        <h2 className="font-semibold mb-2">Documented exceptions (legal holds)</h2>
        <ul className="text-sm space-y-1 mb-3">
          {exceptions.length === 0 && <li className="text-slate-500">None.</li>}
          {exceptions.map((e) => (
            <li key={e.id} className="flex justify-between gap-2 border-t border-slate-100 dark:border-slate-700 pt-1">
              <span><b>{e.category}</b>{e.tourist_id ? ` · tourist ${e.tourist_id}` : ' · whole category'} - {e.reason} <span className="text-xs text-slate-500">({e.basis}; review by {fmt(e.review_by)}){e.review_overdue && ' - REVIEW OVERDUE'}{!e.active && ' - released'}</span></span>
              {e.active && <button onClick={() => adminReleaseException(e.id).then(load)} className="text-xs font-semibold text-sky-600">Release</button>}
            </li>
          ))}
        </ul>
        <form onSubmit={addException} className="grid grid-cols-1 md:grid-cols-6 gap-2">
          <select aria-label="Exception category" className={input} value={ex.category} onChange={(e) => setEx({ ...ex, category: e.target.value })}>
            {policies.map((p) => <option key={p.category} value={p.category}>{p.category}</option>)}
          </select>
          <input aria-label="Exception reason" className={`${input} md:col-span-2`} placeholder="Reason" value={ex.reason} onChange={(e) => setEx({ ...ex, reason: e.target.value })} />
          <input aria-label="Exception basis" className={input} placeholder="Retention basis" value={ex.basis} onChange={(e) => setEx({ ...ex, basis: e.target.value })} />
          <input aria-label="Tourist id (optional)" className={input} placeholder="Tourist id (optional)" value={ex.tourist_id} onChange={(e) => setEx({ ...ex, tourist_id: e.target.value })} />
          <button type="submit" className="bg-sky-600 text-white rounded-lg text-sm">Add exception</button>
        </form>
      </section>

      <section className={card} aria-label="Deletion jobs">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold">Deletion jobs</h2>
          <button onClick={run} className="text-xs font-semibold bg-slate-100 dark:bg-slate-700 px-3 py-1.5 rounded-lg">Run retention now</button>
        </div>
        {jobs && <p className="text-xs text-slate-500 mb-2">{jobs.failed_jobs} failed · {jobs.active_exceptions} active exception(s) · {jobs.exceptions_overdue_for_review} overdue for review · {jobs.policies_needing_review} polic(ies) need review</p>}
        <div className="overflow-x-auto"><table className="w-full text-xs"><thead className="text-left text-slate-500"><tr><th>Job</th><th>Category</th><th>When</th><th>Status</th><th>Processed</th><th>Deleted</th><th>Retained</th></tr></thead>
          <tbody>{(jobs?.recent_jobs || []).map((j) => (
            <tr key={j.job_id} className="border-t border-slate-100 dark:border-slate-700"><td className="font-mono">{j.job_id}</td><td>{j.category}</td><td>{fmt(j.started_at)}</td>
              <td className={j.status === 'failed' ? 'text-red-600' : ''}>{j.status}{j.failure_reason && ` - ${j.failure_reason}`}</td><td>{j.records_processed}</td><td>{j.records_deleted}</td><td>{j.records_retained}</td></tr>))}</tbody></table></div>
      </section>

      <section className={card} aria-label="Account deletion requests">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold">Account deletion requests</h2>
          <button onClick={processDeletions} className="text-xs font-semibold bg-slate-100 dark:bg-slate-700 px-3 py-1.5 rounded-lg">Process due now</button>
        </div>
        <ul className="text-sm space-y-1">
          {requests.length === 0 && <li className="text-slate-500">None.</li>}
          {requests.map((r) => <li key={r.request_id}><span className="font-mono text-xs">{r.request_id}</span> · <b>{r.label}</b> · attempts {r.attempts}{r.retained.length > 0 && ` · retained: ${r.retained.map((x) => x.category).join(', ')}`}{r.failure_reason && <span className="text-red-600"> · {r.failure_reason}</span>}</li>)}
        </ul>
      </section>

      <section className={card} aria-label="Privacy grievances">
        <h2 className="font-semibold mb-2">Grievances</h2>
        <ul className="text-sm space-y-1">
          {grievances.length === 0 && <li className="text-slate-500">None.</li>}
          {grievances.map((g) => (
            <li key={g.grievance_id}>
              <button onClick={() => setOpenG(openG === g.grievance_id ? null : g.grievance_id)} className="text-left w-full flex justify-between gap-2">
                <span><span className="font-mono text-xs">{g.grievance_id}</span> · {g.category} · {fmt(g.submitted_at)}</span>
                <b className={g.overdue ? 'text-orange-600' : ''}>{g.status}{g.overdue && ' (overdue)'}</b>
              </button>
              {openG === g.grievance_id && <GrievanceDetail gid={g.grievance_id} onClose={() => setOpenG(null)} onChanged={load} />}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
