import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import useEmergencyTracking from '../hooks/useEmergencyTracking'
import { cancelSOS } from '../lib/emergencyLocationService'

// How long the "Emergency Resolved" confirmation stays up before the card
// disappears on its own.
export const RESOLVED_DISMISS_MS = 5000

// The tourist can close their own SOS, but only after three separate confirmations.
// Each screen's "continue" button stays disabled for this long after it appears, so a
// frantic triple-tap cannot pass for three considered answers.
export const CLOSE_STEP_DELAY_MS = 1200
export const CLOSE_STEPS = 3

const STATUS_META = {
  connecting: { labelKey: 'emergency_mode.status_connecting', dot: 'bg-slate-400', text: 'text-slate-300' },
  live: { labelKey: 'emergency_mode.status_live', dot: 'bg-emerald-400 animate-pulse', text: 'text-emerald-300' },
  stale: { labelKey: 'emergency_mode.status_stale', dot: 'bg-amber-400', text: 'text-amber-300' },
  offline: { labelKey: 'emergency_mode.status_offline', dot: 'bg-red-500', text: 'text-red-300' },
  stopped: { labelKey: 'emergency_mode.status_stopped', dot: 'bg-slate-500', text: 'text-slate-400' },
}

// The tourist's own "Emergency Mode" screen for an active SOS -- see
// backend/app/services/emergency_location.py for the real GPS -> backend ->
// police-dashboard pipeline this reflects. Deliberately never shown for a
// silent/duress SOS (sosSent.silent): a visible "Emergency Mode" banner on
// screen would defeat the entire point of a silent alert, even though the
// same real location sharing is still running underneath for police.
//
// The tourist can end their own emergency ("I'm safe") but only through three
// separate confirmations (POST /tourists/{id}/sos/cancel). The control room can still
// close any case, and a silent/duress SOS can ONLY be closed by the control room. They
// can press SOS again at any time; every press restarts sharing for an open case.
export default function EmergencyModeCard({ sosSent, tid, posRef }) {
  const { t } = useTranslation()
  const incidentId = sosSent?.incident_id
  // One key per SOS *press* (pressed_at, set by sendSOS). A repeat press inside the
  // server's merge window returns the same incident id, so keying on the id alone
  // would leave a stopped card stopped. Falls back to the id (e.g. fall detection).
  const pressKey = sosSent?.pressed_at ?? incidentId
  // The press whose "resolved" card has been shown and dismissed, so a later press
  // still gets its card.
  const [dismissedKey, setDismissedKey] = useState(null)
  const active = !!incidentId && !sosSent.silent && dismissedKey !== pressKey
  const startPos = posRef?.current ? { lat: posRef.current[0], lng: posRef.current[1] } : null
  const tracking = useEmergencyTracking({
    tid, incidentId, active, startPos, resetKey: pressKey,
  })
  const resolved = active && tracking.status === 'stopped'

  // Closing the SOS: 0 = not asking, 1..3 = which confirmation is on screen.
  const [closeStep, setCloseStep] = useState(0)
  const [armedStep, setArmedStep] = useState(0)
  const [closing, setClosing] = useState(false)
  const [closeFailed, setCloseFailed] = useState(false)

  // "Armed" belongs to the step it was earned on, so a fresh screen is never armed by a stale value.
  const armed = closeStep > 0 && armedStep === closeStep
  useEffect(() => {
    if (!closeStep) return undefined
    const timer = setTimeout(() => setArmedStep(closeStep), CLOSE_STEP_DELAY_MS)
    return () => clearTimeout(timer)
  }, [closeStep])

  // The case ended some other way (control room, another device): drop the dialog.
  useEffect(() => {
    if (resolved) setCloseStep(0)
  }, [resolved])

  const keepSOS = () => {
    setCloseStep(0)
    setArmedStep(0)
    setCloseFailed(false)
  }
  const confirmClose = async () => {
    setCloseFailed(false)
    if (closeStep < CLOSE_STEPS) {
      setCloseStep(closeStep + 1)
      return
    }
    setClosing(true)
    try {
      await cancelSOS(tid)
      setCloseStep(0)
      setArmedStep(0)
      tracking.markStopped()
    } catch {
      setCloseFailed(true) // the SOS is still active: say so, never pretend it closed
    } finally {
      setClosing(false)
    }
  }

  // Once resolved, show the confirmation briefly, then remove the card --
  // which also unmounts the tracking (GPS + send loop) for this incident.
  useEffect(() => {
    if (!resolved) return undefined
    const timer = setTimeout(() => setDismissedKey(pressKey), RESOLVED_DISMISS_MS)
    return () => clearTimeout(timer)
  }, [resolved, pressKey])

  if (!active) return null

  if (resolved) {
    return (
      <div className="fixed inset-x-4 bottom-[calc(10rem_+_var(--sysnav-h))] z-[1400] max-w-sm mx-auto bg-slate-800 text-white rounded-2xl shadow-xl p-4 text-sm">
        <div className="font-bold">{t('emergency_mode.resolved_title')}</div>
        <div className="text-slate-300 mt-1">{t('emergency_mode.resolved_body')}</div>
      </div>
    )
  }

  const meta = STATUS_META[tracking.status] || STATUS_META.connecting

  return (
    <>
    <div className="fixed inset-x-4 bottom-[calc(10rem_+_var(--sysnav-h))] z-[1400] max-w-sm mx-auto bg-red-700 text-white rounded-2xl shadow-2xl p-4 text-sm space-y-2.5">
      <div className="flex items-center justify-between">
        <div className="font-bold text-base">{t('emergency_mode.title')}</div>
        <span className={`flex items-center gap-1.5 text-xs font-bold ${meta.text}`}>
          <span className={`w-2 h-2 rounded-full ${meta.dot}`} /> {t(meta.labelKey)}
        </span>
      </div>

      <div className="text-red-50">
        {t('emergency_mode.sharing_notice')}
        {tracking.demo && <span className="block text-red-200 text-xs mt-0.5">{t('emergency_mode.demo_notice')}</span>}
      </div>

      {tracking.error && (
        <div className="bg-red-900/50 rounded-lg px-3 py-2 text-xs">
          {tracking.error === 'Connection interrupted' ? (
            <>{t('emergency_mode.connection_interrupted')}</>
          ) : (
            <>{t('emergency_mode.location_unavailable')}</>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-red-200">{t('emergency_mode.police_station')}</div>
          <div className="font-semibold">{sosSent.station_name || t('emergency_mode.assigning')}</div>
        </div>
        <div>
          <div className="text-red-200">{t('emergency_mode.response')}</div>
          <div className="font-semibold">{sosSent.nearest_unit ? t('emergency_mode.officer_assigned') : t('emergency_mode.finding_responder')}</div>
        </div>
        <div>
          <div className="text-red-200">{t('emergency_mode.location_status')}</div>
          <div className="font-semibold">{t(meta.labelKey)}</div>
        </div>
        <div>
          <div className="text-red-200">{t('emergency_mode.last_updated')}</div>
          <div className="font-semibold">
            {tracking.secondsSinceUpdate == null ? '—' : t('emergency_mode.seconds_ago', { count: tracking.secondsSinceUpdate })}
          </div>
        </div>
      </div>

      <a href="tel:112" className="block text-center bg-white text-red-700 font-bold text-xs py-2 rounded-lg">
        {t('emergency_mode.call_emergency')}
      </a>
      <button type="button" onClick={() => setCloseStep(1)}
        className="w-full text-center border border-white/60 text-white font-semibold text-xs py-2 rounded-lg">
        {t('emergency_mode.close_button')}
      </button>
    </div>

    {closeStep > 0 && (
      <div className="fixed inset-0 z-[1600] bg-black/70 flex items-center justify-center p-4"
        role="alertdialog" aria-modal="true" aria-labelledby="close-sos-title" aria-describedby="close-sos-body"
        onKeyDown={(e) => { if (e.key === 'Escape') keepSOS() }}>
        <div className="bg-white dark:bg-slate-800 text-slate-900 dark:text-slate-100 rounded-2xl shadow-2xl max-w-sm w-full p-5 space-y-3">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-500 dark:text-slate-400">
            <span>{t('emergency_mode.close_step', { n: closeStep, total: CLOSE_STEPS })}</span>
            <span className="flex gap-1" aria-hidden="true">
              {[1, 2, 3].map((n) => (
                <span key={n}
                  className={`w-2.5 h-2.5 rounded-full ${n <= closeStep ? 'bg-red-600' : 'bg-slate-300 dark:bg-slate-600'}`} />
              ))}
            </span>
          </div>
          <h2 id="close-sos-title" aria-live="polite" className="text-lg font-bold">{t(`emergency_mode.close_title_${closeStep}`)}</h2>
          <p id="close-sos-body" aria-live="polite" className="text-sm text-slate-600 dark:text-slate-300">
            {t(`emergency_mode.close_body_${closeStep}`)}
          </p>
          {closeFailed && (
            <div role="alert" className="bg-red-100 border border-red-600 text-red-800 rounded-lg p-3 text-sm font-semibold">
              {t('emergency_mode.close_failed')}
            </div>
          )}
          <div className="space-y-2 pt-1">
            <button type="button" autoFocus onClick={keepSOS} disabled={closing}
              className="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 rounded-xl">
              {t('emergency_mode.close_keep')}
            </button>
            <button type="button" onClick={confirmClose} disabled={!armed || closing} aria-busy={closing}
              className="w-full border border-slate-400 dark:border-slate-500 text-slate-800 dark:text-slate-100 py-2.5 rounded-xl text-sm font-semibold disabled:opacity-40">
              {closeStep < CLOSE_STEPS ? t('emergency_mode.close_yes') : t('emergency_mode.close_final')}
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  )
}
