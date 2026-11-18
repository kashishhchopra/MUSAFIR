import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { LOCATION_STATUS as S } from '../hooks/useCurrentLocation.js'
import { openPlace } from '../lib/mapsLinks.js'

// Shows where the "current location" for a feature came from -- the device's
// real GPS fix, or exactly why there isn't one -- and lets the tourist grant
// permission / retry. Used by every feature that needs the tourist's real
// position, so permission wording and failure handling are identical everywhere.
// `loc` is a useCurrentLocation() result. With `autoIfGranted`, a permission
// the tourist already granted is used straight away (no tap); otherwise the
// permission prompt only ever appears after they tap the button.
export default function LocationStatus({ loc, autoIfGranted = true }) {
  const { t } = useTranslation()
  const { status, position, request } = loc

  useEffect(() => {
    if (!autoIfGranted || status !== S.IDLE || !navigator.permissions?.query) return
    navigator.permissions.query({ name: 'geolocation' })
      .then((p) => { if (p.state === 'granted') request() })
      .catch(() => {})
  }, [autoIfGranted, status, request])

  const retry = (
    <button type="button" onClick={request}
      className="mt-1 text-xs font-semibold text-sky-700 dark:text-sky-400 underline">
      {t('location.try_again', 'Try again')}
    </button>
  )

  if (status === S.OK && position) {
    return (
      <div className="text-xs bg-emerald-50 dark:bg-emerald-900/20 text-emerald-800 dark:text-emerald-200 rounded-lg p-2 flex items-center justify-between gap-2">
        <span>
          📍 {t('location.using_gps', 'Using your live location')}
          {' '}({position.lat.toFixed(4)}, {position.lng.toFixed(4)}
          {position.accuracy ? ` · ±${Math.round(position.accuracy)} m` : ''})
        </span>
        <span className="flex gap-3 shrink-0">
          <button type="button" onClick={request} className="font-semibold underline">{t('location.refresh', 'Refresh')}</button>
          <button type="button" onClick={() => openPlace(position)} className="font-semibold underline">{t('location.open_in_maps', 'Open in Maps')}</button>
        </span>
      </div>
    )
  }
  if (status === S.LOCATING) {
    return (
      <div className="text-xs text-slate-500 dark:text-slate-400 flex items-center gap-1.5" role="status">
        <span className="inline-block w-3 h-3 border-2 border-slate-300 border-t-sky-500 rounded-full animate-spin" />
        {t('location.locating', 'Getting your location…')}
      </div>
    )
  }
  if (status === S.IDLE) {
    return (
      <div className="text-xs bg-sky-50 dark:bg-sky-900/20 rounded-lg p-2">
        <div className="text-slate-600 dark:text-slate-300 mb-1.5">
          {t('location.why', 'MUSAFIR needs your current location to measure real distances from where you are. It is used only while you use this feature.')}
        </div>
        <button type="button" onClick={request}
          className="text-xs font-semibold bg-sky-600 hover:bg-sky-700 text-white px-3 py-1.5 rounded-lg">
          📍 {t('location.use_my_location', 'Use my current location')}
        </button>
      </div>
    )
  }
  const message = {
    [S.DENIED]: t('location.denied', 'Location permission is off. Allow location for MUSAFIR in your phone settings (Settings → Apps → MUSAFIR → Permissions → Location), then try again.'),
    [S.UNAVAILABLE]: t('location.unavailable', "Your location couldn't be determined. Turn on GPS / Location services and make sure you have a signal, then try again."),
    [S.TIMEOUT]: t('location.timeout', 'Finding your location took too long. Move somewhere with a clearer sky or signal and try again.'),
    [S.UNSUPPORTED]: t('location.unsupported', "This device can't provide a location."),
  }[status]
  return (
    <div className="text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 rounded-lg p-2" role="alert">
      ⚠ {message}
      {status !== S.UNSUPPORTED && <div>{retry}</div>}
    </div>
  )
}
