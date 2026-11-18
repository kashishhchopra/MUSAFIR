import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import LocationStatus from './LocationStatus.jsx'
import PlaceSearch from './PlaceSearch.jsx'
import useCurrentLocation, { LOCATION_STATUS } from '../hooks/useCurrentLocation.js'
import { formatDuration } from '../lib/routeService.js'
import { openDirections } from '../lib/mapsLinks.js'
import {
  listTransportTypes, createFareCheck, listFareChecks, reportFare,
} from '../lib/fareService.js'

const TRANSPORT_ICON = { auto_rickshaw: '🛺', taxi: '🚕', cab: '🚗', bike_taxi: '🏍️' }
const VERDICT_CLS = {
  fair: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  slightly_high: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  overpriced: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  suspicious: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
}

// Fair Price: is the fare a local driver just quoted reasonable? Pickup is the
// tourist's REAL device GPS fix at the moment they check (never a stored or
// default position); the destination is a real place they search for and pick.
// Distance/time come from real road routing on the server, falling back to a
// straight-line estimate that is labelled as such, x a transparent rate card.
export default function FairPriceCard({ touristId }) {
  const { t } = useTranslation()
  const loc = useCurrentLocation()
  const [types, setTypes] = useState([])
  const [transportType, setTransportType] = useState('auto_rickshaw')
  const [destination, setDestination] = useState(null) // {name, lat, lng}
  const [quotedFare, setQuotedFare] = useState('')
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const [checks, setChecks] = useState([])
  const [reportingId, setReportingId] = useState(null)

  const load = () => {
    listTransportTypes().then(setTypes).catch(() => {})
    listFareChecks(touristId).then(setChecks).catch(() => {})
  }
  useEffect(load, [touristId])

  // A fare check needs where the tourist is NOW: a remembered or approximate position is not enough.
  const ready = loc.status === LOCATION_STATUS.OK && !loc.stale && loc.source === 'gps' && destination && Number(quotedFare) > 0

  const submit = async (e) => {
    e.preventDefault()
    if (!ready) return
    setChecking(true)
    setError('')
    try {
      // A fresh fix right now: the driver's quote is for where the tourist
      // actually is, not where they were when the card opened.
      const here = (await loc.request()) || loc.position
      if (!here) { setError(t('fair_price.need_location', 'We need your current location to check this fare.')); return }
      await createFareCheck(touristId, {
        transport_type: transportType, pickup_name: t('fair_price.current_location'),
        pickup_lat: here.lat, pickup_lng: here.lng,
        destination_name: destination.name.slice(0, 120),
        destination_lat: destination.lat, destination_lng: destination.lng,
        quoted_fare: Number(quotedFare),
      })
      setDestination(null)
      setQuotedFare('')
      load()
    } catch (err) {
      setError(err.response?.data?.detail && typeof err.response.data.detail === 'string'
        ? err.response.data.detail : t('fair_price.check_failed'))
    } finally {
      setChecking(false)
    }
  }

  const doReport = async (checkId) => {
    setReportingId(checkId)
    try {
      await reportFare(touristId, checkId)
      load()
    } finally {
      setReportingId(null)
    }
  }

  return (
    <Card title={t('fair_price.card_title')} icon="💰" iconColor="bg-amber-50 text-amber-600 dark:bg-amber-900/30 dark:text-amber-300">
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">{t('fair_price.intro')}</p>

      <div className="mb-3"><LocationStatus loc={loc} /></div>

      <form onSubmit={submit} className="flex flex-col gap-2 mb-3">
        <select value={transportType} onChange={(e) => setTransportType(e.target.value)}
          aria-label={t('fair_price.transport_label', 'Transport type')}
          className="text-sm border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2 py-1.5">
          {types.map((tt) => (
            <option key={tt} value={tt}>{TRANSPORT_ICON[tt]} {t(`fair_price.transport_${tt}`, tt.replace(/_/g, ' '))}</option>
          ))}
        </select>
        {/* Suggestions appear while typing, ranked towards where the tourist is; any place worldwide works. */}
        <PlaceSearch autocomplete selected={destination} onSelect={setDestination}
          near={loc.position ? { lat: loc.position.lat, lng: loc.position.lng } : null}
          placeholder={t('fair_price.destination_placeholder')} />
        <input type="number" min="1" step="1" value={quotedFare} onChange={(e) => setQuotedFare(e.target.value)}
          placeholder={t('fair_price.quoted_fare_placeholder')}
          className="text-sm border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2 py-1.5" />
        {error && <div className="text-sm text-red-600 dark:text-red-400" role="alert">{error}</div>}
        <button disabled={checking || !ready}
          className="text-sm font-semibold bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white py-2 rounded-lg">
          {checking ? t('fair_price.checking') : t('fair_price.check_button')}
        </button>
        {!ready && !checking && (
          <div className="text-[11px] text-slate-400">
            {t('fair_price.needs', 'To check a fare: allow your location, pick a destination and enter the quoted fare.')}
          </div>
        )}
      </form>

      <div className="space-y-2">
        {checks.length === 0 && <div className="text-xs text-slate-400">{t('fair_price.none_yet')}</div>}
        {checks.map((c) => (
          <div key={c.id} className="border border-slate-100 dark:border-slate-700 rounded-lg p-2.5 text-sm">
            <div className="flex items-center justify-between mb-1">
              <span className="font-medium">{TRANSPORT_ICON[c.transport_type]} {c.destination_name}</span>
              <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${VERDICT_CLS[c.verdict]}`}>
                {t(`fair_price.verdict_${c.verdict}`)}
              </span>
            </div>
            <div className="text-xs text-slate-500 dark:text-slate-400">
              {t('fair_price.estimated_range', { min: c.estimated_min, max: c.estimated_max })}
              {' · '}{t('fair_price.quoted', { amount: c.quoted_fare })}
              {c.percent_diff > 0 && ` · +${c.percent_diff}%`}
            </div>
            <div className="text-[11px] text-slate-400 mt-0.5">
              {c.distance_km} km · ~{formatDuration(c.duration_min)}
              {c.route_estimated && (
                <span className="ml-1 text-amber-600 dark:text-amber-400"
                  title={t('fair_price.estimated_tip', 'Road routing was unavailable, so this is the straight-line distance (real roads are usually longer).')}>
                  ({t('fair_price.straight_line', 'straight-line estimate')})
                </span>
              )}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button"
                onClick={() => openDirections({
                  origin: { lat: c.pickup_lat, lng: c.pickup_lng },
                  destination: { lat: c.destination_lat, lng: c.destination_lng },
                })}
                className="text-xs font-semibold bg-sky-50 dark:bg-sky-900/20 text-sky-700 dark:text-sky-300 px-3 py-1.5 rounded-lg">
                🗺 {t('fair_price.open_route', 'Open route in Maps')}
              </button>
              {c.verdict !== 'fair' && !c.reported && (
                <button onClick={() => doReport(c.id)} disabled={reportingId === c.id}
                  className="text-xs font-semibold bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 px-3 py-1.5 rounded-lg disabled:opacity-50">
                  {reportingId === c.id ? t('fair_price.reporting') : t('fair_price.report_button')}
                </button>
              )}
            </div>
            {c.reported && (
              <div className="mt-2 text-[11px] text-red-500 font-semibold">{t('fair_price.reported_notice')}</div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}
