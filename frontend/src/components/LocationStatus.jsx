import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LOCATION_STATUS as S } from '../hooks/useCurrentLocation.js'
import { openPlace } from '../lib/mapsLinks.js'

const SLOW_AFTER_MS = 4000

function ageText(position, t) {
  const m = Math.max(0, Math.round((Date.now() - position.timestamp) / 60000))
  if (m < 2) return t('location.just_now', 'a moment ago')
  if (m < 90) return t('location.minutes_ago', '{{n}} min ago', { n: m })
  return t('location.hours_ago', '{{n}} h ago', { n: Math.round(m / 60) })
}

// Shows where a feature's "current location" came from -- the device's live fix, the last known
// one while it refreshes, an approximation the tourist asked for -- or exactly why there is none,
// with what to do about it. Used by every feature that needs the tourist's real position, so the
// wording and failure handling are identical everywhere. `loc` is a useCurrentLocation() result.
// (The `autoIfGranted` prop is accepted for older callers; the shared store now starts by itself.)
// eslint-disable-next-line no-unused-vars
export default function LocationStatus({ loc, autoIfGranted = true }) {
  const { t } = useTranslation()
  const { status, position, request, requestApprox, permission, stale, source, refreshing } = loc
  const [slow, setSlow] = useState(false)
  const [busy, setBusy] = useState(false)

  // A wait that drags on is explained (and a way out is offered) instead of spinning silently.
  useEffect(() => {
    setSlow(false)
    if (status !== S.LOCATING) return undefined
    const id = setTimeout(() => setSlow(true), SLOW_AFTER_MS)
    return () => clearTimeout(id)
  }, [status])

  const approx = async () => { setBusy(true); try { await requestApprox() } finally { setBusy(false) } }
  const approxButton = requestApprox && (
    <button type="button" onClick={approx} disabled={busy} data-testid="use-approx-location"
      className="mt-1.5 text-xs font-semibold bg-slate-600 hover:bg-slate-700 disabled:opacity-60 text-white px-3 py-1.5 rounded-lg">
      🌐 {busy ? t('location.approx_busy', 'Finding…') : t('location.use_approx', 'Use approximate location instead')}
    </button>
  )
  const approxNote = (
    <div className="text-[11px] opacity-80 mt-0.5">
      {t('location.approx_note', 'Approximate = city-level, worked out from your internet connection (this sends your IP address to a free location service).')}
    </div>
  )
  const retry = (
    <button type="button" onClick={request} data-testid="location-retry"
      className="mt-1 text-xs font-semibold text-sky-700 dark:text-sky-400 underline">
      {t('location.try_again', 'Try again')}
    </button>
  )

  // ---- have a position
  if (status === S.OK && position) {
    if (source === 'ip') {
      return (
        <div className="text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-900 dark:text-amber-200 rounded-lg p-2" data-testid="location-approx">
          🌐 {t('location.using_approx', 'Using an approximate location')}{position.place ? ` (${position.place})` : ''} — {t('location.city_level', 'city-level only')}
          <div className="mt-1"><button type="button" onClick={request} className="font-semibold underline" data-testid="location-precise">
            {t('location.use_precise', 'Use my precise location')}
          </button></div>
        </div>
      )
    }
    if (stale) {
      return (
        <div className="text-xs bg-sky-50 dark:bg-sky-900/20 text-sky-900 dark:text-sky-200 rounded-lg p-2" data-testid="location-stale">
          <div>
            📍 {t('location.using_last_known', 'Using your last known location')} ({ageText(position, t)}){' '}
            {refreshing ? t('location.updating', '— updating…') : ''}
          </div>
          {!refreshing && (
            <div className="mt-1 flex gap-3 font-semibold">
              <button type="button" onClick={request} className="underline">{t('location.refresh', 'Refresh')}</button>
            </div>
          )}
        </div>
      )
    }
    return (
      <div className="text-xs bg-emerald-50 dark:bg-emerald-900/20 text-emerald-800 dark:text-emerald-200 rounded-lg p-2 flex items-center justify-between gap-2" data-testid="location-live">
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

  // ---- waiting
  if (status === S.LOCATING) {
    return (
      <div className="text-xs text-slate-500 dark:text-slate-400" role="status">
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 border-2 border-slate-300 border-t-sky-500 rounded-full animate-spin" />
          {t('location.locating', 'Getting your location…')}
        </div>
        {slow && (
          <div className="mt-1.5 rounded-lg bg-slate-100 dark:bg-slate-700/50 p-2" data-testid="location-slow">
            <div data-testid="location-slow-hint">
              {permission === 'prompt'
                ? t('location.slow_prompt', 'Waiting for your permission — click Allow in the pop-up near the browser’s address bar.')
                : permission === 'granted'
                  ? t('location.slow_granted', 'The browser is allowed to use your location, but your device has not answered yet. On a Mac: System Settings → Privacy & Security → Location Services → make sure your browser is on.')
                  : t('location.slow_generic', 'Your device is taking a while to answer. Check that location is allowed for this site.')}
            </div>
            {approxButton}
            {approxNote}
          </div>
        )}
      </div>
    )
  }

  // ---- idle
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

  // ---- no position, and why
  const message = {
    [S.DENIED]: t('location.denied', 'Location permission is off. Allow location for MUSAFIR in your browser or phone settings (on Android: Settings → Apps → MUSAFIR → Permissions → Location), then try again.'),
    [S.UNAVAILABLE]: t('location.unavailable', "Your location couldn't be determined. Turn on GPS / Location services and make sure you have a signal, then try again."),
    [S.TIMEOUT]: t('location.timeout', 'Finding your location took too long. Move somewhere with a clearer sky or signal and try again.'),
    [S.UNSUPPORTED]: t('location.unsupported', "This device can't provide a location."),
  }[status]
  return (
    <div className="text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200 rounded-lg p-2" role="alert">
      ⚠ {message}
      {status === S.TIMEOUT && permission === 'granted' && (
        <div className="mt-1" data-testid="location-os-hint">
          {t('location.slow_granted', 'The browser is allowed to use your location, but your device has not answered yet. On a Mac: System Settings → Privacy & Security → Location Services → make sure your browser is on.')}
        </div>
      )}
      {status !== S.UNSUPPORTED && <div>{retry}</div>}
      {status !== S.DENIED && approxButton}
      {status !== S.DENIED && approxNote}
    </div>
  )
}
