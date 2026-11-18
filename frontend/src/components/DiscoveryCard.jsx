import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import SearchCenterPanel from './SearchCenterPanel.jsx'
import DiscoveryMap from './DiscoveryMap.jsx'
import DiscoveryPlaceCard, { CATEGORY_ICON } from './DiscoveryPlaceCard.jsx'
import useOnlineStatus from '../hooks/useOnlineStatus.js'
import useSearchCenter from '../hooks/useSearchCenter.js'
import useDiscovery, { DISCOVERY_CATEGORIES } from '../hooks/useDiscovery.js'
import { openDirections } from '../lib/mapsLinks.js'

const RADII = [2, 5, 10, 25, 50]
const PAGE = 30
const PREF_KEY = 'discovery.prefs'
const DEFAULT_PREFS = { radius: 10, sort: 'nearest', view: 'list' }

const readPrefs = () => {
  try {
    const p = JSON.parse(localStorage.getItem(PREF_KEY) || '{}')
    return {
      radius: RADII.includes(p.radius) ? p.radius : DEFAULT_PREFS.radius,
      sort: ['nearest', 'match', 'name'].includes(p.sort) ? p.sort : DEFAULT_PREFS.sort,
      view: p.view === 'map' ? 'map' : 'list',
    }
  } catch { return DEFAULT_PREFS }
}

const chip = (on) => `text-xs font-medium px-2.5 py-1.5 rounded-full whitespace-nowrap ${on
  ? 'bg-emerald-600 text-white'
  : 'bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200'}`

const MATCH_RANK = { region: 0, country: 1 }
const SORTERS = {
  nearest: (a, b) => a.distance_km - b.distance_km,
  match: (a, b) => (MATCH_RANK[a.regional_match] ?? 2) - (MATCH_RANK[b.regional_match] ?? 2) || a.distance_km - b.distance_km,
  name: (a, b) => a.name.localeCompare(b.name),
}

// Discovery: real hidden gems, regional food and homestays around the tourist, from
// OpenStreetMap (see backend/app/services/discovery.py). The position comes from the
// device's own GPS -- or a place the tourist types, when GPS is off or refused -- never
// from a stored or default coordinate. Whatever goes wrong (permission refused, GPS off,
// no connection, map services down) is said plainly; an empty list only ever means the
// map data genuinely has nothing there.
export default function DiscoveryCard() {
  const { t } = useTranslation()
  const online = useOnlineStatus()
  const sc = useSearchCenter()
  const { loc, gps, center } = sc
  const [prefs, setPrefs] = useState(readPrefs)
  const [category, setCategory] = useState('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [limit, setLimit] = useState(PAGE)

  const setPref = (k, v) => setPrefs((p) => {
    const next = { ...p, [k]: v }
    try { localStorage.setItem(PREF_KEY, JSON.stringify(next)) } catch { /* optional */ }
    return next
  })

  const d = useDiscovery({ center, radiusKm: prefs.radius })

  useEffect(() => { setLimit(PAGE) }, [category, query, prefs.sort, prefs.radius, center?.lat, center?.lng])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return d.places
      .filter((p) => category === 'all' || p.category === category)
      .filter((p) => !q || [p.name, p.kind, p.cuisine, p.address, p.description].some((x) => x && x.toLowerCase().includes(q)))
      .sort(SORTERS[prefs.sort])
  }, [d.places, category, query, prefs.sort])

  const label = (c) => t(`discovery.category_${c}`)
  const areaName = d.meta.region?.name
  const nextRadius = RADII.find((r) => r > prefs.radius)

  const directions = (p) => openDirections({
    origin: gps?.precise ? gps : undefined, // a typed location is not where the tourist is standing: let Maps use the live position
    destination: p, mode: p.distance_km <= 2 ? 'walking' : 'driving',
  })
  const showOnMap = (p) => { setPref('view', 'map'); setSelectedId(p.id) }
  const refresh = () => { if (gps) loc.request(); d.refresh() }

  const loading = d.status === 'loading'
  const total = d.places.length

  // ------------------------------------------------------------------ pieces
  const locationPanel = <SearchCenterPanel sc={sc} areaName={areaName} />

  const noticeClass = 'text-xs rounded-lg px-2.5 py-2 mb-2'
  const notices = (
    <>
      {!online && (
        <div role="alert" data-testid="discovery-offline" className={`${noticeClass} bg-orange-50 dark:bg-orange-900/20 text-orange-800 dark:text-orange-200`}>
          📡 {t('discovery.offline', 'You are offline. Connect to the internet to find places near you.')}
        </div>
      )}
      {d.meta.degraded && d.status === 'ok' && (
        <div data-testid="discovery-degraded" className={`${noticeClass} bg-amber-50 dark:bg-amber-900/20 text-amber-900 dark:text-amber-200`}>
          {t('discovery.degraded', 'The main map-data service is busy, so these come from a backup service. There may be fewer places than usual.')}
        </div>
      )}
      {d.meta.stale && (
        <div data-testid="discovery-stale" className={`${noticeClass} bg-amber-50 dark:bg-amber-900/20 text-amber-900 dark:text-amber-200`}>
          {t('discovery.stale', 'The map services could not be reached, so this is an earlier saved result.')}
        </div>
      )}
      {d.status === 'ok' && d.failed.length > 0 && (
        <div role="alert" data-testid="discovery-partial" className={`${noticeClass} bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-200`}>
          {t('discovery.partial', 'Could not load: {{what}}.', { what: d.failed.map(label).join(', ') })}{' '}
          <button type="button" onClick={d.retry} className="underline font-semibold">{t('discovery.retry', 'Try again')}</button>
        </div>
      )}
    </>
  )

  const errorBox = d.status === 'error' && (
    <div role="alert" data-testid="discovery-error" className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-800 dark:text-red-200">
      {d.error === 'offline'
        ? t('discovery.err_offline', 'Could not reach the server. Check your internet connection.')
        : d.error === 'unavailable'
          ? t('discovery.err_unavailable', 'The free map-data services are busy or unreachable right now, so places could not be loaded. This is not the same as "nothing nearby" — please try again in a minute.')
          : t('discovery.failed', "Couldn't load discovery places right now.")}
      <div className="mt-2">
        <button type="button" onClick={d.retry} data-testid="discovery-retry"
          className="text-xs font-semibold bg-red-600 text-white rounded-lg px-3 py-1.5">{t('discovery.retry', 'Try again')}</button>
      </div>
    </div>
  )

  const skeleton = loading && total === 0 && (
    <div className="space-y-2" data-testid="discovery-skeleton" aria-busy="true">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="animate-pulse border border-slate-100 dark:border-slate-700 rounded-xl p-3">
          <div className="h-3.5 w-2/3 bg-slate-200 dark:bg-slate-700 rounded" />
          <div className="h-2.5 w-1/2 bg-slate-100 dark:bg-slate-700/60 rounded mt-2" />
          <div className="h-2.5 w-1/3 bg-slate-100 dark:bg-slate-700/60 rounded mt-2" />
        </div>
      ))}
    </div>
  )

  const empty = d.status === 'ok' && visible.length === 0 && !(category !== 'all' && d.failed.includes(category)) && (
    <div data-testid="discovery-empty" className="text-center text-sm text-slate-600 dark:text-slate-300 py-6 px-2">
      <div className="text-3xl mb-2">🔎</div>
      {query.trim() ? (
        <>
          <p>{t('discovery.empty_search', 'No places match “{{q}}” in this list.', { q: query.trim() })}</p>
          <button type="button" onClick={() => setQuery('')} className="mt-2 text-xs font-semibold text-sky-700 dark:text-sky-400 underline">
            {t('discovery.clear_search', 'Clear search')}
          </button>
        </>
      ) : (
        <>
          <p>
            {category === 'all'
              ? t('discovery.empty_all', 'No places found within {{km}} km.', { km: prefs.radius })
              : t('discovery.empty_cat', 'No {{what}} found within {{km}} km.', { what: label(category).toLowerCase(), km: prefs.radius })}
          </p>
          <p className="text-xs text-slate-500 mt-1">
            {t('discovery.empty_hint', 'OpenStreetMap may have little data here. Try a wider radius or another location.')}
          </p>
          <div className="flex justify-center gap-3 mt-3 text-xs font-semibold">
            {nextRadius && (
              <button type="button" onClick={() => setPref('radius', nextRadius)} data-testid="expand-radius"
                className="bg-emerald-600 text-white rounded-lg px-3 py-1.5">
                {t('discovery.expand', 'Search within {{km}} km', { km: nextRadius })}
              </button>
            )}
            <button type="button" onClick={() => sc.setChanging(true)} className="text-sky-700 dark:text-sky-400 underline">
              {t('discovery.change_location', 'Change location')}
            </button>
          </div>
        </>
      )}
    </div>
  )

  return (
    <Card title={t('discovery.card_title')} icon="🧭" iconColor="bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300"
      actions={center && (
        <button type="button" onClick={refresh} disabled={loading} data-testid="discovery-refresh" aria-label={t('discovery.refresh', 'Refresh')}
          className="text-xs font-semibold text-emerald-700 dark:text-emerald-300 disabled:opacity-50">
          {loading ? '…' : '↻'} {t('discovery.refresh', 'Refresh')}
        </button>
      )}>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">{t('discovery.intro')}</p>
      {locationPanel}
      {notices}

      {center && (
        <>
          <input value={query} onChange={(e) => setQuery(e.target.value)} type="search" data-testid="discovery-search"
            placeholder={t('discovery.search_ph', 'Search these places — e.g. waterfall, bakery, Punjabi')}
            aria-label={t('discovery.search_label', 'Search places')}
            className="w-full text-sm border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2.5 py-1.5 mb-2" />

          <div className="flex flex-wrap gap-1.5 mb-2" role="group" aria-label={t('discovery.categories', 'Categories')}>
            <button type="button" onClick={() => setCategory('all')} aria-pressed={category === 'all'} data-testid="cat-all" className={chip(category === 'all')}>
              {t('discovery.all')}{d.loaded.length ? ` (${total})` : ''}
            </button>
            {DISCOVERY_CATEGORIES.map((c) => (
              <button key={c} type="button" onClick={() => setCategory(c)} aria-pressed={category === c} data-testid={`cat-${c}`} className={chip(category === c)}>
                {CATEGORY_ICON[c]} {label(c)}{d.loaded.includes(c) ? ` (${d.counts[c]})` : ''}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-1.5 mb-3">
            <span className="text-[11px] text-slate-500">{t('discovery.within', 'Within')}</span>
            {RADII.map((r) => (
              <button key={r} type="button" onClick={() => setPref('radius', r)} aria-pressed={prefs.radius === r} data-testid={`radius-${r}`}
                className={chip(prefs.radius === r)}>{r} km</button>
            ))}
          </div>
          <div className="flex items-center justify-between gap-2 mb-3">
            <label className="text-[11px] text-slate-500 flex items-center gap-1.5">
              {t('discovery.sort', 'Sort')}
              <select value={prefs.sort} onChange={(e) => setPref('sort', e.target.value)} data-testid="discovery-sort"
                className="text-xs border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-1.5 py-1">
                <option value="nearest">{t('discovery.sort_nearest', 'Nearest first')}</option>
                <option value="match">{t('discovery.sort_match', 'Best local match')}</option>
                <option value="name">{t('discovery.sort_name', 'Name A–Z')}</option>
              </select>
            </label>
            <div className="flex rounded-lg overflow-hidden border border-slate-200 dark:border-slate-600" role="group" aria-label={t('discovery.view', 'View')}>
              {['list', 'map'].map((v) => (
                <button key={v} type="button" onClick={() => setPref('view', v)} aria-pressed={prefs.view === v} data-testid={`view-${v}`}
                  className={`text-xs font-semibold px-3 py-1 ${prefs.view === v ? 'bg-emerald-600 text-white' : 'bg-white dark:bg-slate-700 text-slate-600 dark:text-slate-200'}`}>
                  {v === 'list' ? `☰ ${t('discovery.view_list', 'List')}` : `🗺️ ${t('discovery.view_map', 'Map')}`}
                </button>
              ))}
            </div>
          </div>

          {errorBox}
          {skeleton}
          {d.status !== 'error' && !skeleton && (
            prefs.view === 'map' ? (
              <>
                <DiscoveryMap center={center} places={visible} selectedId={selectedId} onSelect={setSelectedId} onDirections={directions} />
                {empty}
              </>
            ) : (
              <>
                <div className="space-y-2" data-testid="discovery-list">
                  {visible.slice(0, limit).map((p) => (
                    <DiscoveryPlaceCard key={p.id} place={p} selected={p.id === selectedId} onDirections={directions} onShowOnMap={showOnMap} />
                  ))}
                </div>
                {visible.length > limit && (
                  <button type="button" onClick={() => setLimit((l) => l + PAGE)} data-testid="discovery-more"
                    className="w-full mt-2 text-xs font-semibold text-sky-700 dark:text-sky-400 py-2">
                    {t('discovery.show_more', 'Show more ({{n}} left)', { n: visible.length - limit })}
                  </button>
                )}
                {empty}
              </>
            )
          )}
          {loading && total > 0 && <p className="text-[11px] text-slate-400 mt-2" role="status">{t('app.loading')}</p>}

          {(category === 'homestay' || (category === 'all' && d.counts.homestay > 0)) && d.status === 'ok' && (
            <p data-testid="stay-disclaimer" className="text-[11px] text-slate-500 dark:text-slate-400 mt-3">
              🏡 {t('discovery.stay_disclaimer', 'Rooms, prices and availability are not shown and not confirmed — contact the property directly to check and book.')}
            </p>
          )}
          <p className="text-[10px] text-slate-400 mt-3">
            {t('discovery.attribution', 'Places: © OpenStreetMap contributors (Overpass / Nominatim). Distances are in a straight line from the centre of the search.')}
          </p>
        </>
      )}
    </Card>
  )
}
