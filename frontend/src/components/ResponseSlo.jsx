import { useCallback, useEffect, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { Card, Stat } from './ui.jsx'
import { errorText, formatDuration, getSlo, listUnits } from '../lib/responseService'

// Same palettes as the existing Analytics page: severity is a state colour, not a series.
const STATUS = { low: '#0ca30c', medium: '#fab219', high: '#ec835a', critical: '#d03b3b' }
const SERIES = ['#2a78d6', '#e34948']
const INK = { grid: '#e1e0d9', axis: '#898781' }
const SEVERITIES = ['critical', 'high', 'medium', 'low']

const pct = (v) => (v == null ? '—' : `${v}%`)
const dur = (v) => (v == null ? '—' : formatDuration(v))
const dayAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10)

export default function ResponseSlo() {
  const [filters, setFilters] = useState({ from: dayAgo(30), to: dayAgo(0), severity: '', incident_type: '', unit_id: '' })
  const [data, setData] = useState(null)
  const [units, setUnits] = useState([])
  const [types, setTypes] = useState([])
  const [error, setError] = useState(null)

  const load = useCallback(async (f) => {
    const params = {}
    // whole-day bounds, sent as UTC: the API stores and reports UTC
    if (f.from) params.from = `${f.from}T00:00:00Z`
    if (f.to) params.to = `${f.to}T23:59:59Z`
    if (f.severity) params.severity = f.severity
    if (f.incident_type) params.incident_type = f.incident_type
    if (f.unit_id) params.unit_id = f.unit_id
    try {
      const d = await getSlo(params)
      setData(d)
      setError(null)
      if (!f.incident_type) setTypes(d.by_type.map((t) => t.type))
    } catch (e) { setError(errorText(e, 'Could not load SLO data')) }
  }, [])

  useEffect(() => { load(filters) }, [filters, load])
  useEffect(() => { listUnits().then(setUnits).catch(() => {}) }, [])

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }))
  const o = data?.overall
  const sevRows = SEVERITIES.map((s) => ({
    severity: s, ...(data?.by_severity?.[s] || {}),
    compliance: data?.by_severity?.[s]?.compliance_pct ?? 0,
    avg: data?.by_severity?.[s]?.avg_ack_seconds ?? 0,
  }))
  const axis = { tick: { fill: INK.axis, fontSize: 12 }, tickLine: false }

  return (
    <div className="space-y-4" data-testid="slo-panel">
      <Card title="Filters">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
          <label>From<input type="date" value={filters.from} onChange={set('from')} aria-label="From date"
            className="block w-full border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600" /></label>
          <label>To<input type="date" value={filters.to} onChange={set('to')} aria-label="To date"
            className="block w-full border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600" /></label>
          <label>Severity
            <select value={filters.severity} onChange={set('severity')} aria-label="Severity filter"
              className="block w-full border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600">
              <option value="">All</option>{SEVERITIES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select></label>
          <label>Incident type
            <select value={filters.incident_type} onChange={set('incident_type')} aria-label="Type filter"
              className="block w-full border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600">
              <option value="">All</option>{types.map((t) => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
            </select></label>
          <label>Response team
            <select value={filters.unit_id} onChange={set('unit_id')} aria-label="Team filter"
              className="block w-full border rounded-lg px-2 py-1 dark:bg-slate-900 dark:border-slate-600">
              <option value="">All</option>{units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select></label>
        </div>
        {error && <div role="alert" className="mt-2 text-sm text-red-600">{error}</div>}
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3" data-testid="slo-stats">
        <Stat label="Response SLO" value={pct(o?.compliance_pct)} accent="text-sky-600" />
        {SEVERITIES.map((s) => (
          <Stat key={s} label={`${s} SLO`} value={pct(data?.by_severity?.[s]?.compliance_pct)}
            accent={s === 'critical' ? 'text-red-600' : s === 'high' ? 'text-orange-600' : undefined} />
        ))}
        <Stat label="Avg response" value={dur(o?.avg_ack_seconds)} />
        <Stat label="Median response" value={dur(o?.median_ack_seconds)} />
        <Stat label="Active incidents" value={data?.active_incidents ?? '—'} />
        <Stat label="Overdue now" value={data?.active_overdue ?? '—'} accent={data?.active_overdue ? 'text-red-600' : undefined} />
        <Stat label="Escalated" value={o?.escalated ?? '—'} />
        <Stat label="Global timeouts" value={o?.global_timeouts ?? '—'} accent={o?.global_timeouts ? 'text-red-600' : undefined} />
        <Stat label="Received" value={o?.received ?? '—'} />
        <Stat label="Acknowledged" value={o?.acknowledged ?? '—'} />
        <Stat label="Within target" value={o?.within_target ?? '—'} />
        <Stat label="After target" value={o?.after_target ?? '—'} />
        <Stat label="Unacknowledged" value={o?.unacknowledged ?? '—'} />
      </div>
      <p className="text-xs text-slate-500" data-testid="slo-method">
        SLO = incidents acknowledged within their response target ÷ acknowledged incidents in the period. Unacknowledged
        incidents are never counted as successes; they are listed separately above. Targets are illustrative demonstration
        values, not emergency-service standards.
      </p>

      <div className="grid md:grid-cols-2 gap-4">
        <Card title="SLO compliance by severity (%)">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={sevRows} margin={{ top: 10, right: 16, bottom: 0, left: -16 }}>
              <CartesianGrid stroke={INK.grid} vertical={false} />
              <XAxis dataKey="severity" {...axis} /><YAxis domain={[0, 100]} {...axis} axisLine={false} />
              <Tooltip formatter={(v) => `${v}%`} />
              <Bar dataKey="compliance" name="Compliance %" radius={[4, 4, 0, 0]}>
                {sevRows.map((r) => <Cell key={r.severity} fill={STATUS[r.severity]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Average acknowledgment time by severity (s)">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={sevRows} margin={{ top: 10, right: 16, bottom: 0, left: -16 }}>
              <CartesianGrid stroke={INK.grid} vertical={false} />
              <XAxis dataKey="severity" {...axis} /><YAxis {...axis} axisLine={false} />
              <Tooltip formatter={(v) => `${v}s`} />
              <Bar dataKey="avg" name="Average (s)" radius={[4, 4, 0, 0]}>
                {sevRows.map((r) => <Cell key={r.severity} fill={STATUS[r.severity]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Response-time trend">
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={data?.trend || []} margin={{ top: 10, right: 16, bottom: 0, left: -16 }}>
              <CartesianGrid stroke={INK.grid} vertical={false} />
              <XAxis dataKey="date" {...axis} /><YAxis {...axis} axisLine={false} />
              <Tooltip /><Legend />
              <Line type="monotone" dataKey="avg_ack_seconds" name="Avg ack (s)" stroke={SERIES[0]} strokeWidth={2} dot={{ r: 3 }} />
              <Line type="monotone" dataKey="received" name="Incidents" stroke={SERIES[1]} strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        </Card>
        <Card title="Escalations over time">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={data?.trend || []} margin={{ top: 10, right: 16, bottom: 0, left: -16 }}>
              <CartesianGrid stroke={INK.grid} vertical={false} />
              <XAxis dataKey="date" {...axis} /><YAxis allowDecimals={false} {...axis} axisLine={false} />
              <Tooltip />
              <Bar dataKey="escalations" name="Stage escalations" fill={SERIES[1]} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
        <div className="md:col-span-2">
          <Card title="Acknowledgment-time distribution">
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={data?.distribution || []} margin={{ top: 10, right: 16, bottom: 0, left: -16 }}>
                <CartesianGrid stroke={INK.grid} vertical={false} />
                <XAxis dataKey="label" {...axis} /><YAxis allowDecimals={false} {...axis} axisLine={false} />
                <Tooltip />
                <Bar dataKey="count" name="Incidents" fill={SERIES[0]} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </Card>
        </div>
      </div>

      {data?.by_type?.length > 0 && (
        <Card title="SLO by incident type">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="slo-by-type">
              <thead><tr className="text-left text-xs text-slate-500">
                <th className="py-1 pr-3">Type</th><th className="pr-3">Received</th><th className="pr-3">Acked</th>
                <th className="pr-3">SLO</th><th>Avg</th></tr></thead>
              <tbody>
                {data.by_type.map((t) => (
                  <tr key={t.type} className="border-t border-slate-100 dark:border-slate-700">
                    <td className="py-1 pr-3 capitalize">{t.type.replace('_', ' ')}</td>
                    <td className="pr-3">{t.received}</td><td className="pr-3">{t.acknowledged}</td>
                    <td className="pr-3">{pct(t.compliance_pct)}</td><td>{dur(t.avg_ack_seconds)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
