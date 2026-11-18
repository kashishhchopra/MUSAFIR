import { useState } from 'react'
import { Polyline, CircleMarker, useMapEvents } from 'react-leaflet'
import api from '../api'
import { riskColor } from './mapIcons'
import { saveRoute } from '../lib/routeService'
import { openDirections } from '../lib/mapsLinks'

// Shared state for the safe-route picker, split across two rendering sites:
// `RouteLayer` (map children -- click capture + polylines, must live inside
// a MapContainer) and the default-exported `RoutePicker` (the outside-map
// control panel), matching the existing pattern of keeping plain HTML
// controls outside MapContainer (see Zones.jsx) while only Leaflet layers go
// inside it. Both pieces are driven by this one hook so TouristApp only has
// to wire a single instance of state.
export function useRoutePicker(tid) {
  const [dest, setDest] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const pick = (p) => { setDest(p); setResult(null); setError(null); setSaved(false); setSaveError(null) }

  // Persist the recommended route for real (POST /tourists/{id}/saved-routes):
  // start = the tourist's current position, end = the tapped destination, the
  // route line and its risk level as computed by the server.
  const save = async (origin, name) => {
    if (!dest || !result?.recommended || !origin) return false
    setSaving(true)
    setSaveError(null)
    try {
      await saveRoute(tid, {
        name: (name || '').trim() || `Safe route ${new Date().toLocaleDateString()}`,
        mode: 'driving',
        origin: { name: 'My location', lat: origin[0], lng: origin[1] },
        destination: { name: `Pin ${dest[0].toFixed(4)}, ${dest[1].toFixed(4)}`, lat: dest[0], lng: dest[1] },
        distance_km: result.recommended.length_km,
        geometry: result.recommended.points,
        risk_level: result.recommended.risk_level || '',
      })
      setSaved(true)
      return true
    } catch (err) {
      const detail = err.response?.data?.detail
      setSaveError(typeof detail === 'string' ? detail : 'Could not save this route. Please try again.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const findRoute = async () => {
    if (!dest) return
    setLoading(true)
    setError(null)
    setSaved(false)
    setSaveError(null)
    try {
      const { data } = await api.get(`/tourists/${tid}/route-recommendation`, {
        params: { dest_lat: dest[0], dest_lng: dest[1] },
      })
      setResult(data)
    } catch (err) {
      setError(err.response?.data?.detail || 'Could not find a route')
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  const reset = () => { setDest(null); setResult(null); setError(null); setSaved(false); setSaveError(null) }

  return { dest, result, error, loading, pick, findRoute, reset, save, saving, saved, saveError }
}

// Map layer: click-to-pick-destination marker + candidate route polylines,
// colored by risk level with the recommended one drawn thicker. Renders
// nothing when there's no destination/result yet. Uses the same hand-rolled
// click-capture pattern as Zones.jsx's DrawCapture.
export function RouteLayer({ active, dest, result, onPick }) {
  useMapEvents({
    click(e) {
      if (active) onPick([e.latlng.lat, e.latlng.lng])
    },
  })

  return (
    <>
      {dest && (
        <CircleMarker center={dest} radius={7}
          pathOptions={{ color: '#0284c7', fillColor: '#0284c7', fillOpacity: 0.9 }} />
      )}
      {result?.candidates?.map((c, i) => {
        const isRecommended = c === result.recommended
        return (
          <Polyline
            key={i}
            positions={c.points}
            className={isRecommended ? 'route-recommended' : 'route-candidate'}
            pathOptions={{
              color: riskColor[c.risk_level] || riskColor.medium,
              weight: isRecommended ? 6 : 3,
              opacity: isRecommended ? 0.95 : 0.6,
              dashArray: isRecommended ? null : '4 6',
            }}
          />
        )
      })}
    </>
  )
}

// Outside-map control panel: toggle, destination readout, "Find safe route"
// button, and the recommendation summary. `state` is a `useRoutePicker(tid)`
// instance shared with the `RouteLayer` rendered inside the map.
export default function RoutePicker({ active, onToggle, state, origin, onSaved }) {
  const { dest, result, error, loading, findRoute, reset, save, saving, saved, saveError } = state
  const [name, setName] = useState('')

  if (!active) return null

  const doSave = async () => { if (await save(origin, name)) { setName(''); onSaved?.() } }

  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl shadow-sm p-4 text-sm space-y-2">
      <div className="flex items-center justify-between">
        <div className="font-medium text-slate-900 dark:text-slate-100">Safe route</div>
        <button onClick={onToggle} className="text-xs text-slate-500 dark:text-slate-400">Close</button>
      </div>
      <div className="text-xs text-slate-500 dark:text-slate-400">
        {dest ? `Destination: ${dest[0].toFixed(4)}, ${dest[1].toFixed(4)}` : 'Tap the map to choose a destination'}
      </div>
      <div className="flex gap-2">
        <button onClick={findRoute} disabled={!dest || loading}
          className="bg-sky-600 hover:bg-sky-700 disabled:opacity-40 text-white text-xs font-semibold px-3 py-1.5 rounded-lg">
          {loading ? 'Finding…' : 'Find safe route'}
        </button>
        <button onClick={reset} disabled={!dest}
          className="bg-slate-100 hover:bg-slate-200 disabled:opacity-40 text-xs px-3 py-1.5 rounded-lg">
          Reset
        </button>
      </div>
      {error && <div className="text-xs text-red-600">{error}</div>}
      {result && (
        <div className="text-xs text-slate-500 dark:text-slate-400 space-y-1">
          <div>
            Recommended: {result.recommended.length_km} km, risk level{' '}
            <span className="font-semibold">{result.recommended.risk_level}</span>
          </div>
          <div className="italic">Approximate route, not turn-by-turn navigation.</div>
          <div className="flex gap-2 pt-1">
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder="Name this route (optional)"
              aria-label="Route name"
              className="flex-1 min-w-0 border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded-lg px-2 py-1.5" />
            <button onClick={doSave} disabled={saving || saved || !origin}
              className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold px-3 rounded-lg">
              {saving ? 'Saving…' : saved ? 'Saved ✓' : '💾 Save route'}
            </button>
          </div>
          {!origin && <div className="text-amber-600">Your current position isn't known yet, so this route can't be saved.</div>}
          {saved && <div className="text-emerald-600" role="status">Route saved — see Saved routes below.</div>}
          {saveError && <div className="text-red-600" role="alert">{saveError}</div>}
          <button onClick={() => openDirections({
            origin: origin ? { lat: origin[0], lng: origin[1] } : undefined,
            destination: { lat: dest[0], lng: dest[1] },
          })} className="text-sky-700 dark:text-sky-400 font-semibold underline">
            🗺 Open in Google Maps
          </button>
        </div>
      )}
    </div>
  )
}
