import { useTranslation } from 'react-i18next'
import PlaceSearch from './PlaceSearch.jsx'
import LocationStatus from './LocationStatus.jsx'

// "You are near <area>" + Change / Use my location, or -- when there is no position yet --
// the precise reason (permission refused, GPS off, timed out...) and a place search so the
// tourist is never stuck. `sc` is a useSearchCenter() result; `areaName` is the reverse-
// geocoded name of the centre when known.
export default function SearchCenterPanel({ sc, areaName, needText }) {
  const { t } = useTranslation()
  const { center, manual, changing } = sc
  const shortName = manual ? manual.name.split(', ').slice(0, 2).join(', ') : areaName
  return (
    <div className="space-y-2 mb-3" data-testid="discovery-location">
      {center ? (
        <div className="flex items-center justify-between gap-2 text-xs bg-emerald-50 dark:bg-emerald-900/20 text-emerald-900 dark:text-emerald-200 rounded-lg px-2.5 py-2">
          <span className="min-w-0 truncate" data-testid="discovery-area">
            📍 {manual ? t('discovery.searching_near', 'Searching near') : t('discovery.you_are_near', 'You are near')}{' '}
            <strong>{shortName || `${center.lat.toFixed(3)}, ${center.lng.toFixed(3)}`}</strong>
          </span>
          <span className="flex gap-3 shrink-0 font-semibold">
            {manual && <button type="button" onClick={sc.useMyLocation} data-testid="use-my-location" className="underline">{t('discovery.use_my_location', 'Use my location')}</button>}
            <button type="button" onClick={() => sc.setChanging((c) => !c)} data-testid="change-location" className="underline">{t('discovery.change', 'Change')}</button>
          </span>
        </div>
      ) : (
        <>
          <p className="text-xs text-slate-600 dark:text-slate-300">
            {needText || t('discovery.need_location', 'Share your location to see places around you — or search for a city or area below.')}
          </p>
          <LocationStatus loc={sc.loc} autoIfGranted={false} />
        </>
      )}
      {(!center || changing) && (
        <PlaceSearch autocomplete selected={null} icon="📍" near={center} onSelect={sc.choose}
          placeholder={t('discovery.search_location_ph', 'Search a city or area…')} />
      )}
    </div>
  )
}
