import { useEffect, useState } from 'react'
import { errorText, getGrievance, listGrievances, replyGrievance, submitGrievance } from '../../lib/privacyService'

const CATEGORIES = [
  ['access_or_export', 'My data / a copy of it'], ['correction', 'Something is wrong in my data'],
  ['erasure', 'Deleting my data'], ['consent', 'Consent or withdrawal'],
  ['location', 'Location tracking'], ['security', 'Security of my data'], ['other', 'Something else'],
]
const STATUS = { open: 'Open', in_review: 'In review', resolved: 'Resolved', rejected: 'Closed - not upheld' }
const fmt = (d) => (d ? new Date(d).toLocaleString() : '-')
const INPUT = 'w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-3 py-2 text-sm'

// Privacy support: raise a concern, follow its status and the replies.
export default function GrievancePanel({ touristId, contactEmail, responseDays }) {
  const [items, setItems] = useState([])
  const [open, setOpen] = useState(null)
  const [form, setForm] = useState({ category: 'other', description: '' })
  const [reply, setReply] = useState('')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = () => listGrievances(touristId).then(setItems).catch(() => {})
  useEffect(() => { load() }, [touristId]) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const g = await submitGrievance(touristId, form)
      setMsg({ ok: true, text: `Submitted. Your reference is ${g.grievance_id}.` })
      setForm({ category: 'other', description: '' })
      load()
    } catch (err) { setMsg({ ok: false, text: errorText(err) }) } finally { setBusy(false) }
  }
  const show = async (id) => {
    if (open?.grievance_id === id) return setOpen(null)
    try { setOpen(await getGrievance(touristId, id)) } catch (e) { setMsg({ ok: false, text: errorText(e) }) }
  }
  const send = async () => {
    try { setOpen(await replyGrievance(touristId, open.grievance_id, reply)); setReply('') }
    catch (e) { setMsg({ ok: false, text: errorText(e) }) }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Questions or complaints about your personal data? Tell us here.
        {responseDays ? <> We aim to answer within {responseDays} days.</> : null}
        {contactEmail && <> You can also write to <a className="text-sky-600" href={`mailto:${contactEmail}`}>{contactEmail}</a>.</>}
      </p>
      <form onSubmit={submit} className="space-y-2">
        <select aria-label="Grievance category" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className={INPUT}>
          {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <textarea aria-label="Describe your concern" rows={3} placeholder="Describe your concern" value={form.description}
          onChange={(e) => setForm({ ...form, description: e.target.value })} className={INPUT} maxLength={4000} />
        <button type="submit" disabled={busy || form.description.trim().length < 10}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-sky-600 text-white disabled:opacity-40">Submit concern</button>
      </form>
      {msg && <div role="status" className={`text-xs ${msg.ok ? 'text-green-700 dark:text-green-300' : 'text-red-600'}`}>{msg.text}</div>}

      <ul className="space-y-2" aria-label="My grievances">
        {items.map((g) => (
          <li key={g.grievance_id} className="border border-slate-200 dark:border-slate-700 rounded-xl p-2 text-xs">
            <button onClick={() => show(g.grievance_id)} className="w-full text-left flex justify-between gap-2">
              <span><span className="font-mono">{g.grievance_id}</span> · {g.category.replace(/_/g, ' ')}</span>
              <b>{STATUS[g.status] || g.status}</b>
            </button>
            <div className="text-slate-500">Submitted {fmt(g.submitted_at)}{g.resolved_at && ` · closed ${fmt(g.resolved_at)}`}{g.overdue && <span className="text-orange-600"> · past target</span>}</div>
            {open?.grievance_id === g.grievance_id && (
              <div className="mt-2 space-y-1">
                <p className="whitespace-pre-wrap">{open.description}</p>
                {open.responses.map((r, i) => (
                  <div key={i} className={`rounded-lg p-2 ${r.author_role === 'admin' ? 'bg-sky-50 dark:bg-sky-900/20' : 'bg-slate-50 dark:bg-slate-700/40'}`}>
                    <b>{r.author_role === 'admin' ? 'MUSAFIR privacy team' : 'You'}</b> · {fmt(r.created_at)}
                    <p className="whitespace-pre-wrap">{r.message}</p>
                  </div>
                ))}
                {!['resolved', 'rejected'].includes(open.status) && (
                  <div className="flex gap-2 pt-1">
                    <input aria-label="Reply" value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Add a message" className={INPUT} />
                    <button disabled={!reply.trim()} onClick={send} className="font-semibold text-sky-600 disabled:opacity-40">Send</button>
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
        {items.length === 0 && <li className="text-xs text-slate-500">You have not raised any concerns.</li>}
      </ul>
    </div>
  )
}
