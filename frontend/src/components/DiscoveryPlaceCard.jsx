import { useTranslation } from 'react-i18next'

export const CATEGORY_ICON = { hidden_gem: '💎', regional_food: '🍲', homestay: '🏡' }
export const CATEGORY_COLOR = { hidden_gem: '#7c3aed', regional_food: '#ea580c', homestay: '#0d9488' }

export const formatKm = (km) => (km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`)

// One discovered place. Every line shown is something OpenStreetMap actually holds for
// it -- fields that are missing are simply not shown, never filled in.
export default function DiscoveryPlaceCard({ place: p, selected, onDirections, onShowOnMap }) {
  const { t } = useTranslation()
  return (
    <div data-testid="discovery-place" data-category={p.category} aria-current={selected ? 'true' : undefined}
      className={`border rounded-xl p-3 text-sm ${selected
        ? 'border-emerald-500 bg-emerald-50/50 dark:bg-emerald-900/10'
        : 'border-slate-100 dark:border-slate-700'}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-slate-800 dark:text-slate-100">{CATEGORY_ICON[p.category]} {p.name}</div>
          <div className="text-[11px] text-slate-500 dark:text-slate-400 mt-0.5">
            <span className="inline-block bg-slate-100 dark:bg-slate-700 rounded-full px-2 py-0.5 mr-1.5">{p.kind}</span>
            {p.address}
          </div>
        </div>
        <span data-testid="place-distance" className="shrink-0 text-xs font-semibold text-emerald-700 dark:text-emerald-400">
          {formatKm(p.distance_km)}
        </span>
      </div>

      {p.cuisine && (
        <div className="text-xs text-slate-600 dark:text-slate-300 mt-1.5">
          {t('discovery.cuisine', 'Cuisine')}: {p.cuisine}
          {p.regional_match && (
            <span data-testid="match-badge" className="ml-1.5 text-[10px] font-semibold bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300 rounded-full px-1.5 py-0.5">
              {p.regional_match === 'region'
                ? t('discovery.match_region', 'Local to this region')
                : t('discovery.match_country', 'Local cuisine')}
            </span>
          )}
        </div>
      )}
      {p.description && <div className="text-xs text-slate-600 dark:text-slate-300 mt-1">{p.description}</div>}
      {p.opening_hours && <div className="text-[11px] text-slate-500 mt-1">🕒 {p.opening_hours}</div>}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 mt-2">
        <button type="button" onClick={() => onDirections(p)} data-testid="place-directions"
          className="text-xs font-semibold bg-sky-600 hover:bg-sky-700 text-white px-2.5 py-1 rounded-lg">
          🧭 {t('discovery.directions', 'Directions')}
        </button>
        <button type="button" onClick={() => onShowOnMap(p)} data-testid="place-on-map"
          className="text-xs font-semibold text-sky-700 dark:text-sky-400">
          🗺️ {t('discovery.show_on_map', 'On map')}
        </button>
        {p.phone && (
          <a href={`tel:${p.phone.replace(/[^+\d]/g, '')}`} className="text-xs font-semibold text-sky-700 dark:text-sky-400">
            📞 {p.phone}
          </a>
        )}
        {p.website && (
          <a href={p.website} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold text-sky-700 dark:text-sky-400">
            🔗 {t('discovery.website', 'Website')}
          </a>
        )}
      </div>
    </div>
  )
}
