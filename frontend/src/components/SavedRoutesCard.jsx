import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import TripMap from './TripMap.jsx'
import {
  listSavedRoutes, markRouteOpened, deleteSavedRoute, formatDuration,
} from '../lib/routeService.js'
import { openDirections } from '../lib/mapsLinks.js'

const MODE_ICON = { driving: '🚗', walking: '🚶', bicycling: '🚲', transit: '🚌' }

// The tourist's saved routes, straight from the database (so they survive an
// app restart): open one on the map, resume navigation in Google Maps from
// wherever they are now, or delete it. `refreshKey` changes when a route is
// saved elsewhere so the list reloads.
export default function SavedRoutesCard({ touristId, refreshKey = 0 }) {
  const { t } = useTranslation()
  const [routes, setRoutes] = useState(null) // null = loading
  const [error, setError] = useState('')
  const [openId, setOpenId] = useState(null)
  const [confirmId, setConfirmId] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      setRoutes(await listSavedRoutes(touristId))
    } catch {
      setRoutes((r) => r || [])
      setError(t('saved_routes.load_failed', "Couldn't load your saved routes. Check your connection and try again."))
    }
  }, [touristId, t])
  useEffect(() => { load() }, [load, refreshKey])

  const touch = (r) => markRouteOpened(touristId, r.id)
    .then((u) => setRoutes((list) => list.map((x) => (x.id === u.id ? u : x))))
    .catch(() => {}) // bookkeeping only -- never block opening a route on it

  const view = (r) => {
    setOpenId(openId === r.id ? null : r.id)
    if (openId !== r.id) touch(r)
  }
  const navigate = (r) => {
    touch(r)
    // No origin: Google Maps starts from the device's live location, which is
    // what "resume" means -- the tourist may be nowhere near where it was saved.
    openDirections({ destination: r.destination, stops: r.stops, mode: r.mode })
  }
  const remove = async (r) => {
    setBusyId(r.id)
    setNotice('')
    try {
      await deleteSavedRoute(touristId, r.id)
      setRoutes((list) => list.filter((x) => x.id !== r.id))
      setConfirmId(null)
      if (openId === r.id) setOpenId(null)
      setNotice(t('saved_routes.deleted', 'Route deleted.'))
    } catch {
      setError(t('saved_routes.delete_failed', "Couldn't delete that route. Please try again."))
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Card title={t('saved_routes.title', 'Saved routes')} icon="🔖" iconColor="bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
      {routes === null && (
        <div className="text-xs text-slate-400 flex items-center gap-1.5" role="status">
          <span className="inline-block w-3 h-3 border-2 border-slate-300 border-t-sky-500 rounded-full animate-spin" />
          {t('saved_routes.loading', 'Loading your routes…')}
        </div>
      )}
      {error && (
        <div className="text-xs text-red-600 dark:text-red-400 mb-2" role="alert">
          {error} <button type="button" onClick={load} className="underline font-semibold">{t('location.try_again', 'Try again')}</button>
        </div>
      )}
      {notice && <div className="text-xs text-emerald-600 dark:text-emerald-400 mb-2" role="status">{notice}</div>}
      {routes && routes.length === 0 && !error && (
        <div className="text-xs text-slate-400">
          {t('saved_routes.empty', 'No saved routes yet. Plan a route or itinerary, then tap "Save route" to keep it here.')}
        </div>
      )}
      <ul className="space-y-2">
        {routes?.map((r) => (
          <li key={r.id} className="border border-slate-100 dark:border-slate-700 rounded-lg p-2.5 text-sm">
            <div className="font-medium">{MODE_ICON[r.mode]} {r.name}</div>
            <div className="text-xs text-slate-500 dark:text-slate-400">
              {r.origin.name || t('saved_routes.start', 'Start')} → {r.destination.name || t('saved_routes.end', 'Destination')}
              {r.stops.length > 0 && ` (${t('saved_routes.stops', '{{count}} stops', { count: r.stops.length })})`}
            </div>
            <div className="text-[11px] text-slate-400">
              {r.distance_km != null && `${r.distance_km} km`}
              {r.duration_min != null && ` · ${formatDuration(r.duration_min)}`}
              {r.risk_level && ` · ${t('saved_routes.risk', 'risk')}: ${r.risk_level}`}
              {' · '}{t('saved_routes.saved_on', 'saved {{date}}', { date: new Date(r.created_at + (r.created_at.endsWith('Z') ? '' : 'Z')).toLocaleDateString() })}
            </div>
            <div className="mt-2 flex flex-wrap gap-2 text-xs font-semibold">
              <button type="button" onClick={() => view(r)} aria-expanded={openId === r.id}
                className="bg-indigo-50 dark:bg-indigo-900/20 text-indigo-700 dark:text-indigo-300 px-3 py-1.5 rounded-lg">
                {openId === r.id ? t('saved_routes.hide_map', 'Hide map') : `🗺 ${t('saved_routes.view_map', 'View on map')}`}
              </button>
              <button type="button" onClick={() => navigate(r)}
                className="bg-sky-600 text-white px-3 py-1.5 rounded-lg">
                ▶ {t('saved_routes.navigate', 'Navigate')}
              </button>
              {confirmId === r.id ? (
                <>
                  <button type="button" onClick={() => remove(r)} disabled={busyId === r.id}
                    className="bg-red-600 text-white px-3 py-1.5 rounded-lg disabled:opacity-50">
                    {busyId === r.id ? t('saved_routes.deleting', 'Deleting…') : t('saved_routes.confirm_delete', 'Yes, delete')}
                  </button>
                  <button type="button" onClick={() => setConfirmId(null)} className="text-slate-500 px-2">{t('saved_routes.cancel', 'Cancel')}</button>
                </>
              ) : (
                <button type="button" onClick={() => setConfirmId(r.id)}
                  className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 px-3 py-1.5 rounded-lg">
                  🗑 {t('saved_routes.delete', 'Delete')}
                </button>
              )}
            </div>
            {openId === r.id && (
              <div className="mt-2">
                <TripMap
                  stops={[r.origin, ...r.stops, r.destination].map((p) => ({ lat: p.lat, lng: p.lng }))}
                  path={r.geometry}
                />
                {!r.geometry && (
                  <div className="text-[11px] text-slate-400 mt-1">
                    {t('saved_routes.no_line', 'This route was saved without a road line, so stops are joined by straight dashed lines.')}
                  </div>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
    </Card>
  )
}
