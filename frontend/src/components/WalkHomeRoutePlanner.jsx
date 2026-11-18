import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import PlaceSearch from './PlaceSearch.jsx'
import LocationStatus from './LocationStatus.jsx'
import WalkHomeMap from './WalkHomeMap.jsx'
import useCurrentLocation from '../hooks/useCurrentLocation.js'
import useJourneyRoute from '../hooks/useJourneyRoute.js'
import { listTravelModes, formatDuration } from '../lib/routeService.js'
import '../lib/walkHomeStrings'

const MODE_ICON = { walking: '🚶', bicycling: '🚲', driving: '🚗', transit: '🚌' }
const MODE_ORDER = ['walking', 'bicycling', 'driving', 'transit']
const chip = (on) => `px-3 py-1.5 rounded-full text-xs font-semibold border ${on
  ? 'bg-sky-600 text-white border-sky-600'
  : 'bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-slate-300 dark:border-slate-600'}`

// Deadline for a journey = routed travel time + a safety buffer (the larger of a
// percentage and a minimum, both set by the server), rounded up to whole minutes.
export function deadlineMinutes(travelMin, config) {
  const buffer = Math.max(travelMin * (config.route_buffer_percent ?? 25) / 100, config.route_buffer_min_minutes ?? 5)
  return { buffer: Math.ceil(buffer), total: Math.ceil(travelMin + buffer) }
}

// Reduce a long road polyline to at most `max` points for storage on the device.
export function thin(path, max = 400) {
  if (!Array.isArray(path) || path.length <= max) return path || null
  const step = (path.length - 1) / (max - 1)
  return Array.from({ length: max }, (_, i) => path[Math.round(i * step)])
}

// Destination search -> real route from the device's GPS position -> distance,
// travel time, arrival time and the suggested journey deadline. The tourist never
// guesses a duration: it is computed, and recomputed when the destination or the
// travel mode changes. `onPlan` receives the current state of all of that.
export default function WalkHomeRoutePlanner({ config, onPlan }) {
  const { t } = useTranslation()
  const loc = useCurrentLocation({ quick: true })
  const [dest, setDest] = useState(null)
  const [mode, setMode] = useState('walking')
  const [modes, setModes] = useState(['walking', 'bicycling', 'driving'])
  const [startPlace, setStartPlace] = useState(null) // chosen start, when GPS cannot give one
  // "Detect my location": drop any searched start and use a fresh device fix.
  const detect = () => { setStartPlace(null); loc.request() }
  const gpsFailed = ['denied', 'unavailable', 'timeout', 'unsupported'].includes(loc.status)
  // The route starts from a place the traveller chose, else from the live GPS fix.
  const here = startPlace || loc.position
  const { state, route, error, retry } = useJourneyRoute({ from: here, to: dest, mode })

  // Picking a destination is the user's own action, so asking for the device
  // location then is expected: the route needs a real start point.
  useEffect(() => { if (dest && loc.status === 'idle') loc.request() }, [dest]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { listTravelModes().then((m) => m.length && setModes(m)).catch(() => {}) }, [])
  const ordered = MODE_ORDER.filter((m) => modes.includes(m))

  const calc = useMemo(
    () => (route ? deadlineMinutes(route.duration_min, config) : null), [route, config])
  const tooLong = !!calc && calc.total > config.duration.max
  const minutes = calc ? Math.max(config.duration.min, calc.total) : null

  useEffect(() => {
    onPlan({ dest, mode, route, state, here, minutes: tooLong ? null : minutes, tooLong, buffer: calc?.buffer })
  }, [dest, mode, route, state, here, minutes, tooLong]) // eslint-disable-line react-hooks/exhaustive-deps

  const why = error === 'offline' ? t('walk_home.offline_action') : error === 'failed' ? '—' : error
  const arrival = minutes && !tooLong
    ? new Date(Date.now() + minutes * 60_000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null

  return (
    <div className="space-y-3">
      <div>
        <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">
          {t('walk_home.destination')}
        </label>
        <PlaceSearch autocomplete selected={dest} onSelect={setDest} near={here}
          placeholder={t('walk_home.dest_search_ph')} />
      </div>

      {dest && (
        <div className="space-y-2" data-testid="wh-start-picker">
          <label className="block text-xs font-semibold text-slate-600 dark:text-slate-300">
            {t('walk_home.start_from')}
          </label>
          {/* Two ways to say where you start: search a place, or detect where you are now. */}
          <PlaceSearch autocomplete selected={startPlace} onSelect={setStartPlace} icon="📍"
            label={t('walk_home.start_label')} placeholder={t('walk_home.start_search_ph')} />
          <button type="button" onClick={detect} disabled={loc.locating} data-testid="wh-detect"
            className="w-full text-xs font-semibold bg-sky-600 hover:bg-sky-700 disabled:opacity-60 text-white px-3 py-2 rounded-lg">
            {loc.locating ? t('location.locating', 'Getting your location…') : t('walk_home.detect_location')}
          </button>
          {!startPlace && <LocationStatus loc={loc} />}
        </div>
      )}

      {dest && (
        <>
          <div className="flex flex-wrap gap-2" role="group" aria-label={t('walk_home.route_title')}>
            {ordered.map((m) => (
              <button key={m} type="button" onClick={() => setMode(m)} aria-pressed={mode === m}
                data-testid={`wh-mode-${m}`} className={chip(mode === m)}>
                {MODE_ICON[m]} {t(`walk_home.mode_${m}`)}
              </button>
            ))}
          </div>

          {!here && gpsFailed && (
            <p className="text-xs text-amber-700 dark:text-amber-300" data-testid="wh-need-location">
              {t('walk_home.route_need_location')}
            </p>
          )}
          {state === 'loading' && (
            <p className="text-xs text-slate-500" role="status">{t('walk_home.route_loading')}</p>
          )}
          {state === 'error' && (
            <p className="text-xs text-red-600" role="alert" data-testid="wh-route-error">
              {t('walk_home.route_failed', { why })}{' '}
              <button type="button" onClick={retry} className="underline font-semibold">{t('walk_home.route_retry')}</button>
            </p>
          )}

          {route && (
            <div className="rounded-xl border border-slate-200 dark:border-slate-700 p-3 space-y-2" data-testid="wh-route">
              <dl className="text-sm grid grid-cols-2 gap-x-3 gap-y-1">
                <dt className="text-slate-500">{t('walk_home.route_distance')}</dt>
                <dd className="font-medium text-right" data-testid="wh-distance">{route.distance_km} km</dd>
                <dt className="text-slate-500">{t('walk_home.route_travel')}</dt>
                <dd className="font-medium text-right" data-testid="wh-travel">{formatDuration(route.duration_min)}</dd>
                <dt className="text-slate-500">{t('walk_home.route_buffer')}</dt>
                <dd className="font-medium text-right">+{calc.buffer} min</dd>
                <dt className="text-slate-500">{t('walk_home.route_deadline')}</dt>
                <dd className="font-bold text-right" data-testid="wh-deadline">{formatDuration(calc.total)}</dd>
                {arrival && (<>
                  <dt className="text-slate-500">{t('walk_home.route_arrive')}</dt>
                  <dd className="font-medium text-right">{arrival}</dd>
                </>)}
              </dl>
              {route.demo && (
                <p className="text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 rounded-lg p-2"
                  data-testid="wh-estimate-only">{t('walk_home.route_estimate_only')}</p>
              )}
              {tooLong && (
                <p className="text-xs text-red-600" role="alert" data-testid="wh-too-long">
                  {t('walk_home.route_too_long', { n: config.duration.max })}</p>
              )}
              <p className="text-[11px] text-slate-500 dark:text-slate-400">{t('walk_home.route_note')}</p>
            </div>
          )}

          <WalkHomeMap height={200} here={here ? [here.lat, here.lng] : null} dest={[dest.lat, dest.lng]}
            path={route && !route.demo ? route.geometry : null} />
        </>
      )}
      {!dest && <p className="text-xs text-slate-500">{t('walk_home.route_need_destination')}</p>}
    </div>
  )
}
