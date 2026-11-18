import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Polyline } from 'react-leaflet'
import { useSearchParams } from 'react-router-dom'
import api from '../../api'
import useWebSocket from '../../useWebSocket'
import { getEmergencyTrack, listLiveEmergencies } from '../../lib/emergencyLocationService'
import { sosIcon } from '../../components/mapIcons'
import { TILE_ATTRIBUTION, TILE_SUBDOMAINS, TILE_URL } from "../../lib/mapConfig"

const STATUS_META = {
  live: { label: '🟢 LIVE', cls: 'bg-emerald-100 text-emerald-700' },
  stale: { label: '🟡 STALE', cls: 'bg-amber-100 text-amber-700' },
  offline: { label: '🔴 OFFLINE', cls: 'bg-red-100 text-red-700' },
  no_data: { label: '⚪ NO DATA', cls: 'bg-slate-100 text-slate-500' },
}

function timeAgo(seconds) {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Math.round(seconds)} sec ago`
  return `${Math.round(seconds / 60)} min ago`
}

// Backend timestamps are naive-UTC ("2026-09-09T15:38:34.215304", no "Z" --
// see core/time.py). Without an explicit zone/offset, `new Date(...)`
// parses the string as *local* time, not UTC -- on a UTC+5:30 client that
// silently adds 5.5 hours to every computed age, which was making a
// just-arrived ping look ~330 minutes stale (and OFFLINE) instead of LIVE.
function parseUtc(ts) {
  return new Date(/[Zz]|[+-]\d\d:\d\d$/.test(ts) ? ts : `${ts}Z`)
}

// Central Safety Dashboard's live-emergency view -- see
// backend/app/services/emergency_location.py. GPS -> backend (real) ->
// this page (real-time via the same admin WebSocket feed every other live
// dashboard already uses) -> live map. A card's own position marker moves
// as soon as an `emergency_location` event arrives for its incident; no
// polling needed while the socket is connected (list refresh on load/new
// incident still uses a plain fetch).
export default function LiveEmergencies() {
  const [emergencies, setEmergencies] = useState([])
  // Deep-link target from "View Live Location" elsewhere in the dashboard
  // (PoliceNetwork.jsx) -- e.g. /admin/live-emergencies?incident=42.
  const [searchParams] = useSearchParams()
  const linkedIncidentId = Number(searchParams.get('incident')) || null
  const [selectedId, setSelectedId] = useState(linkedIncidentId)
  const [error, setError] = useState('')
  // The deep-linked incident specifically, if it isn't (or isn't yet) part
  // of the "currently live" aggregate below -- e.g. right after a transfer,
  // before the next ping, or a case whose location is STALE/OFFLINE rather
  // than LIVE. Fetched by id directly (works for any incident, not just
  // ones the aggregate list currently includes) so "View Live Location"
  // always shows *something* for the exact case that was clicked, instead
  // of silently falling back to a different one or showing nothing.
  const [linkedNotice, setLinkedNotice] = useState('')

  // Tick-to-resolve: tick one or more cases and mark them resolved, which closes the
  // incident (stops live tracking, clears the tourist's SOS status) and drops it from this list.
  const [checked, setChecked] = useState(() => new Set())
  const [confirming, setConfirming] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [resolveMsg, setResolveMsg] = useState(null)

  const load = () => {
    listLiveEmergencies()
      .then((data) => { setEmergencies(data); setError('') })
      .catch(() => setError('Could not load live emergencies right now.'))
  }
  useEffect(load, [])

  useEffect(() => {
    if (!linkedIncidentId) return
    setLinkedNotice('')
    getEmergencyTrack(linkedIncidentId)
      .then((track) => {
        setEmergencies((list) => (
          list.some((e) => e.incident_id === track.incident_id)
            ? list.map((e) => (e.incident_id === track.incident_id ? track : e))
            : [track, ...list]
        ))
        if (!track.latest) {
          setLinkedNotice(`Case #${linkedIncidentId} has not received any location update yet.`)
        }
      })
      .catch(() => setLinkedNotice(`Could not load case #${linkedIncidentId} — it may not exist or you may not have access.`))
  }, [linkedIncidentId])

  // Live position updates without a full re-fetch: patch just the affected
  // incident's `latest`/`trail`/`location_status` in place.
  useWebSocket((msg) => {
    if (msg.event === 'emergency_location') {
      setEmergencies((list) => list.map((e) => (
        e.incident_id === msg.incident_id
          ? {
              ...e,
              latest: { lat: msg.lat, lng: msg.lng, accuracy_m: msg.accuracy_m,
                       speed_kmh: msg.speed_kmh, heading_deg: msg.heading_deg,
                       timestamp: msg.timestamp, anomaly_flag: msg.anomaly_flag, demo: msg.demo },
              trail: [...e.trail, { lat: msg.lat, lng: msg.lng }].slice(-60),
              location_status: 'live', seconds_since_update: 0,
            }
          : e
      )))
    } else if (msg.event === 'emergency_tracking_stopped') {
      setEmergencies((list) => list.filter((e) => e.incident_id !== msg.incident_id))
    } else if (msg.event === 'incident') {
      load() // a fresh SOS -- pick up the new live-tracking incident
    }
  })

  // Recompute LIVE -> STALE -> OFFLINE locally between socket pushes, same
  // thresholds the backend uses, so a card never keeps claiming LIVE once
  // updates have actually stopped arriving.
  useEffect(() => {
    const iv = setInterval(() => {
      setEmergencies((list) => list.map((e) => {
        if (!e.latest) return e
        const age = (Date.now() - parseUtc(e.latest.timestamp).getTime()) / 1000
        const status = age <= 15 ? 'live' : age <= 45 ? 'stale' : 'offline'
        return { ...e, location_status: status, seconds_since_update: age }
      }))
    }, 3000)
    return () => clearInterval(iv)
  }, [])

  const toggleChecked = (id) => setChecked((prev) => {
    const next = new Set(prev)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })
  const allChecked = emergencies.length > 0 && emergencies.every((e) => checked.has(e.incident_id))
  const toggleAll = () => setChecked(allChecked ? new Set() : new Set(emergencies.map((e) => e.incident_id)))

  const resolveChecked = async () => {
    const ids = emergencies.filter((e) => checked.has(e.incident_id)).map((e) => e.incident_id)
    if (ids.length === 0) return
    setResolving(true); setResolveMsg(null)
    const results = await Promise.allSettled(
      ids.map((id) => api.patch(`/incidents/${id}`, { status: 'resolved', note: 'Resolved from Live Emergencies' })),
    )
    const done = ids.filter((_, i) => results[i].status === 'fulfilled')
    const failed = ids.filter((_, i) => results[i].status === 'rejected')
    setEmergencies((list) => list.filter((e) => !done.includes(e.incident_id)))
    setChecked(new Set(failed))
    setConfirming(false); setResolving(false)
    setResolveMsg(failed.length === 0
      ? { ok: true, text: `${done.length} ${done.length === 1 ? 'emergency' : 'emergencies'} marked resolved.` }
      : { ok: false, text: `${done.length} resolved, ${failed.length} could not be resolved (${failed.map((i) => `INC-${i}`).join(', ')}). Try again.` })
  }

  const selected = emergencies.find((e) => e.incident_id === selectedId) || emergencies[0]

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-slate-800 dark:text-slate-100">🚨 Live Emergencies</h1>
        <span className="text-xs text-slate-400">{emergencies.length} active</span>
      </div>

      {error && <div className="text-sm text-red-600 dark:text-red-400">{error}</div>}
      {linkedNotice && (
        <div className="text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/30
                        border border-amber-200 dark:border-amber-800 rounded-lg px-3 py-2">
          {linkedNotice}
        </div>
      )}

      {!error && emergencies.length === 0 && !linkedIncidentId && (
        <div className="text-sm text-slate-400 bg-white dark:bg-slate-800 rounded-xl p-6 text-center">
          No active emergencies right now.
        </div>
      )}

      {(emergencies.length > 0 || linkedIncidentId) && (
        <div className="grid md:grid-cols-3 gap-4">
          <div className="md:col-span-1 space-y-2">
            {emergencies.length > 0 && (
              <div className="bg-white dark:bg-slate-800 rounded-xl p-2 text-sm space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <label className="flex items-center gap-2 text-xs font-semibold text-slate-600 dark:text-slate-300 cursor-pointer">
                    <input type="checkbox" checked={allChecked} onChange={toggleAll} className="h-4 w-4 accent-red-600" />
                    Select all
                  </label>
                  <button type="button" disabled={checked.size === 0 || resolving} onClick={() => setConfirming(true)}
                    className="px-3 py-1 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40 disabled:cursor-not-allowed">
                    ✓ Mark resolved{checked.size > 0 ? ` (${checked.size})` : ''}
                  </button>
                </div>
                {confirming && (
                  <div role="alertdialog" aria-label="Confirm resolve" className="rounded-lg border border-amber-300 bg-amber-50 dark:bg-amber-900/30 dark:border-amber-800 p-2 text-xs text-amber-900 dark:text-amber-200">
                    Mark <b>{checked.size}</b> {checked.size === 1 ? 'emergency' : 'emergencies'} as resolved? This closes the case, stops live
                    tracking, and tells the tourist and their family it is resolved.
                    <div className="flex gap-2 mt-2">
                      <button type="button" disabled={resolving} onClick={resolveChecked}
                        className="px-3 py-1 rounded-lg font-semibold bg-emerald-600 text-white disabled:opacity-60">
                        {resolving ? 'Resolving…' : 'Yes, resolve'}
                      </button>
                      <button type="button" disabled={resolving} onClick={() => setConfirming(false)}
                        className="px-3 py-1 rounded-lg font-semibold border border-slate-300 dark:border-slate-600 text-slate-700 dark:text-slate-200">
                        Cancel
                      </button>
                    </div>
                  </div>
                )}
                {resolveMsg && (
                  <div role="status" className={`text-xs ${resolveMsg.ok ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'}`}>
                    {resolveMsg.text}
                  </div>
                )}
              </div>
            )}
            <div className="space-y-2 max-h-[62vh] overflow-y-auto">
            {emergencies.map((e) => {
              const meta = STATUS_META[e.location_status] || STATUS_META.no_data
              return (
                <div key={e.incident_id} className="flex items-start gap-2">
                <input type="checkbox" aria-label={`Select INC-${e.incident_id}`} checked={checked.has(e.incident_id)}
                  onChange={() => toggleChecked(e.incident_id)} className="mt-4 h-4 w-4 shrink-0 accent-red-600 cursor-pointer" />
                <button onClick={() => setSelectedId(e.incident_id)}
                  className={`flex-1 min-w-0 text-left bg-white dark:bg-slate-800 rounded-xl p-3 text-sm border-2 ${
                    selected?.incident_id === e.incident_id ? 'border-red-500' : 'border-transparent'}`}>
                  <div className="flex items-center justify-between">
                    <span className="font-bold">🚨 INC-{e.incident_id} · {e.tourist_name}</span>
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${meta.cls}`}>{meta.label}</span>
                  </div>
                  <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                    Digital ID: {e.digital_id}
                  </div>
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    Station: {e.station_name || 'Unassigned'}
                    {e.pending_transfer_id && (
                      <span className="ml-1.5 text-[10px] font-bold text-amber-600 dark:text-amber-400">
                        · TRANSFER PENDING
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">
                    Updated {timeAgo(e.seconds_since_update)}
                    {e.latest?.accuracy_m != null && ` · ±${Math.round(e.latest.accuracy_m)} m`}
                    {e.latest?.demo && ' · demo'}
                  </div>
                </button>
                </div>
              )
            })}
            </div>
          </div>

          <div className="md:col-span-2 bg-white dark:bg-slate-800 rounded-xl overflow-hidden" style={{ height: 500 }}>
            {selected?.latest ? (
              <MapContainer center={[selected.latest.lat, selected.latest.lng]} zoom={15}
              className="map-ops-dark"
                style={{ height: '100%' }} key={selected.incident_id}>
                <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains={TILE_SUBDOMAINS} />
                <Marker position={[selected.latest.lat, selected.latest.lng]} icon={sosIcon}>
                  <Popup>
                    <div className="text-xs">
                      <div className="font-bold">INC-{selected.incident_id} · {selected.tourist_name}</div>
                      <div>Digital ID: {selected.digital_id}</div>
                      <div>Accuracy: ±{Math.round(selected.latest.accuracy_m || 0)} m</div>
                      {selected.latest.speed_kmh != null && <div>Speed: {selected.latest.speed_kmh.toFixed(1)} km/h</div>}
                      {selected.latest.anomaly_flag && <div className="text-orange-600 font-semibold">⚠ Location anomaly detected</div>}
                    </div>
                  </Popup>
                </Marker>
                {selected.trail?.length > 1 && (
                  <Polyline positions={selected.trail.map((p) => [p.lat, p.lng])}
                    pathOptions={{ color: '#dc2626', weight: 3, dashArray: '4 4' }} />
                )}
              </MapContainer>
            ) : selected ? (
              <div className="h-full flex items-center justify-center text-sm text-slate-400 text-center px-6">
                Case #{selected.incident_id} ({selected.digital_id || selected.tourist_name}) has not
                received a location update yet.
              </div>
            ) : (
              <div className="h-full flex items-center justify-center text-sm text-slate-400">
                Waiting for the first location update…
              </div>
            )}
          </div>

          {/* Full case + tourist detail -- everything a receiving station
              needs after a transfer, in one place (see backend
              services/emergency_location.py:build_track). */}
          {selected && (
            <div className="md:col-span-3 bg-white dark:bg-slate-800 rounded-xl p-4 text-sm grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {selected.tourist_photo && (
                <div className="sm:col-span-2 lg:col-span-4 flex items-center gap-3">
                  <img src={selected.tourist_photo} alt={selected.tourist_name}
                    className="w-14 h-14 rounded-full object-cover border border-slate-200 dark:border-slate-600" />
                  <div>
                    <div className="font-bold text-slate-800 dark:text-slate-100">{selected.tourist_name}</div>
                    <div className="text-xs text-slate-400">{selected.digital_id}</div>
                  </div>
                </div>
              )}
              <div><div className="text-xs text-slate-400">Phone</div><div>{selected.tourist_phone || '—'}</div></div>
              <div><div className="text-xs text-slate-400">Nationality</div><div>{selected.tourist_nationality || '—'}</div></div>
              <div><div className="text-xs text-slate-400">Hotel</div><div>{selected.hotel || '—'}</div></div>
              <div><div className="text-xs text-slate-400">Safety Score</div><div>{selected.safety_score != null ? Math.round(selected.safety_score) : '—'}</div></div>
              <div><div className="text-xs text-slate-400">Incident Type</div><div className="capitalize">{selected.incident_type} · {selected.severity}</div></div>
              <div><div className="text-xs text-slate-400">Detected</div><div>{selected.detected_at ? new Date(selected.detected_at).toLocaleString() : '—'}</div></div>
              <div className="sm:col-span-2"><div className="text-xs text-slate-400">Description</div><div>{selected.description || '—'}</div></div>
              {selected.emergency_contacts?.length > 0 && (
                <div className="sm:col-span-2 lg:col-span-4">
                  <div className="text-xs text-slate-400 mb-1">Emergency Contacts</div>
                  <div className="flex flex-wrap gap-2">
                    {selected.emergency_contacts.map((c, i) => (
                      <span key={i} className="text-xs bg-slate-100 dark:bg-slate-700 px-2 py-1 rounded-full">
                        {c.name} ({c.relation}) · {c.phone}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
