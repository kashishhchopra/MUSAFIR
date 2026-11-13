import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { MapContainer, TileLayer, CircleMarker, Marker, Polygon, Polyline, Tooltip, useMap } from 'react-leaflet'
import api from '../../api'
import useWebSocket from '../../useWebSocket'
import { sosIcon, riskColor } from '../../components/mapIcons'
import { DEFAULT_MAP } from '../../config'
import { TILE_ATTRIBUTION, TILE_SUBDOMAINS, TILE_URL } from '../../lib/mapConfig'

/**
 * Simulation Core control room: virtual tourists on a live map, a controllable
 * clock (1x-60x), scripted event triggers, and the live alert / event stream.
 * Everything shown is produced by the real monitoring pipeline -- the sim only
 * moves the (virtual) tourists. See backend app/services/simulation.py.
 */

const STATUS = {
  normal: { label: 'Normal', color: '#22c55e' },
  off_route: { label: 'Off route', color: '#eab308' },
  danger_zone: { label: 'In danger zone', color: '#f97316' },
  inactive: { label: 'Inactive', color: '#94a3b8' },
  anomaly: { label: 'Anomaly', color: '#a855f7' },
  sos: { label: 'SOS', color: '#ef4444' },
}
const SPEEDS = [1, 2, 5, 10, 30, 60]
const SEV = { critical: '#ef4444', high: '#f97316', medium: '#eab308', low: '#22c55e' }
const EVENT_LABEL = {
  danger_zone: ['⛔', 'Enter danger zone'],
  off_route: ['🧭', 'Go off-route'],
  inactivity: ['😴', 'Go inactive'],
  abduction: ['🚗', 'Abduction jump'],
  sos: ['🆘', 'Press SOS'],
  resolve: ['✅', 'Mark safe / resume'],
}

function Chip({ color, children }) {
  return (
    <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold border"
      style={{ color, borderColor: color + '66', background: color + '1a' }}>{children}</span>
  )
}

function Tile({ label, value, sub, color }) {
  return (
    <div className="rounded-xl border border-slate-700/70 bg-slate-900/80 px-4 py-2 min-w-[110px] text-center">
      <div className="text-2xl font-bold leading-tight" style={color ? { color } : undefined}>{value}</div>
      <div className="text-[10px] tracking-wider uppercase text-slate-400">{label}</div>
      {sub ? <div className="text-[10px] text-slate-500">{sub}</div> : null}
    </div>
  )
}

// Fit the map to the tourists once, the first time they appear.
function FitOnce({ points }) {
  const map = useMap()
  const done = useRef(false)
  useEffect(() => {
    if (done.current || points.length < 2) return
    done.current = true
    map.fitBounds(points, { padding: [40, 40], maxZoom: 15 })
  }, [points, map])
  return null
}

function FlyTo({ target }) {
  const map = useMap()
  useEffect(() => {
    if (target) map.flyTo(target, Math.max(map.getZoom(), 15), { duration: 0.6 })
  }, [target, map])
  return null
}

// The API emits naive-UTC ISO strings; treat them as UTC so every clock agrees.
const asUtc = (iso) => (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
const fmtClock = (iso) => {
  if (!iso) return '--:--:--'
  const d = new Date(asUtc(iso))
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}
const fmtElapsed = (s) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return `+${h}h ${String(m).padStart(2, '0')}m`
}

export default function Simulation() {
  const [sim, setSim] = useState(null)               // latest sim_state (without log)
  const [log, setLog] = useState([])
  const [alerts, setAlerts] = useState([])
  const [zones, setZones] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [flyTarget, setFlyTarget] = useState(null)
  const [tab, setTab] = useState('trigger')
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [layers, setLayers] = useState({ tourists: true, zones: true, route: true })
  const [spawnCount, setSpawnCount] = useState(50)
  const [zoneId, setZoneId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const idsRef = useRef(new Set())

  const applyState = useCallback((s) => {
    const { log: l, ...rest } = s
    setSim((prev) => ({ ...(prev || {}), ...rest }))
    if (l) setLog(l)
    idsRef.current = new Set((s.agents || []).map((a) => a.id))
  }, [])

  const refresh = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([api.get('/simulation/state'), api.get('/simulation/alerts?limit=60')])
      applyState(s.data)
      setAlerts(a.data)
      setError('')
    } catch (e) {
      setError(e?.response?.data?.detail || 'Could not reach the simulation service.')
    }
  }, [applyState])

  useEffect(() => {
    refresh()
    api.get('/zones').then((r) => setZones(r.data)).catch(() => {})
  }, [refresh])

  const { connected } = useWebSocket((ev) => {
    if (ev.event === 'sim_state') {
      const { event: _e, ...rest } = ev
      setSim((prev) => ({ ...(prev || {}), ...rest }))
      idsRef.current = new Set((ev.agents || []).map((a) => a.id))
    } else if (ev.event === 'sim_log') {
      setLog((prev) => [...prev, ev].slice(-300))
    } else if (ev.event === 'alert' && idsRef.current.has(ev.tourist_id)) {
      setAlerts((prev) => [{ ...ev, created_at: ev.created_at || new Date().toISOString() }, ...prev].slice(0, 80))
    }
  })

  // Fallback polling if the socket isn't up.
  useEffect(() => {
    if (connected) return undefined
    const iv = setInterval(refresh, 3000)
    return () => clearInterval(iv)
  }, [connected, refresh])

  // Selected tourist details (pings, alerts, route, trail).
  useEffect(() => {
    if (selectedId == null) { setDetail(null); return undefined }
    let live = true
    const load = () => api.get(`/simulation/tourists/${selectedId}`)
      .then((r) => { if (live) setDetail(r.data) }).catch(() => {})
    load()
    const iv = setInterval(load, 3000)
    return () => { live = false; clearInterval(iv) }
  }, [selectedId])

  const act = async (fn) => {
    setBusy(true); setError('')
    try { await fn() } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Request failed')
    } finally { setBusy(false) }
  }
  const post = (path, body) => api.post(`/simulation${path}`, body)

  const agents = useMemo(() => sim?.agents || [], [sim])
  const byId = useMemo(() => Object.fromEntries(agents.map((a) => [a.id, a])), [agents])
  const selected = selectedId != null ? byId[selectedId] : null
  const counts = sim?.counts || {}
  const running = !!sim?.running

  const visible = useMemo(() => agents.filter((a) =>
    (filter === 'all' || a.status === filter) &&
    (!query || `${a.name} ${a.digital_id}`.toLowerCase().includes(query.toLowerCase()))
  ).sort((a, b) => {
    const rank = (x) => ['sos', 'anomaly', 'danger_zone', 'off_route', 'inactive', 'normal'].indexOf(x.status)
    return rank(a) - rank(b) || a.name.localeCompare(b.name)
  }), [agents, filter, query])

  const select = (a) => { setSelectedId(a.id); setFlyTarget([a.lat, a.lng]); setTab('tourist') }
  const fire = (event) => act(async () => {
    await post('/events', {
      event, tourist_id: selectedId ?? null, zone_id: event === 'danger_zone' && zoneId ? Number(zoneId) : null,
    })
  })
  const riskyZones = zones.filter((z) => z.risk_level === 'high' || z.risk_level === 'restricted')
  const criticalAlerts = alerts.filter((a) => a.severity === 'critical' || a.type === 'anomaly' || a.type === 'sos')
  const center = [DEFAULT_MAP?.center?.[0] ?? 26.1445, DEFAULT_MAP?.center?.[1] ?? 91.7362]

  return (
    <div className="-m-4 p-4 min-h-[calc(100vh-56px)] bg-slate-950 text-slate-100 space-y-3">
      {/* ------------------------------------------------ header */}
      <div className="flex flex-wrap items-center gap-3 justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold tracking-tight">Simulation Control Room</h1>
            <Chip color="#38bdf8">SIMULATED DATA</Chip>
          </div>
          <p className="text-sm text-slate-400">
            Virtual tourists → live GPS → ML detection → alert → incident response.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tile label="Tourists" value={counts.total ?? 0} />
          <Tile label="Active" value={counts.active ?? 0} color="#22c55e" sub={`${counts.inactive ?? 0} inactive`} />
          <Tile label="SOS" value={counts.sos ?? 0} color={counts.sos ? '#ef4444' : undefined} />
          <Tile label="At risk" value={(counts.off_route ?? 0) + (counts.danger_zone ?? 0) + (counts.anomaly ?? 0)}
            color="#f97316" />
          <Tile label={`Sim clock · ${sim?.speed ?? 1}x`} value={fmtClock(sim?.sim_time)}
            sub={sim ? fmtElapsed(sim.elapsed_s || 0) : ''} />
          <Chip color={connected ? '#22c55e' : '#eab308'}>{connected ? 'Realtime' : 'Polling'}</Chip>
        </div>
      </div>

      {/* ------------------------------------------------ controls */}
      <div className="rounded-xl border border-slate-700/70 bg-slate-900/70 p-3 flex flex-wrap items-center gap-3">
        <span className="text-[11px] tracking-widest text-slate-400 uppercase">Time control</span>
        <button disabled={busy} onClick={() => act(() => post(running ? '/pause' : '/play').then(refresh))}
          className={`px-4 py-1.5 rounded-lg font-semibold text-sm ${running
            ? 'bg-amber-500 hover:bg-amber-400 text-slate-900' : 'bg-emerald-500 hover:bg-emerald-400 text-slate-900'}`}>
          {running ? '⏸ Pause' : '▶ Play'}
        </button>
        <div className="flex gap-1" role="group" aria-label="Simulation speed">
          {SPEEDS.map((s) => (
            <button key={s} onClick={() => act(() => post('/speed', { speed: s }).then(refresh))}
              aria-pressed={sim?.speed === s}
              className={`px-2.5 py-1.5 rounded-lg text-sm border ${sim?.speed === s
                ? 'bg-sky-500 border-sky-400 text-slate-900 font-bold' : 'border-slate-600 hover:bg-slate-800'}`}>
              {s}x
            </button>
          ))}
        </div>
        <span className="hidden lg:inline w-px h-6 bg-slate-700" />
        <label className="text-xs text-slate-400 flex items-center gap-1">Tourists
          <input type="number" min={1} max={100} value={spawnCount}
            onChange={(e) => setSpawnCount(Math.max(1, Math.min(100, Number(e.target.value) || 1)))}
            className="w-16 px-2 py-1 rounded-lg bg-slate-800 border border-slate-600 text-slate-100" />
        </label>
        <button disabled={busy} onClick={() => act(() => post('/spawn', { count: spawnCount }).then(refresh))}
          className="px-3 py-1.5 rounded-lg text-sm border border-slate-600 hover:bg-slate-800">Spawn</button>
        <button disabled={busy || !agents.length}
          onClick={() => act(() => post('/scenario', { name: 'full_demo' }).then(async () => {
            await post('/speed', { speed: 30 }); await post('/play'); await refresh()
          }))}
          className="px-3 py-1.5 rounded-lg text-sm bg-sky-600 hover:bg-sky-500 font-semibold"
          title="Off-route → danger zone → inactivity → abduction jump → SOS, ~13 simulated minutes">
          🎬 Run full demo scenario
        </button>
        <div className="ml-auto flex gap-2">
          <button disabled={busy} onClick={() => act(() => post('/reset', { purge: false }).then(refresh))}
            className="px-3 py-1.5 rounded-lg text-sm border border-slate-600 hover:bg-slate-800">Reset</button>
          <button disabled={busy}
            onClick={() => { if (window.confirm('Delete all simulated tourists and their alerts/incidents?'))
              act(() => post('/reset', { purge: true }).then(() => { setSelectedId(null); setAlerts([]); return refresh() })) }}
            className="px-3 py-1.5 rounded-lg text-sm border border-red-500/60 text-red-300 hover:bg-red-950">
            Purge data
          </button>
        </div>
      </div>
      {error ? <div role="alert" className="rounded-lg border border-red-500/50 bg-red-950/60 text-red-200 text-sm px-3 py-2">{error}</div> : null}

      {/* ------------------------------------------------ main grid */}
      <div className="grid gap-3 lg:grid-cols-[300px_minmax(0,1fr)_360px]">
        {/* ---- tourist queue ---- */}
        <section className="rounded-xl border border-slate-700/70 bg-slate-900/70 flex flex-col h-[640px]">
          <div className="p-3 border-b border-slate-800 space-y-2">
            <div className="text-[11px] tracking-widest text-slate-400 uppercase">Tourist queue</div>
            <div className="text-xl font-bold">{visible.length} <span className="text-sm font-normal text-slate-400">shown</span></div>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search name or SIM-id"
              className="w-full px-2 py-1.5 rounded-lg bg-slate-800 border border-slate-600 text-sm" />
            <select value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Status filter"
              className="w-full px-2 py-1.5 rounded-lg bg-slate-800 border border-slate-600 text-sm">
              <option value="all">All status</option>
              {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label} ({counts[k] ?? 0})</option>)}
            </select>
          </div>
          <ul className="overflow-y-auto flex-1 divide-y divide-slate-800">
            {visible.length === 0 ? (
              <li className="p-4 text-sm text-slate-400">
                {agents.length ? 'No tourists match.' : 'No simulated tourists yet — press Spawn or Play.'}
              </li>
            ) : visible.map((a) => (
              <li key={a.id}>
                <button onClick={() => select(a)}
                  className={`w-full text-left px-3 py-2 hover:bg-slate-800/70 ${selectedId === a.id ? 'bg-slate-800' : ''}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-sm truncate">{a.name}</span>
                    <Chip color={STATUS[a.status]?.color || '#94a3b8'}>{STATUS[a.status]?.label || a.status}</Chip>
                  </div>
                  <div className="text-[11px] text-slate-400 flex justify-between">
                    <span>{a.digital_id} · {a.nationality}</span>
                    <span>score {a.score}</span>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* ---- map ---- */}
        <section className="rounded-xl border border-slate-700/70 overflow-hidden relative h-[640px]">
          <MapContainer center={center} zoom={13} className="sim-map h-full w-full" style={{ background: '#0b1420' }}>
            <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains={TILE_SUBDOMAINS} />
            <FlyTo target={flyTarget} />
            <FitOnce points={agents.map((a) => [a.lat, a.lng])} />
            {layers.zones && zones.map((z) => (
              <Polygon key={z.id} positions={z.polygon}
                pathOptions={{ color: riskColor[z.risk_level] || '#64748b', weight: 1.5, fillOpacity: 0.12 }}>
                <Tooltip sticky>{z.name} · {z.risk_level}</Tooltip>
              </Polygon>
            ))}
            {layers.route && detail && detail.id === selectedId && (
              <>
                <Polyline positions={detail.route} pathOptions={{ color: '#38bdf8', weight: 2, dashArray: '6 6', opacity: 0.8 }} />
                <Polyline positions={detail.trail} pathOptions={{ color: '#f8fafc', weight: 3, opacity: 0.9 }} />
              </>
            )}
            {layers.tourists && agents.map((a) => {
              const st = STATUS[a.status] || STATUS.normal
              if (a.status === 'sos') {
                return (
                  <Marker key={a.id} position={[a.lat, a.lng]} icon={sosIcon}
                    eventHandlers={{ click: () => select(a) }}>
                    <Tooltip>{a.name} · SOS</Tooltip>
                  </Marker>
                )
              }
              return (
                <CircleMarker key={a.id} center={[a.lat, a.lng]}
                  radius={a.id === selectedId ? 9 : a.status === 'normal' ? 5 : 7}
                  pathOptions={{ color: a.id === selectedId ? '#fff' : '#0f172a', weight: a.id === selectedId ? 3 : 1,
                    fillColor: st.color, fillOpacity: 0.95 }}
                  eventHandlers={{ click: () => select(a) }}>
                  <Tooltip>{a.name} · {st.label} · {a.score}</Tooltip>
                </CircleMarker>
              )
            })}
          </MapContainer>

          <div className="absolute top-3 right-3 z-[500] w-44 rounded-xl border border-slate-700 bg-slate-900/90 backdrop-blur p-3 text-xs">
            <div className="tracking-widest text-slate-400 uppercase mb-1">Layers</div>
            {[['tourists', 'Tourists'], ['zones', 'Risk zones'], ['route', 'Route & trail']].map(([k, label]) => (
              <label key={k} className="flex items-center justify-between py-1 cursor-pointer">
                <span>{label}</span>
                <input type="checkbox" checked={layers[k]} onChange={() => setLayers((l) => ({ ...l, [k]: !l[k] }))} />
              </label>
            ))}
            <div className="border-t border-slate-700 mt-2 pt-2 space-y-1">
              {Object.entries(STATUS).map(([k, v]) => (
                <div key={k} className="flex items-center gap-2">
                  <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: v.color }} />
                  <span className="flex-1">{v.label}</span><span className="text-slate-400">{counts[k] ?? 0}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ---- right panel ---- */}
        <section className="rounded-xl border border-slate-700/70 bg-slate-900/70 flex flex-col h-[640px]">
          <div role="tablist" className="flex border-b border-slate-800 text-xs">
            {[['trigger', 'Events'], ['tourist', 'Tourist'], ['alerts', `Alerts${criticalAlerts.length ? ` (${criticalAlerts.length})` : ''}`], ['log', 'Log']].map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                className={`flex-1 px-2 py-2.5 font-semibold tracking-wide uppercase ${tab === k
                  ? 'text-sky-300 border-b-2 border-sky-400' : 'text-slate-400 hover:text-slate-200'}`}>{label}</button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto p-3 text-sm">
            {tab === 'trigger' && (
              <div className="space-y-3">
                <div className="rounded-lg border border-slate-700 bg-slate-950/60 p-2 text-xs text-slate-300">
                  Target: {selected
                    ? <b>{selected.name} ({selected.digital_id})</b>
                    : <span className="text-slate-400">none selected — a random free tourist is used</span>}
                  {selected && <button onClick={() => setSelectedId(null)} className="ml-2 underline text-sky-300">clear</button>}
                </div>
                {riskyZones.length > 0 && (
                  <label className="block text-xs text-slate-400">Danger zone to enter
                    <select value={zoneId} onChange={(e) => setZoneId(e.target.value)}
                      className="mt-1 w-full px-2 py-1.5 rounded-lg bg-slate-800 border border-slate-600 text-slate-100 text-sm">
                      <option value="">Any high-risk / restricted zone</option>
                      {riskyZones.map((z) => <option key={z.id} value={z.id}>{z.name} ({z.risk_level})</option>)}
                    </select>
                  </label>
                )}
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(EVENT_LABEL).map(([ev, [icon, label]]) => (
                    <button key={ev} disabled={busy || !agents.length} onClick={() => fire(ev)}
                      title={sim?.events?.[ev]}
                      className={`px-2 py-2.5 rounded-lg border text-left text-xs font-semibold hover:bg-slate-800 ${ev === 'sos'
                        ? 'border-red-500/60 text-red-200' : ev === 'resolve' ? 'border-emerald-500/50 text-emerald-200' : 'border-slate-600'}`}>
                      <span className="mr-1">{icon}</span>{label}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-slate-500">
                  Events only move the virtual tourist. Whether an alert or incident appears is decided by the real
                  ML anomaly model, geofencing and SOS workflow. Inactivity needs ~70 simulated minutes of silence
                  to register — use 30x–60x.
                </p>
                {sim?.scenario && (
                  <div className="text-xs rounded-lg bg-sky-950/60 border border-sky-700/60 p-2">
                    Scenario “{sim.scenario}” running — {sim.scenario_pending} step(s) left.
                  </div>
                )}
              </div>
            )}

            {tab === 'tourist' && (!selected ? (
              <p className="text-slate-400">Select a tourist from the queue or the map.</p>
            ) : (
              <div className="space-y-3">
                <div>
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-lg font-bold">{selected.name}</h3>
                    <Chip color={STATUS[selected.status]?.color}>{STATUS[selected.status]?.label}</Chip>
                  </div>
                  <div className="text-xs text-slate-400">{selected.digital_id} · {selected.nationality}{detail ? ` · ${detail.phone}` : ''}</div>
                </div>
                <div className="grid grid-cols-3 gap-2 text-center">
                  <Tile label="Safety" value={selected.score} />
                  <Tile label="km/h" value={selected.speed_kmh} />
                  <Tile label="Anomaly" value={selected.anomaly_score} />
                </div>
                <div className="text-xs text-slate-300">
                  Location {selected.lat.toFixed(5)}, {selected.lng.toFixed(5)}
                  {selected.flags?.length ? <div className="mt-1 flex flex-wrap gap-1">{selected.flags.map((f) => <Chip key={f} color="#f97316">{f}</Chip>)}</div> : null}
                </div>
                <div>
                  <div className="text-[11px] tracking-widest text-slate-400 uppercase mb-1">Recent GPS history</div>
                  <ul className="text-xs space-y-0.5 max-h-40 overflow-y-auto font-mono">
                    {(detail?.pings || []).slice(0, 15).map((p) => (
                      <li key={p.ts} className={p.anomaly ? 'text-purple-300' : 'text-slate-300'}>
                        {fmtClock(p.ts)} · {p.lat.toFixed(4)},{p.lng.toFixed(4)} · {p.speed_kmh} km/h{p.anomaly ? ' ⚠' : ''}
                      </li>
                    ))}
                    {!detail?.pings?.length && <li className="text-slate-500">No pings yet — press Play.</li>}
                  </ul>
                </div>
                <div>
                  <div className="text-[11px] tracking-widest text-slate-400 uppercase mb-1">Alerts for this tourist</div>
                  <ul className="space-y-1">
                    {(detail?.alerts || []).slice(0, 8).map((a) => (
                      <li key={a.id} className="text-xs border-l-2 pl-2" style={{ borderColor: SEV[a.severity] }}>{a.message}</li>
                    ))}
                    {!detail?.alerts?.length && <li className="text-xs text-slate-500">None.</li>}
                  </ul>
                </div>
              </div>
            ))}

            {tab === 'alerts' && (
              <ul className="space-y-2">
                {alerts.length === 0 && <li className="text-slate-400">No alerts yet. Trigger an event or run the scenario.</li>}
                {alerts.map((a) => {
                  const who = a.tourist || byId[a.tourist_id]?.name || `Tourist #${a.tourist_id}`
                  return (
                    <li key={`${a.id}-${a.created_at}`}>
                      <button onClick={() => byId[a.tourist_id] && select(byId[a.tourist_id])}
                        className="w-full text-left rounded-lg border bg-slate-950/60 p-2 hover:bg-slate-800"
                        style={{ borderColor: (SEV[a.severity] || '#475569') + '88' }}>
                        <div className="flex justify-between items-center">
                          <Chip color={SEV[a.severity] || '#94a3b8'}>{a.severity} · {a.type}</Chip>
                          <span className="text-[10px] text-slate-500">{fmtClock(a.created_at)}</span>
                        </div>
                        <div className="text-xs mt-1">{a.message}</div>
                        <div className="text-[11px] text-slate-400">{who}</div>
                      </button>
                    </li>
                  )
                })}
                <li className="text-[11px] text-slate-500 pt-1">
                  Incidents and the response workflow (acknowledge → dispatch → resolve) are on the Incidents page.
                </li>
              </ul>
            )}

            {tab === 'log' && (
              <ul className="space-y-1.5 font-mono text-[11px]">
                {log.length === 0 && <li className="text-slate-400">Event log is empty.</li>}
                {[...log].reverse().map((l) => (
                  <li key={l.seq} className="flex gap-2">
                    <span className="text-slate-500 shrink-0">{fmtClock(l.sim_time)}</span>
                    <span style={{ color: l.level === 'critical' ? '#fca5a5' : l.level === 'warn' ? '#fcd34d' : '#cbd5e1' }}>{l.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
