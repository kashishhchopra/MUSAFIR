import { useTranslation } from 'react-i18next'
import { formatKm } from './DiscoveryPlaceCard.jsx'
import { formatDuration } from '../lib/routeService'

export const NEARBY_STYLES = {
  hospital: { glyph: '🏥', color: '#dc2626' },
  pharmacy: { glyph: '💊', color: '#16a34a' },
  police: { glyph: '👮', color: '#2563eb' },
  transport: { glyph: '🚕', color: '#d97706' },
}
const telHref = (p) => `tel:${p.replace(/[^+\d]/g, '')}`
const MODE_LABEL = { walking: ['🚶', 'on foot'], driving: ['🚗', 'by car'] }

// One nearby facility. Everything shown is what OpenStreetMap holds for it: a phone button
// exists only when a number is mapped, "open now" only when the hours could be proven, and
// nothing about services, ratings or live timetables is ever filled in.
export default function NearbyPlaceCard({ place: p, nearest, selected, route, onRoute, onDirections, onShowOnMap }) {
  const { t } = useTranslation()
  const style = NEARBY_STYLES[p.category]

  const status = p.open_24h
    ? { text: t('nearby.open_24h', 'Open 24 hours'), cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' }
    : p.open_now === true
      ? { text: t('nearby.open_now', 'Open now'), cls: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' }
      : p.open_now === false
        ? { text: t('nearby.closed_now', 'Closed now'), cls: 'bg-slate-200 text-slate-700 dark:bg-slate-700 dark:text-slate-200' }
        : null

  return (
    <div data-testid="nearby-place" data-category={p.category} aria-current={selected ? 'true' : undefined}
      className={`border rounded-xl p-3 text-sm ${selected ? 'border-sky-500 bg-sky-50/50 dark:bg-sky-900/10' : 'border-slate-100 dark:border-slate-700'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          {nearest && (
            <span data-testid="nearest-badge" className="inline-block text-[10px] font-bold tracking-wide uppercase bg-sky-600 text-white rounded-full px-2 py-0.5 mb-1">
              {t('nearby.nearest', 'Nearest')}
            </span>
          )}
          <div className="font-semibold text-slate-800 dark:text-slate-100">{style.glyph} {p.name}</div>
          <div className="flex flex-wrap items-center gap-1 mt-1 text-[11px]">
            <span className="bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200 rounded-full px-2 py-0.5">{p.kind}</span>
            {p.emergency && <span data-testid="emergency-badge" className="bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300 font-semibold rounded-full px-2 py-0.5">{t('nearby.emergency', 'Emergency dept.')}</span>}
            {p.operator_type && <span className="bg-slate-100 dark:bg-slate-700 text-slate-700 dark:text-slate-200 rounded-full px-2 py-0.5">{p.operator_type}</span>}
            {status && <span data-testid="open-badge" className={`font-semibold rounded-full px-2 py-0.5 ${status.cls}`}>{status.text}</span>}
          </div>
        </div>
        <span data-testid="place-distance" className="shrink-0 text-xs font-semibold text-sky-700 dark:text-sky-400">{formatKm(p.distance_km)}</span>
      </div>

      {p.address && <div className="text-xs text-slate-500 dark:text-slate-400 mt-1.5">{p.address}</div>}
      {p.opening_hours && !p.open_24h && <div className="text-[11px] text-slate-500 mt-1">🕒 {p.opening_hours}</div>}
      {Object.keys(p.details || {}).length > 0 && (
        <div data-testid="transport-details" className="text-xs text-slate-600 dark:text-slate-300 mt-1.5 space-y-0.5">
          {p.details.operator && <div>{t('nearby.operator', 'Operator')}: {p.details.operator}</div>}
          {p.details.network && <div>{t('nearby.network', 'Network')}: {p.details.network}</div>}
          {p.details.lines && <div>{t('nearby.lines', 'Lines / routes')}: {p.details.lines.replace(/;/g, ', ')}</div>}
        </div>
      )}
      {p.source === 'saved_osm' && (
        <div className="text-[11px] text-amber-700 dark:text-amber-300 mt-1.5">{t('nearby.saved_copy', 'From a saved OpenStreetMap copy — may be out of date.')}</div>
      )}

      <div className="flex flex-wrap items-center gap-1.5 mt-2.5">
        {p.phone && (
          <a href={telHref(p.phone)} data-testid="place-call"
            className="text-xs font-bold bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-1.5 rounded-lg">
            📞 {t('nearby.call', 'Call')} {p.phone}
          </a>
        )}
        <button type="button" onClick={() => onDirections(p)} data-testid="place-directions"
          className="text-xs font-semibold bg-sky-600 hover:bg-sky-700 text-white px-2.5 py-1.5 rounded-lg">
          🧭 {t('nearby.directions', 'Directions')}
        </button>
        <button type="button" onClick={() => onRoute(p, route?.mode || (p.distance_km <= 2 ? 'walking' : 'driving'))} data-testid="place-route"
          className="text-xs font-semibold text-sky-700 dark:text-sky-400 px-1.5 py-1.5">
          ⏱ {t('nearby.route', 'Time & route')}
        </button>
        <button type="button" onClick={() => onShowOnMap(p)} data-testid="place-on-map" className="text-xs font-semibold text-sky-700 dark:text-sky-400 px-1.5 py-1.5">
          🗺️ {t('nearby.on_map', 'On map')}
        </button>
        {p.website && (
          <a href={p.website} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold text-sky-700 dark:text-sky-400 px-1.5 py-1.5">
            🔗 {t('nearby.website', 'Website')}
          </a>
        )}
      </div>
      {p.phones?.length > 1 && (
        <div className="text-[11px] text-slate-500 mt-1 flex flex-wrap gap-x-3">
          {p.phones.slice(1).map((n) => <a key={n} href={telHref(n)} className="text-sky-700 dark:text-sky-400 font-semibold">📞 {n}</a>)}
        </div>
      )}

      {route && (
        <div data-testid="route-result" className="mt-2 rounded-lg bg-slate-50 dark:bg-slate-900/40 px-2.5 py-2 text-xs">
          {route.status === 'loading' && <span className="text-slate-500">{t('nearby.route_loading', 'Calculating route…')}</span>}
          {route.status === 'error' && <span role="alert" className="text-red-600 dark:text-red-400">{t('nearby.route_failed', 'Could not calculate the route. Use Directions instead.')}</span>}
          {route.status === 'ok' && (
            <>
              <div className="font-semibold text-slate-800 dark:text-slate-100">
                ≈ {formatDuration(route.data.duration_min)} {MODE_LABEL[route.mode][1]} · {route.data.distance_km} km
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">
                {route.data.demo
                  ? t('nearby.route_estimate', 'Straight-line estimate (no road route was available) — the real trip will take longer.')
                  : t('nearby.route_road', 'By road/path from where the search is centred. Traffic is not included.')}
              </div>
            </>
          )}
          <div className="flex gap-2 mt-1.5">
            {['walking', 'driving'].map((m) => (
              <button key={m} type="button" onClick={() => onRoute(p, m)} aria-pressed={route.mode === m} data-testid={`route-mode-${m}`}
                className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${route.mode === m ? 'bg-sky-600 text-white' : 'bg-slate-200 dark:bg-slate-700 text-slate-700 dark:text-slate-200'}`}>
                {MODE_LABEL[m][0]} {m === 'walking' ? t('nearby.walk', 'Walk') : t('nearby.drive', 'Drive')}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
