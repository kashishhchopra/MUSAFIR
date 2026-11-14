import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import '../lib/fallDetectionStrings'

// Full-screen "Are you OK?" countdown + the outcome of it. Purely presentational:
// every value (phase, seconds left, event) comes from useFallDetection, which
// lives above this component, so re-rendering it can never restart the timer.
export default function FallCountdownModal({ fall }) {
  const { t } = useTranslation()
  const { phase, event, remaining, offlineRetry } = fall
  const open = phase !== 'idle'
  const primaryRef = useRef(null)

  // Move focus to the safest action when the alert opens (screen readers + keyboard).
  useEffect(() => {
    if (open) primaryRef.current?.focus()
  }, [open, phase])

  if (!open || !event) return null

  const type = event.event_type === 'crash' ? 'crash' : 'fall'
  const live = phase === 'countdown' || phase === 'sending' || phase === 'cancelling'
  const sourceKey = event.simulated ? 'from_simulation' : event.source === 'wearable' ? 'from_wearable' : 'from_phone'
  const total = fall.config?.countdown_seconds || 1
  const pct = Math.max(0, Math.min(100, (remaining / total) * 100))

  const shell = live
    ? 'bg-red-700 text-white'
    : phase === 'cancelled' ? 'bg-emerald-700 text-white'
    : phase === 'error' ? 'bg-slate-800 text-white'
    : 'bg-red-800 text-white'

  return (
    <div className="fixed inset-0 z-[2000] flex items-center justify-center p-4 bg-black/70"
      role="alertdialog" aria-modal="true" aria-labelledby="fall-title" aria-describedby="fall-desc"
      data-testid="fall-modal" data-phase={phase}>
      <div className={`w-full max-w-sm rounded-3xl shadow-2xl p-6 text-center space-y-4 ${shell}`}>
        {event.simulated && (
          <div className="text-xs font-bold tracking-wide bg-amber-300 text-amber-950 rounded-full px-3 py-1 inline-block"
            data-testid="fall-sim-badge">
            {t('fall_detection.from_simulation')}
          </div>
        )}

        {live && (
          <>
            <div className="text-5xl" aria-hidden="true">{type === 'crash' ? '💥' : '⚠️'}</div>
            <h2 id="fall-title" className="text-2xl font-extrabold">
              {t(`fall_detection.modal_title_${type}`)}
            </h2>
            <p id="fall-desc" className="text-red-50 text-sm">
              {t('fall_detection.modal_question')} {t('fall_detection.modal_explain')}
            </p>
            <div>
              <div className="text-8xl font-black tabular-nums leading-none"
                role="timer" aria-live="assertive" aria-atomic="true" data-testid="fall-remaining">
                {remaining}
              </div>
              <div className="text-xs uppercase tracking-widest text-red-100 mt-1">
                {t('fall_detection.seconds_left')}
              </div>
              <div className="h-2 bg-red-900/60 rounded-full mt-3 overflow-hidden" aria-hidden="true">
                <div className="h-full bg-white transition-[width] duration-300" style={{ width: `${pct}%` }} />
              </div>
            </div>
            <div className="text-xs text-red-100">{t(`fall_detection.${sourceKey}`)}</div>
            {event.has_location === false && (
              <div className="text-xs text-red-100">{t('fall_detection.no_location')}</div>
            )}
            {offlineRetry && (
              <div className="text-xs bg-red-900/60 rounded-lg px-3 py-2">{t('fall_detection.network_retrying')}</div>
            )}
            {phase === 'cancelling' && <div className="text-sm font-semibold">{t('fall_detection.cancelling')}</div>}
            {phase === 'sending' && <div className="text-sm font-semibold">{t('fall_detection.sending')}</div>}

            <div className="space-y-2 pt-1">
              <button ref={primaryRef} onClick={fall.cancel} disabled={phase === 'cancelling'}
                className="w-full bg-white text-emerald-800 hover:bg-emerald-50 disabled:opacity-60 font-extrabold text-lg py-4 rounded-2xl shadow">
                ✅ {t('fall_detection.cancel_button')}
              </button>
              <button onClick={fall.sosNow} disabled={phase === 'cancelling'}
                className="w-full bg-red-950/60 hover:bg-red-950 disabled:opacity-60 text-white font-bold py-3 rounded-2xl border border-red-300/40">
                🆘 {t('fall_detection.sos_button')}
              </button>
            </div>
          </>
        )}

        {!live && (
          <>
            <div className="text-5xl" aria-hidden="true">
              {phase === 'cancelled' ? '✅' : phase === 'error' ? '📡' : '🚨'}
            </div>
            <h2 id="fall-title" className="text-2xl font-extrabold">
              {t(`fall_detection.result_${phase === 'sos' ? 'sos' : phase}_title`)}
            </h2>
            <p id="fall-desc" className="text-sm opacity-90">
              {t(`fall_detection.result_${phase === 'sos' ? 'sos' : phase}_body`)}
            </p>
            {event.incident_id && (
              <div className="text-sm font-bold bg-black/25 rounded-lg px-3 py-2" data-testid="fall-incident">
                {t('fall_detection.result_confirmed_incident', { id: event.incident_id })}
              </div>
            )}
            <button ref={primaryRef} onClick={fall.dismiss}
              className="w-full bg-white/95 text-slate-900 font-bold py-3 rounded-2xl">
              {t('fall_detection.close')}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
