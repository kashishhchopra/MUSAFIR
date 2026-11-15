import { useEffect, useRef, useState } from 'react'
import {
  DELETION_LABELS, TERMINAL_DELETION, cancelDeletion, confirmDeletion, downloadExport, errorText,
  getDeletionPreview, getDeletionStatus, listExports, requestExport, startDeletion,
} from '../../lib/privacyService'

const INPUT = 'w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-3 py-2 text-sm'
const fmt = (d) => (d ? new Date(d).toLocaleString() : '-')

function DeletionStatus({ status, onSignOut }) {
  const tone = status.status === 'failed' ? 'border-red-400 bg-red-50 dark:bg-red-900/20'
    : status.status === 'completed' ? 'border-green-400 bg-green-50 dark:bg-green-900/20'
      : 'border-orange-300 bg-orange-50 dark:bg-orange-900/20'
  return (
    <div className={`border-2 rounded-xl p-3 text-sm ${tone}`} role="status" data-testid="deletion-status">
      <div className="font-bold">{DELETION_LABELS[status.status] || status.status}</div>
      <div className="text-xs mt-1">Reference <span className="font-mono">{status.request_id}</span> - keep this to check progress.</div>
      {status.failure_reason && <div className="text-xs mt-1 text-red-700">Reason: {status.failure_reason}. It will be retried automatically; contact support if it stays like this.</div>}
      {status.retained?.length > 0 && (
        <div className="mt-2 text-xs">
          <b>Kept for now (access limited to administrators):</b>
          <ul className="list-disc ml-4">
            {status.retained.map((r) => <li key={r.category}>{r.item} ({r.count}) - {r.reason}</li>)}
          </ul>
          <p className="mt-1">Each one is removed as soon as the reason ends.</p>
        </div>
      )}
      {status.external_processors?.length > 0 && (
        <p className="text-xs mt-2">Outside services told to erase: {status.external_processors.map((p) => `${p.name} (${p.status.replace(/_/g, ' ')})`).join(', ')}.</p>
      )}
      <p className="text-[11px] mt-2 text-slate-600 dark:text-slate-300">{status.backups}</p>
      {TERMINAL_DELETION.includes(status.status) && onSignOut && (
        <button onClick={onSignOut} className="mt-2 text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-800 text-white">Sign out</button>
      )}
    </div>
  )
}

// "My data": what we hold, Download My Data, and Delete My Account.
export default function DataPanel({ touristId, deletion, onChanged, onSignOut }) {
  const [format, setFormat] = useState('json')
  const [pw, setPw] = useState('')
  const [exports, setExports] = useState([])
  const [exMsg, setExMsg] = useState(null)
  const [busy, setBusy] = useState('')
  const [held, setHeld] = useState(null)

  const [del, setDel] = useState({ step: deletion?.status === 'awaiting_confirmation' ? 'confirm' : 'idle', req: deletion || null })
  const [preview, setPreview] = useState(null)
  const [delPw, setDelPw] = useState('')
  const [phrase, setPhrase] = useState('')
  const [delMsg, setDelMsg] = useState('')
  const timer = useRef(null)

  const loadExports = () => listExports(touristId).then(setExports).catch(() => {})
  useEffect(() => { loadExports() }, [touristId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => clearTimeout(timer.current), [])

  const viewData = async () => {
    try { setHeld(await getDeletionPreview(touristId)) } catch (e) { setExMsg({ ok: false, text: errorText(e) }) }
  }

  const makeExport = async (e) => {
    e.preventDefault()
    setBusy('export')
    setExMsg(null)
    try {
      const r = await requestExport(touristId, { format, password: pw })
      setPw('')
      setExMsg({ ok: true, text: `Export ${r.export_id} is ${r.status}. The link expires ${fmt(r.expires_at)}.` })
      loadExports()
    } catch (err) { setExMsg({ ok: false, text: errorText(err) }) } finally { setBusy('') }
  }
  const download = async (id) => {
    try { await downloadExport(touristId, id) } catch (err) {
      setExMsg({ ok: false, text: err?.response?.status === 410 ? 'This export has expired. Request a new one.' : 'Download failed.' })
      loadExports()
    }
  }

  const poll = (id, tries = 0) => {
    timer.current = setTimeout(async () => {
      try {
        const s = await getDeletionStatus(id)
        setDel((d) => ({ ...d, req: s }))
        if (!TERMINAL_DELETION.includes(s.status) && tries < 15) poll(id, tries + 1)
      } catch { /* keep the last known status */ }
    }, 2500)
  }

  const openDelete = async () => {
    setDelMsg('')
    try { setPreview(await getDeletionPreview(touristId)); setDel({ step: 'review', req: null }) }
    catch (e) { setDelMsg(errorText(e)) }
  }
  const start = async (e) => {
    e.preventDefault()
    setBusy('del')
    setDelMsg('')
    try {
      const req = await startDeletion(touristId, delPw)
      setDel({ step: 'confirm', req })
    } catch (err) { setDelMsg(errorText(err)) } finally { setBusy('') }
  }
  const confirm = async (e) => {
    e.preventDefault()
    setBusy('del')
    setDelMsg('')
    try {
      const req = await confirmDeletion(touristId, del.req.request_id, { password: delPw, confirmation: phrase })
      setDel({ step: 'done', req })
      setDelPw('')
      setPhrase('')
      poll(req.request_id)
    } catch (err) { setDelMsg(errorText(err)) } finally { setBusy('') }
  }
  const cancel = async () => {
    try { await cancelDeletion(touristId, del.req.request_id); setDel({ step: 'idle', req: null }); onChanged?.() }
    catch (e) { setDelMsg(errorText(e)) }
  }

  return (
    <div className="space-y-5">
      <section aria-label="View my data" className="space-y-2">
        <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">View my data</h4>
        <button onClick={viewData} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700">Show what MUSAFIR holds about me</button>
        {held && (
          <ul className="text-xs space-y-0.5" aria-label="Data held">
            {[...held.will_be_erased, ...held.will_be_retained.map((r) => ({ ...r, result: 'kept under an exception' }))].map((i, n) => (
              <li key={n} className="flex justify-between gap-2"><span>{i.item}</span><span className="text-slate-500">{i.count}</span></li>
            ))}
            <li className="text-slate-500 pt-1">Plus your profile, ID document, photo and login. Download a full copy below.</li>
          </ul>
        )}
      </section>

      <section aria-label="Download my data" className="space-y-2">
        <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-100">Download my data</h4>
        <p className="text-xs text-slate-500 dark:text-slate-400">A private copy of your own data. It is prepared on the server, expires, and is never e-mailed.</p>
        <form onSubmit={makeExport} className="space-y-2">
          <div className="flex gap-2">
            <select aria-label="Export format" value={format} onChange={(e) => setFormat(e.target.value)} className={`${INPUT} w-28`}>
              <option value="json">JSON</option>
              <option value="csv">CSV (zip)</option>
            </select>
            <input type="password" aria-label="Password to confirm export" placeholder="Your password" autoComplete="current-password"
              value={pw} onChange={(e) => setPw(e.target.value)} className={INPUT} />
          </div>
          <button type="submit" disabled={!pw || busy === 'export'}
            className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-sky-600 text-white disabled:opacity-40">
            {busy === 'export' ? 'Preparing…' : 'Download My Data'}
          </button>
        </form>
        {exMsg && <div role="status" className={`text-xs ${exMsg.ok ? 'text-green-700 dark:text-green-300' : 'text-red-600'}`}>{exMsg.text}</div>}
        <ul className="text-xs space-y-1" aria-label="My exports">
          {exports.map((x) => (
            <li key={x.export_id} className="flex items-center justify-between gap-2 border border-slate-200 dark:border-slate-700 rounded-lg px-2 py-1.5">
              <span><span className="font-mono">{x.export_id}</span> · {x.format.toUpperCase()} · <b>{x.status}</b>{x.status === 'ready' && <> · expires {fmt(x.expires_at)}</>}</span>
              {x.status === 'ready' && <button onClick={() => download(x.export_id)} className="font-semibold text-sky-600">Download</button>}
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Delete my account" className="space-y-2 border-t border-slate-200 dark:border-slate-700 pt-4">
        <h4 className="text-sm font-semibold text-red-700 dark:text-red-400">Delete my account</h4>
        {del.step === 'idle' && (
          <>
            <p className="text-xs text-slate-500 dark:text-slate-400">Erase your account and personal data. You will see exactly what is removed and what must be kept before anything happens.</p>
            <button onClick={openDelete} className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-red-300 text-red-700">Delete My Account</button>
          </>
        )}
        {del.step === 'review' && preview && (
          <form onSubmit={start} className="space-y-2">
            <div className="text-xs border border-red-200 rounded-lg p-2">
              <b>Will be erased or de-identified</b>
              <ul className="list-disc ml-4">{preview.will_be_erased.map((i, n) => <li key={n}>{i.item} ({i.count}) - {i.result}</li>)}
                {preview.also.map((a) => <li key={a}>{a}</li>)}</ul>
              {preview.will_be_retained.length > 0 && (<>
                <b className="block mt-2">Must be kept for now (administrators only)</b>
                <ul className="list-disc ml-4">{preview.will_be_retained.map((i, n) => <li key={n}>{i.item} ({i.count}) - {i.reason}</li>)}</ul>
              </>)}
              <p className="mt-2 text-slate-600 dark:text-slate-300">{preview.backups}</p>
              {preview.external_processors.length > 0 && <p className="mt-1">Outside services that also hold data: {preview.external_processors.map((p) => p.name).join(', ')}.</p>}
            </div>
            <input type="password" aria-label="Password to start deletion" placeholder="Your password" autoComplete="current-password"
              value={delPw} onChange={(e) => setDelPw(e.target.value)} className={INPUT} />
            <div className="flex gap-2">
              <button type="submit" disabled={!delPw || busy === 'del'} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-600 text-white disabled:opacity-40">Continue</button>
              <button type="button" onClick={() => setDel({ step: 'idle', req: null })} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700">Not now</button>
            </div>
          </form>
        )}
        {del.step === 'confirm' && del.req && (
          <form onSubmit={confirm} className="space-y-2">
            <DeletionStatus status={del.req} />
            <p className="text-xs">Final step. This signs you out everywhere and cannot be undone once it starts.</p>
            <input type="password" aria-label="Password to confirm deletion" placeholder="Your password" autoComplete="current-password"
              value={delPw} onChange={(e) => setDelPw(e.target.value)} className={INPUT} />
            <input aria-label="Type DELETE to confirm" placeholder="Type DELETE to confirm" value={phrase}
              onChange={(e) => setPhrase(e.target.value)} className={INPUT} autoComplete="off" />
            <div className="flex gap-2">
              <button type="submit" disabled={!delPw || phrase.trim() !== 'DELETE' || busy === 'del'}
                className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-600 text-white disabled:opacity-40">Permanently delete my account</button>
              <button type="button" onClick={cancel} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700">Cancel request</button>
            </div>
          </form>
        )}
        {del.step === 'done' && del.req && <DeletionStatus status={del.req} onSignOut={onSignOut} />}
        {del.step === 'idle' && deletion && !['cancelled', 'awaiting_confirmation'].includes(deletion.status) && <DeletionStatus status={deletion} onSignOut={onSignOut} />}
        {delMsg && <div role="alert" className="text-xs text-red-600">{delMsg}</div>}
      </section>
    </div>
  )
}
