import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import LocationStatus from './LocationStatus.jsx'
import TripMap from './TripMap.jsx'
import useCurrentLocation, { LOCATION_STATUS } from '../hooks/useCurrentLocation.js'
import { getTrip, listTravelModes, formatDuration } from '../lib/routeService.js'
import { openDirections } from '../lib/mapsLinks.js'

const MODE_ICON = { driving: '🚗', walking: '🚶', bicycling: '🚲', transit: '🚌' }
const MODE_LABEL = { driving: 'Drive', walking: 'Walk', bicycling: 'Cycle', transit: 'Public transport' }

// Real travel information for an ordered list of stops: distance and time from
// the tourist's live GPS position to the first stop, and between consecutive
// stops, for the travel mode they pick -- from real road routing, with any
// straight-line estimate labelled as one. Plus a map of the journey, "navigate
// in Google Maps". Nothing is computed until the tourist
// taps Calculate, so opening a screen never triggers a location prompt or API call.
export default function TripTravelInfo({ stops }) {
  const { t } = useTranslation()
  const loc = useCurrentLocation()
  const [modes, setModes] = useState(['driving', 'walking', 'bicycling'])
  const [mode, setMode] = useState('driving')
  const [trip, setTrip] = useState(null) // {result, here, stops, mode, at}
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const located = useMemo(
    () => stops.filter((s) => s.lat != null && s.lng != null),
    [stops],
  )
  const signature = `${mode}|${located.map((s) => `${s.lat},${s.lng}`).join(';')}`

  useEffect(() => { listTravelModes().then((m) => m.length && setModes(m)).catch(() => {}) }, [])
  // Changing stops, order or mode makes the old numbers stale -- drop them.
  useEffect(() => { setTrip(null); setError('') }, [signature])

  const calculate = async () => {
    setError('')
    // Fresh fix; if the device can't give one we can still time stop-to-stop.
    const here = await loc.request()
    const points = [...(here ? [here] : []), ...located]
    if (points.length < 2) {
      setError(here
        ? t('trip.need_stop', 'Add at least one destination with a map location.')
        : t('trip.need_location_or_two', 'Allow your location, or add at least two stops, to calculate travel time.'))
      return
    }
    setLoading(true)
    try {
      const result = await getTrip(points, mode)
      setTrip({ result, here, stops: located, mode, at: Date.now() })
    } catch (err) {
      const detail = err.response?.data?.detail
      setError(typeof detail === 'string' ? detail : t('trip.failed', "Couldn't calculate travel times right now. Check your connection and try again."))
    } finally {
      setLoading(false)
    }
  }

  const labels = trip ? [...(trip.here ? [t('trip.you', 'You')] : []), ...trip.stops.map((s) => s.name)] : []
  const arrive = trip && trip.result.legs.length
    ? new Date(trip.at + trip.result.legs[0].duration_min * 60_000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : null

  const navigate = () => {
    const pts = trip.stops
    openDirections({
      origin: trip.here || undefined,
      destination: pts[pts.length - 1],
      stops: pts.slice(0, -1),
      mode: trip.mode,
    })
  }

  if (!located.length) return null
  return (
    <div className="space-y-2.5 text-sm">
      <div className="text-xs font-semibold text-slate-500 dark:text-slate-400">
        {t('trip.heading', 'Travel time & distance')}
      </div>
      <LocationStatus loc={loc} />

      <div className="flex flex-wrap gap-1.5" role="group" aria-label={t('trip.mode', 'Travel mode')}>
        {modes.map((m) => (
          <button key={m} type="button" onClick={() => setMode(m)} aria-pressed={mode === m}
            className={`text-xs px-2.5 py-1.5 rounded-full border ${mode === m
              ? 'bg-indigo-600 text-white border-indigo-600'
              : 'border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300'}`}>
            {MODE_ICON[m]} {t(`trip.mode_${m}`, MODE_LABEL[m])}
          </button>
        ))}
      </div>
      {!modes.includes('transit') && (
        <div className="text-[11px] text-slate-400">
          {t('trip.no_transit', 'Public-transport times need a Google Maps key on the server, so they are not shown.')}
        </div>
      )}

      <button type="button" onClick={calculate} disabled={loading || loc.status === LOCATION_STATUS.LOCATING}
        className="w-full text-sm font-semibold bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white py-2 rounded-lg">
        {loading ? t('trip.calculating', 'Calculating…') : t('trip.calculate', 'Calculate travel time')}
      </button>
      {error && <div className="text-xs text-red-600 dark:text-red-400" role="alert">{error}</div>}

      {trip && (
        <div className="space-y-2" data-testid="trip-result">
          <div className="rounded-lg bg-indigo-50 dark:bg-indigo-900/20 p-2.5">
            <div className="font-semibold text-slate-800 dark:text-slate-100">
              {trip.result.total_distance_km} km · {formatDuration(trip.result.total_duration_min)}
              <span className="font-normal text-xs text-slate-500 dark:text-slate-400"> {t('trip.total', 'total')} ({MODE_ICON[trip.mode]})</span>
            </div>
            {arrive && trip.here && (
              <div className="text-xs text-slate-500 dark:text-slate-400">
                {t('trip.arrive_first', 'If you leave now you reach {{name}} around {{time}}.', { name: trip.stops[0].name, time: arrive })}
              </div>
            )}
            {trip.result.demo && (
              <div className="text-[11px] text-amber-700 dark:text-amber-300 mt-1">
                {t('trip.estimate_note', 'Road routing was unavailable for some legs, so those are straight-line estimates — real travel will usually take longer.')}
              </div>
            )}
          </div>

          <ol className="space-y-1">
            {trip.result.legs.map((leg, i) => (
              <li key={i} className="text-xs flex justify-between gap-2 border-b border-slate-100 dark:border-slate-700 pb-1">
                <span className="min-w-0 truncate">{labels[i]} → {labels[i + 1]}</span>
                <span className="shrink-0 text-slate-500 dark:text-slate-400">
                  {leg.distance_km} km · {formatDuration(leg.duration_min)}{leg.demo ? ' ≈' : ''}
                </span>
              </li>
            ))}
          </ol>

          <TripMap here={trip.here} stops={trip.stops} lines={trip.result.legs.map((l) => l.geometry)} />

          <div className="flex gap-2">
            <button type="button" onClick={navigate}
              className="flex-1 text-xs font-semibold bg-sky-600 hover:bg-sky-700 text-white py-2 rounded-lg">
              🗺 {t('trip.navigate', 'Navigate in Google Maps')}
            </button>
          </div>

        </div>
      )}
    </div>
  )
}
