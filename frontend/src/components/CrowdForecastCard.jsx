import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import SearchCenterPanel from './SearchCenterPanel.jsx'
import useOnlineStatus from '../hooks/useOnlineStatus.js'
import useSearchCenter from '../hooks/useSearchCenter.js'
import { reasonOf } from '../hooks/useDiscovery.js'
import { getCrowdOutlook } from '../lib/crowdForecastService.js'
import { deviceTzOffsetMin } from '../lib/nearbyService.js'

const BAND = {
  quiet: { bar: 'bg-emerald-500', pill: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300' },
  moderate: { bar: 'bg-amber-400', pill: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300' },
  busy: { bar: 'bg-orange-500', pill: 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300' },
  very_busy: { bar: 'bg-red-500', pill: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300' },
}
const BAND_LABEL = { quiet: 'Quiet', moderate: 'Moderate', busy: 'Busy', very_busy: 'Very busy' }
const AREA_RETRY_MS = 10_000
const AREA_RETRIES = 3

// Crowd outlook for the tourist's own area. There is no free source of live crowd counts, so
// this is an ESTIMATE and is labelled as one on screen: it is built from the real local time,
// hourly weather, public holidays and what OpenStreetMap shows within 1 km of where the tourist
// is (backend/app/services/crowd_outlook.py), and every factor that moved it is listed by name.
export default function CrowdForecastCard() {
  const { t } = useTranslation()
  const online = useOnlineStatus()
  const sc = useSearchCenter()
  const { center, gps } = sc
  const [state, setState] = useState({ status: 'idle', data: null, error: null })
  const [nonce, setNonce] = useState(0)
  const tz = useMemo(() => (gps ? deviceTzOffsetMin() : undefined), [gps])
  const lat = center?.lat
  const lng = center?.lng

  useEffect(() => {
    if (lat == null || lng == null) { setState({ status: 'idle', data: null, error: null }); return undefined }
    const controller = new AbortController()
    let timer
    let tries = 0
    setState((s) => ({ status: 'loading', data: s.data, error: null }))
    const load = () => getCrowdOutlook(lat, lng, { tzOffsetMin: tz, signal: controller.signal })
      .then((data) => {
        setState({ status: 'ok', data, error: null })
        // The surroundings lookup can still be running on the server: ask again shortly.
        if (data.inputs.area_pending && tries < AREA_RETRIES) { tries += 1; timer = setTimeout(load, AREA_RETRY_MS) }
      })
      .catch((e) => {
        if (controller.signal.aborted || e?.code === 'ERR_CANCELED') return
        setState((s) => ({ status: 'error', data: null, error: reasonOf(e), prev: s.data }))
      })
    load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [lat, lng, tz, nonce])

  const refresh = useCallback(() => { if (gps) sc.loc.request(); setNonce((n) => n + 1) }, [gps, sc.loc])
  const { status, data, error } = state
  const loading = status === 'loading'
  const dayWord = (off) => (off === 0 ? '' : off === 1 ? ` ${t('crowd.tomorrow', 'tomorrow')}` : '')

  return (
    <Card title={t('crowd_forecast.card_title')} icon="👥" iconColor="bg-orange-50 text-orange-600 dark:bg-orange-900/30 dark:text-orange-300"
      actions={center && (
        <button type="button" onClick={refresh} disabled={loading} data-testid="crowd-refresh"
          className="text-xs font-semibold text-orange-700 dark:text-orange-300 disabled:opacity-50">
          {loading ? '…' : '↻'} {t('discovery.refresh', 'Refresh')}
        </button>
      )}>
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">{t('crowd_forecast.intro')}</p>
      <SearchCenterPanel sc={sc} areaName={data?.region?.name}
        needText={t('crowd.need_location', 'Share your location to see how busy it is around you — or search for a place below.')} />

      {!online && (
        <div role="alert" data-testid="crowd-offline" className="text-xs rounded-lg px-2.5 py-2 mb-2 bg-orange-50 dark:bg-orange-900/20 text-orange-800 dark:text-orange-200">
          📡 {t('crowd.offline', 'You are offline. Connect to the internet to see the crowd outlook.')}
        </div>
      )}

      {center && status === 'error' && (
        <div role="alert" data-testid="crowd-error" className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-900/20 p-3 text-sm text-red-800 dark:text-red-200">
          {error === 'offline'
            ? t('crowd.err_offline', 'Could not reach the server. Check your internet connection.')
            : error === 'unavailable'
              ? t('crowd.err_unavailable', 'The crowd outlook could not be worked out right now (the weather or time service did not answer). Please try again in a moment.')
              : t('crowd_forecast.failed')}
          <div className="mt-2">
            <button type="button" onClick={() => setNonce((n) => n + 1)} data-testid="crowd-retry"
              className="text-xs font-semibold bg-red-600 text-white rounded-lg px-3 py-1.5">{t('discovery.retry', 'Try again')}</button>
          </div>
        </div>
      )}

      {center && loading && !data && (
        <div className="animate-pulse space-y-2" data-testid="crowd-skeleton" aria-busy="true">
          <div className="h-10 bg-slate-200 dark:bg-slate-700 rounded-xl" />
          <div className="h-24 bg-slate-100 dark:bg-slate-700/60 rounded-xl" />
        </div>
      )}

      {center && data && status !== 'error' && (
        <div className="space-y-3" data-testid="crowd-result">
          <div className="flex items-center justify-between gap-2 rounded-xl border border-slate-100 dark:border-slate-700 p-3">
            <div>
              <div className="text-[11px] text-slate-500">{t('crowd.right_now', 'Right now (estimate)')}</div>
              <span data-testid="crowd-now" data-band={data.now.band}
                className={`inline-block mt-0.5 text-sm font-bold rounded-full px-3 py-1 ${BAND[data.now.band].pill}`}>
                {t(`crowd.band_${data.now.band}`, BAND_LABEL[data.now.band])}
              </span>
            </div>
            {data.best_time && (
              <div className="text-right">
                <div className="text-[11px] text-slate-500">{t('crowd.best_time', 'Quietest daytime hour')}</div>
                <div data-testid="crowd-best" className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                  {data.best_time.hour}{dayWord(data.best_time.day_offset)}
                </div>
              </div>
            )}
          </div>

          <div>
            <div className="text-[11px] font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('crowd.next_hours', 'Next 12 hours')}</div>
            <div className="flex items-end gap-1 h-24" role="img" data-testid="crowd-chart"
              aria-label={t('crowd.chart_aria', 'Estimated crowd level for each of the next 12 hours')}>
              {data.hours.map((h, i) => (
                <div key={`${h.hour}-${i}`} className="flex-1 flex flex-col items-center justify-end h-full" data-testid="crowd-bar" data-band={h.band}
                  title={`${h.hour} — ${t(`crowd.band_${h.band}`, BAND_LABEL[h.band])}`}>
                  <div className={`w-full rounded-t ${BAND[h.band].bar}`} style={{ height: `${Math.max(4, h.score)}%` }} />
                </div>
              ))}
            </div>
            <div className="flex gap-1 mt-1">
              {data.hours.map((h, i) => (
                <div key={`l${h.hour}-${i}`} className="flex-1 text-center text-[9px] text-slate-400">{i % 3 === 0 ? h.hour.slice(0, 2) : ''}</div>
              ))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-2 text-[10px] text-slate-500">
              {Object.keys(BAND).map((b) => (
                <span key={b} className="flex items-center gap-1"><i className={`inline-block w-2.5 h-2.5 rounded-sm ${BAND[b].bar}`} />{t(`crowd.band_${b}`, BAND_LABEL[b])}</span>
              ))}
            </div>
          </div>

          {data.factors.length > 0 && (
            <ul className="space-y-1" data-testid="crowd-factors">
              {data.factors.map((f) => (
                <li key={f.text} className="text-xs text-slate-600 dark:text-slate-300 flex gap-1.5">
                  <span aria-hidden="true" className={f.effect === 'up' ? 'text-red-500' : f.effect === 'down' ? 'text-emerald-600' : 'text-slate-400'}>
                    {f.effect === 'up' ? '▲' : f.effect === 'down' ? '▼' : '●'}
                  </span>
                  {f.text}
                </li>
              ))}
            </ul>
          )}

          <div className="space-y-1 text-[11px] text-slate-500 dark:text-slate-400">
            {data.inputs.area_pending && <p data-testid="crowd-area-pending">{t('crowd.area_pending', 'Still checking what is around you — this estimate will update.')}</p>}
            {!data.inputs.area && !data.inputs.area_pending && <p>{t('crowd.no_area', 'What is around you could not be looked up, so the area is not factored in.')}</p>}
            {!data.inputs.weather && <p data-testid="crowd-no-weather">{t('crowd.no_weather', 'Weather data was unavailable, so weather is not factored in.')}</p>}
            {!data.inputs.holidays && <p data-testid="crowd-no-holidays">{t('crowd.no_holidays', 'Public-holiday data is not available here, so holidays are not factored in.')}</p>}
          </div>
          <p data-testid="crowd-disclaimer" className="text-[11px] italic text-slate-500 dark:text-slate-400">
            {t('crowd.disclaimer', 'This is an estimate, not a live count of people — no free source of live crowd data exists. It combines the time of day, weekday, public holidays, weather and what is mapped within 1 km of here.')}
          </p>
        </div>
      )}
    </Card>
  )
}
