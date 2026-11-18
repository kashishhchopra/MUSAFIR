import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import SearchCenterPanel from './SearchCenterPanel.jsx'
import DiscoveryMap from './DiscoveryMap.jsx'
import NearbyPlaceCard, { NEARBY_STYLES } from './NearbyPlaceCard.jsx'
import useOnlineStatus from '../hooks/useOnlineStatus.js'
import useSearchCenter from '../hooks/useSearchCenter.js'
import useNearby, { NEARBY_CATEGORIES } from '../hooks/useNearby.js'
import { deviceTzOffsetMin } from '../lib/nearbyService.js'
import { getRoute } from '../lib/routeService.js'
import { openDirections } from '../lib/mapsLinks.js'
import { NATIONAL_EMERGENCY_NUMBER } from '../config'

const RADII = [1, 2, 5, 10, 25, 50]
const PAGE = 30
const PREF_KEY = 'nearby.prefs'
const CATEGORY_META = {
  hospital: { icon: '🏥', key: 'nearby.cat_hospital' },
  pharmacy: { icon: '💊', key: 'nearby.cat_pharmacy' },
  police: { icon: '👮', key: 'nearby.cat_police' },
  transport: { icon: '🚕', key: 'nearby.cat_transport' },
}
const readPrefs = () => {
  try {
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || '{}')
    return { radius: RADII.includes(p.radius) ? p.radius : 5, view: p.view === 'map' ? 'map' : 'list' }
  } catch { return { radius: 5, view: 'list' } }
}
const chip = (on) => `text-xs font-semibold px-3 py-1.5 rounded-full whitespace-nowrap ${on
  ? 'bg-sky-600 text-white' : 'bg-slate-100 dark:bg-slate-700 text-slate-600 dark:text-slate-300'}`
const SORTERS = {
  nearest: (a, b) => a.distance_km - b.distance_km,
  relevance: (a, b) => a.rank - b.rank || a.distance_km - b.distance_km, // emergency hospitals first, then nearest
  name: (a, b) => a.name.localeCompare(b.name),
}

// Nearby: the real hospitals, pharmacies, police stations and transport around the tourist,
// from OpenStreetMap (backend/app/services/nearby.py). The position is the phone's GPS, or a
// place the tourist types when GPS is unavailable -- never a stored coordinate. Every failure
// (permission refused, GPS off, offline, map services down) is named; "nothing found" only
// ever means the map has nothing within the chosen radius.
export default function NearbyPlacesCard() {
  const { t } = useTranslation()
  const online = useOnlineStatus()
  const sc = useSearchCenter()
  const { gps, center } = sc
  const [prefs, setPrefs] = useState(readPrefs)
  const [category, setCategory] = useState('hospital')
  const [sort, setSort] = useState('relevance')
  const [query, setQuery] = useState('')
  const [openOnly, setOpenOnly] = useState(false)
  const [selectedId, setSelectedId] = useState(null)
  const [limit, setLimit] = useState(PAGE)
  const [routes, setRoutes] = useState({})
  const [pendingNearest, setPendingNearest] = useState(null)

  const setPref = (k, v) => setPrefs((p) => {
    const next = { ...p, [k]: v }
    try { localStorage.setItem(PREF_KEY, JSON.stringify(next)) } catch { /* optional */ }
    return next
  })

  // "Open now" needs the user's own clock, which is only meaningful at the user's own position.
  const tzOffsetMin = useMemo(() => (gps ? deviceTzOffsetMin() : undefined), [gps])
  const { byCat, refresh, retry } = useNearby({ center, radiusKm: prefs.radius, category, tzOffsetMin })
  const cur = { places: [], ...(byCat[category] || { status: center ? 'loading' : 'idle' }) }
  const meta = cur.meta || {}

  useEffect(() => { setRoutes({}); setSelectedId(null) }, [center?.lat, center?.lng, prefs.radius])
  useEffect(() => { setLimit(PAGE) }, [category, query, sort, openOnly, prefs.radius, center?.lat, center?.lng])

  const effectiveSort = sort === 'relevance' && category !== 'hospital' ? 'nearest' : sort
  const nearestId = useMemo(
    () => cur.places.reduce((m, p) => (!m || p.distance_km < m.distance_km ? p : m), null)?.id, [cur.places])
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (cur.places || [])
      .filter((p) => !openOnly || p.open_24h || p.open_now === true)
      .filter((p) => !q || [p.name, p.kind, p.address, p.operator_type, ...Object.values(p.details || {})].some((x) => x && x.toLowerCase().includes(q)))
      .sort(SORTERS[effectiveSort])
  }, [cur.places, query, openOnly, effectiveSort])

  // "Nearest hospital / police": switch there, order by distance, and highlight the first result.
  const findNearest = (cat) => {
    setCategory(cat); setSort('nearest'); setQuery(''); setOpenOnly(false); setPendingNearest(cat)
  }
  useEffect(() => {
    if (pendingNearest && pendingNearest === category && cur.status === 'ok') {
      if (nearestId) setSelectedId(nearestId)
      setPendingNearest(null)
    }
  }, [pendingNearest, category, cur.status, nearestId])

  const doRoute = (p, mode) => {
    if (!center) return
    setRoutes((r) => ({ ...r, [p.id]: { status: 'loading', mode } }))
    getRoute(center, p, mode)
      .then((data) => setRoutes((r) => ({ ...r, [p.id]: { status: 'ok', mode, data } })))
      .catch(() => setRoutes((r) => ({ ...r, [p.id]: { status: 'error', mode } })))
  }
  const directions = (p) => openDirections({
    origin: gps || undefined, // a typed place is not where the tourist is standing: let Maps use the live position
    destination: p, mode: p.distance_km <= 2 ? 'walking' : 'driving',
  })
  const showOnMap = (p) => { setPref('view', 'map'); setSelectedId(p.id) }
  const selectedRoute = selectedId && routes[selectedId]?.status === 'ok' && !routes[selectedId].data.demo
    ? routes[selectedId].data.geometry : null

  const label = (c) => t(CATEGORY_META[c].key)
  const nextRadius = RADII.find((r) => r > prefs.radius)
  const loading = cur.status === 'loading'
  const counts = (c) => (byCat[c]?.status === 'ok' ? byCat[c].places.length : null)
  const noteCls = 'text-xs rounded-lg px-2.5 py-2 mb-2'

  const errorBox = cur.status === 'error' && (
    <div role="alert" data-testid="nearby-error" className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-800 dark:text-red-200">
      {cur.error === 'offline'
        ? t('nearby.err_offline', 'Could not reach the server. Check your internet connection.')
        : cur.error === 'unavailable'
          ? t('nearby.err_unavailable', 'The free map-data services are busy or unreachable right now, so nearby places could not be loaded. This does not mean there is nothing nearby — please try again, or call the emergency number if it is urgent.')
          : t('nearby.load_error', "Couldn't load nearby places right now.")}
      <div className="mt-2">
        <button type="button" onClick={retry} data-testid="nearby-retry" className="text-xs font-semibold bg-red-600 text-white rounded-lg px-3 py-1.5">
          {t('discovery.retry', 'Try again')}
        </button>
      </div>
    </div>
  )

  const skeleton = loading && !cur.places?.length && (
    <div className="space-y-2" data-testid="nearby-skeleton" aria-busy="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className="animate-pulse border border-slate-100 dark:border-slate-700 rounded-xl p-3">
          <div className="h-3.5 w-2/3 bg-slate-200 dark:bg-slate-700 rounded" />
          <div className="h-2.5 w-1/2 bg-slate-100 dark:bg-slate-700/60 rounded mt-2" />
          <div className="h-7 w-1/3 bg-slate-100 dark:bg-slate-700/60 rounded mt-3" />
        </div>
      ))}
    </div>
  )

  const empty = cur.status === 'ok' && visible.length === 0 && (
    <div data-testid="nearby-empty" className="text-center text-sm text-slate-600 dark:text-slate-300 py-6 px-2">
      <div className="text-3xl mb-2">🔎</div>
      {openOnly && cur.places.length > 0 ? (
        <>
          <p>{t('nearby.empty_open', 'None of these are confirmed open right now.')}</p>
          <p className="text-xs text-slate-500 mt-1">{t('nearby.empty_open_hint', 'Places without published opening hours are not counted. Turn the filter off to see them all.')}</p>
          <button type="button" onClick={() => setOpenOnly(false)} className="mt-2 text-xs font-semibold text-sky-700 dark:text-sky-400 underline">{t('nearby.show_all', 'Show all')}</button>
        </>
      ) : query.trim() ? (
        <>
          <p>{t('nearby.empty_search', 'No results match “{{q}}”.', { q: query.trim() })}</p>
          <button type="button" onClick={() => setQuery('')} className="mt-2 text-xs font-semibold text-sky-700 dark:text-sky-400 underline">{t('discovery.clear_search', 'Clear search')}</button>
        </>
      ) : (
        <>
          <p>{t('nearby.empty_cat', 'No {{what}} found within {{km}} km.', { what: label(category).toLowerCase(), km: prefs.radius })}</p>
          <p className="text-xs text-slate-500 mt-1">{t('nearby.empty_hint', 'OpenStreetMap may have little data here. Try a wider radius or another location.')}</p>
          <div className="flex justify-center gap-3 mt-3 text-xs font-semibold">
            {nextRadius && (
              <button type="button" onClick={() => setPref('radius', nextRadius)} data-testid="expand-radius" className="bg-sky-600 text-white rounded-lg px-3 py-1.5">
                {t('discovery.expand', 'Search within {{km}} km', { km: nextRadius })}
              </button>
            )}
            <button type="button" onClick={() => sc.setChanging(true)} className="text-sky-700 dark:text-sky-400 underline">{t('discovery.change_location', 'Change location')}</button>
          </div>
        </>
      )}
    </div>
  )

  return (
    <Card title={t('nearby.card_title')} icon="📍" iconColor="bg-sky-50 text-sky-600 dark:bg-sky-900/30 dark:text-sky-300"
      actions={center && (
        <button type="button" onClick={() => { if (gps) sc.loc.request(); refresh() }} disabled={loading} data-testid="nearby-refresh"
          className="text-xs font-semibold text-sky-700 dark:text-sky-300 disabled:opacity-50">
          {loading ? '…' : '↻'} {t('discovery.refresh', 'Refresh')}
        </button>
      )}>
      <a href={`tel:${NATIONAL_EMERGENCY_NUMBER}`} data-testid="emergency-call"
        className="flex items-center justify-center gap-2 text-sm font-bold text-white bg-red-600 hover:bg-red-700 rounded-xl py-2 mb-3">
        📞 {t('nearby.call_emergency', 'Emergency: call {{number}}', { number: NATIONAL_EMERGENCY_NUMBER })}
      </a>

      <SearchCenterPanel sc={sc} areaName={meta.region?.name || Object.values(byCat).find((c) => c.meta?.region)?.meta.region.name}
        needText={t('nearby.need_location', 'Share your location to find hospitals, pharmacies, police and transport around you — or search for a place below.')} />

      {!online && (
        <div role="alert" data-testid="nearby-offline" className={`${noteCls} bg-orange-50 dark:bg-orange-900/20 text-orange-800 dark:text-orange-200`}>
          📡 {t('nearby.offline', 'You are offline. Connect to the internet to find places near you.')}
        </div>
      )}

      {center && (
        <>
          <div className="grid grid-cols-2 gap-2 mb-3">
            <button type="button" onClick={() => findNearest('hospital')} data-testid="nearest-hospital"
              className="text-xs font-bold bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-900/50 rounded-xl py-2">
              🏥 {t('nearby.nearest_hospital', 'Nearest hospital')}
            </button>
            <button type="button" onClick={() => findNearest('police')} data-testid="nearest-police"
              className="text-xs font-bold bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-900/50 rounded-xl py-2">
              👮 {t('nearby.nearest_police', 'Nearest police')}
            </button>
          </div>

          <div className="flex gap-1.5 mb-2 overflow-x-auto" role="group" aria-label={t('discovery.categories', 'Categories')}>
            {NEARBY_CATEGORIES.map((c) => (
              <button key={c} type="button" onClick={() => setCategory(c)} aria-pressed={category === c} data-testid={`cat-${c}`} className={chip(category === c)}>
                {CATEGORY_META[c].icon} {label(c)}{counts(c) != null ? ` (${counts(c)})` : ''}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-1.5 mb-2">
            <span className="text-[11px] text-slate-500">{t('discovery.within', 'Within')}</span>
            {RADII.map((r) => (
              <button key={r} type="button" onClick={() => setPref('radius', r)} aria-pressed={prefs.radius === r} data-testid={`radius-${r}`} className={chip(prefs.radius === r)}>{r} km</button>
            ))}
          </div>

          <input value={query} onChange={(e) => setQuery(e.target.value)} type="search" data-testid="nearby-search"
            placeholder={t('nearby.search_ph', 'Search these results by name or type')} aria-label={t('nearby.search_label', 'Search nearby results')}
            className="w-full text-sm border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2.5 py-1.5 mb-2" />

          <div className="flex items-center justify-between gap-2 mb-2">
            <label className="text-[11px] text-slate-500 flex items-center gap-1.5">
              {t('discovery.sort', 'Sort')}
              <select value={effectiveSort} onChange={(e) => setSort(e.target.value)} data-testid="nearby-sort"
                className="text-xs border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-1.5 py-1">
                <option value="nearest">{t('discovery.sort_nearest', 'Nearest first')}</option>
                {category === 'hospital' && <option value="relevance">{t('nearby.sort_emergency', 'Emergency first')}</option>}
                <option value="name">{t('discovery.sort_name', 'Name A–Z')}</option>
              </select>
            </label>
            <div className="flex rounded-lg overflow-hidden border border-slate-200 dark:border-slate-600" role="group" aria-label={t('discovery.view', 'View')}>
              {['list', 'map'].map((v) => (
                <button key={v} type="button" onClick={() => setPref('view', v)} aria-pressed={prefs.view === v} data-testid={`view-${v}`}
                  className={`text-xs font-semibold px-3 py-1 ${prefs.view === v ? 'bg-sky-600 text-white' : 'bg-white dark:bg-slate-700 text-slate-600 dark:text-slate-200'}`}>
                  {v === 'list' ? `☰ ${t('discovery.view_list', 'List')}` : `🗺️ ${t('discovery.view_map', 'Map')}`}
                </button>
              ))}
            </div>
          </div>

          {(category === 'pharmacy' || category === 'hospital') && (
            gps ? (
              <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300 mb-3">
                <input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} data-testid="open-only" />
                {t('nearby.open_only', 'Only places confirmed open now')}
              </label>
            ) : (
              <p className="text-[11px] text-slate-500 mb-3" data-testid="open-unknown-note">
                {t('nearby.open_needs_gps', 'Open-now status is only shown when searching around your live location.')}
              </p>
            )
          )}

          {meta.provider === 'saved' && cur.status === 'ok' && (
            <div role="alert" data-testid="nearby-saved" className={`${noteCls} bg-amber-50 dark:bg-amber-900/20 text-amber-900 dark:text-amber-200`}>
              {t('nearby.saved', 'The live map services could not be reached. Showing a saved copy of OpenStreetMap data — it can be out of date and has no phone numbers. Use Directions, or call the emergency number.')}
            </div>
          )}
          {meta.degraded && meta.provider === 'nominatim' && cur.status === 'ok' && (
            <div data-testid="nearby-degraded" className={`${noteCls} bg-amber-50 dark:bg-amber-900/20 text-amber-900 dark:text-amber-200`}>
              {t('nearby.degraded', 'The main map-data service is busy, so these come from a backup service. Some places may be missing.')}
            </div>
          )}
          {meta.stale && meta.provider !== 'saved' && (
            <div data-testid="nearby-stale" className={`${noteCls} bg-amber-50 dark:bg-amber-900/20 text-amber-900 dark:text-amber-200`}>
              {t('nearby.stale', 'The map services could not be reached, so this is an earlier saved result.')}
            </div>
          )}

          {errorBox}
          {skeleton}
          {cur.status !== 'error' && !skeleton && (
            prefs.view === 'map' ? (
              <>
                <DiscoveryMap center={center} places={visible} selectedId={selectedId} onSelect={setSelectedId}
                  onDirections={directions} styles={NEARBY_STYLES} routePath={selectedRoute} />
                {empty}
              </>
            ) : (
              <>
                <div className="space-y-2" data-testid="nearby-list">
                  {visible.slice(0, limit).map((p) => (
                    <NearbyPlaceCard key={p.id} place={p} nearest={p.id === nearestId} selected={p.id === selectedId} route={routes[p.id]}
                      onRoute={doRoute} onDirections={directions} onShowOnMap={showOnMap} />
                  ))}
                </div>
                {visible.length > limit && (
                  <button type="button" onClick={() => setLimit((l) => l + PAGE)} data-testid="nearby-more" className="w-full mt-2 text-xs font-semibold text-sky-700 dark:text-sky-400 py-2">
                    {t('discovery.show_more', 'Show more ({{n}} left)', { n: visible.length - limit })}
                  </button>
                )}
                {empty}
              </>
            )
          )}

          {cur.status === 'ok' && visible.length > 0 && (
            <p data-testid="nearby-count" className="text-[11px] text-slate-500 mt-2">
              {t('nearby.count', '{{n}} found within {{km}} km', { n: visible.length, km: prefs.radius })}
            </p>
          )}
          {category === 'transport' && cur.status === 'ok' && (
            <p data-testid="transport-note" className="text-[11px] text-slate-500 dark:text-slate-400 mt-2">
              🚌 {t('nearby.transport_note', 'Stops and stations are shown where they are mapped. Timetables and live running status are not available here — check with the operator.')}
            </p>
          )}
          <p className="text-[10px] text-slate-400 mt-3">
            {t('nearby.attribution', 'Places: © OpenStreetMap contributors (Overpass / Nominatim). Phone numbers and opening hours are shown only where OpenStreetMap lists them and can be out of date; distances are in a straight line.')}
          </p>
        </>
      )}
    </Card>
  )
}
