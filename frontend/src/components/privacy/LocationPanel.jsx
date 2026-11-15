import { useCallback, useEffect, useState } from 'react'
import { deleteLocationHistory, errorText, getLocationHistory } from '../../lib/privacyService'

const PAGE = 25
const fmt = (d) => (d ? new Date(d).toLocaleString() : '-')

// Location Privacy: see your stored GPS history, delete picked rows, a date
// range, or everything. Deleting history never switches SOS off; an emergency
// that is still open keeps the part of the trail it needs (the server says so).
export default function LocationPanel({ touristId, onChanged }) {
  const [data, setData] = useState(null)
  const [offset, setOffset] = useState(0)
  const [range, setRange] = useState({ from: '', to: '' })
  const [picked, setPicked] = useState([])
  const [confirm, setConfirm] = useState(null) // { label, body }
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)

  const load = useCallback(async (off = 0, r = range) => {
    try {
      const params = { limit: PAGE, offset: off }
      if (r.from) params.date_from = new Date(r.from).toISOString()
      if (r.to) params.date_to = new Date(r.to).toISOString()
      setData(await getLocationHistory(touristId, params))
      setOffset(off)
      setPicked([])
    } catch (e) { setMsg({ ok: false, text: errorText(e) }) }
  }, [touristId, range])
  useEffect(() => { load(0) }, [touristId]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async () => {
    setBusy(true)
    try {
      const res = await deleteLocationHistory(touristId, confirm.body)
      setMsg({
        ok: true,
        text: `${res.deleted} location record(s) deleted.` + (res.retained ? ` ${res.retained} kept: ${res.retained_reason}` : ''),
      })
      setConfirm(null)
      await load(0)
      onChanged?.()
    } catch (e) { setMsg({ ok: false, text: errorText(e) }) } finally { setBusy(false) }
  }

  const ask = (label, body) => setConfirm({ label, body })
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))
  const rangeBody = () => {
    const b = {}
    if (range.from) b.date_from = new Date(range.from).toISOString()
    if (range.to) b.date_to = new Date(range.to).toISOString()
    return b
  }

  if (!data) return <p className="text-xs text-slate-500">Loading…</p>
  return (
    <div className="space-y-3">
      <div className="text-xs text-slate-600 dark:text-slate-300">
        <b>{data.total}</b> location record(s) stored
        {data.available_from && <> · from {fmt(data.available_from)} to {fmt(data.available_to)}</>}.
        {' '}Live tracking is <b>{data.tracking_enabled ? 'on' : 'off'}</b> - change it under Consents.
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="text-xs">From
          <input type="datetime-local" aria-label="From date" value={range.from}
            onChange={(e) => setRange({ ...range, from: e.target.value })}
            className="mt-1 w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2 py-1.5 text-sm" />
        </label>
        <label className="text-xs">To
          <input type="datetime-local" aria-label="To date" value={range.to}
            onChange={(e) => setRange({ ...range, to: e.target.value })}
            className="mt-1 w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2 py-1.5 text-sm" />
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <button onClick={() => load(0)} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700">Filter</button>
        <button disabled={!range.from && !range.to} onClick={() => ask('all records in this date range', rangeBody())}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-red-300 text-red-700 disabled:opacity-40">Delete date range</button>
        <button disabled={picked.length === 0} onClick={() => ask(`${picked.length} selected record(s)`, { ids: picked })}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-red-300 text-red-700 disabled:opacity-40">Delete selected ({picked.length})</button>
        <button disabled={data.total === 0} onClick={() => ask('your entire location history', { delete_all: true })}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-600 text-white disabled:opacity-40">Delete all</button>
      </div>

      {confirm && (
        <div role="alertdialog" aria-label="Confirm location deletion" className="border-2 border-red-300 rounded-xl p-3 bg-red-50 dark:bg-red-900/20 text-sm">
          <p className="font-semibold text-red-800 dark:text-red-200">Are you sure you want to delete {confirm.label}?</p>
          <p className="text-xs text-red-700 dark:text-red-300 mt-1">
            This will remove the selected stored location records and cannot be undone. SOS keeps working.
            Anything tied to an emergency that is still open is kept.
          </p>
          <div className="flex gap-2 mt-2">
            <button disabled={busy} onClick={run} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-red-600 text-white disabled:opacity-50">Yes, delete</button>
            <button onClick={() => setConfirm(null)} className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-slate-700">Cancel</button>
          </div>
        </div>
      )}
      {msg && <div role="status" className={`text-xs ${msg.ok ? 'text-green-700 dark:text-green-300' : 'text-red-600'}`}>{msg.text}</div>}

      <ul className="divide-y divide-slate-100 dark:divide-slate-700 max-h-64 overflow-y-auto text-xs" aria-label="Location history">
        {data.items.length === 0 && <li className="py-3 text-slate-500">No location records.</li>}
        {data.items.map((p) => (
          <li key={p.id} className="py-1.5 flex items-center gap-2">
            <input type="checkbox" aria-label={`Select record ${p.id}`} checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
            <span className="flex-1 min-w-0">{fmt(p.timestamp)}</span>
            <span className="font-mono text-slate-500">{p.lat.toFixed(4)}, {p.lng.toFixed(4)}</span>
          </li>
        ))}
      </ul>
      <div className="flex items-center justify-between text-xs">
        <button disabled={offset === 0} onClick={() => load(Math.max(0, offset - PAGE))} className="font-semibold text-sky-600 disabled:opacity-40">← Newer</button>
        <span>{data.total ? `${offset + 1}-${Math.min(offset + PAGE, data.total)} of ${data.total}` : ''}</span>
        <button disabled={offset + PAGE >= data.total} onClick={() => load(offset + PAGE)} className="font-semibold text-sky-600 disabled:opacity-40">Older →</button>
      </div>
    </div>
  )
}
