import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { NATIONAL_EMERGENCY_NUMBER } from '../config'
import '../lib/walkHomeStrings'

// An SOS inside the running journey. Two taps (button -> "Yes, send SOS") so a
// pocket press cannot raise a real alert. Reports what actually happened: sent
// (with the incident id the server returned), queued for when the phone is back
// online, or failed -- never "sent" unless the server accepted it.
export default function WalkHomeSOS({ journey, onSOS }) {
  const { t } = useTranslation()
  const [step, setStep] = useState('idle') // idle | confirm | sending | sent | queued | failed
  const [incident, setIncident] = useState(null)

  const send = async () => {
    setStep('sending')
    const due = new Date(journey.next_checkin_deadline).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    try {
      const r = await onSOS(t('walk_home.sos_message', { dest: journey.destination_name, due }))
      if (r?.queued) setStep('queued')
      else { setIncident(r?.incident_id ?? r?.id ?? null); setStep('sent') }
    } catch {
      setStep('failed')
    }
  }

  return (
    <div className="space-y-2" data-testid="wh-sos">
      {step === 'idle' && (
        <button type="button" onClick={() => setStep('confirm')} data-testid="wh-sos-open"
          className="w-full text-sm font-extrabold py-3 rounded-2xl bg-red-600 hover:bg-red-700 text-white">
          🆘 {t('walk_home.sos_button')}
        </button>
      )}
      {step === 'confirm' && (
        <div role="alertdialog" aria-label={t('walk_home.sos_button')}
          className="rounded-xl border border-red-300 bg-red-50 dark:bg-red-900/20 p-3 space-y-2">
          <p className="text-sm font-semibold text-red-800 dark:text-red-200">{t('walk_home.sos_confirm')}</p>
          <div className="flex gap-2">
            <button type="button" onClick={() => setStep('idle')}
              className="flex-1 py-2 rounded-lg border border-slate-300 text-sm font-semibold">{t('walk_home.confirm_no')}</button>
            <button type="button" onClick={send} data-testid="wh-sos-send"
              className="flex-[2] py-2 rounded-lg bg-red-600 text-white text-sm font-bold">{t('walk_home.sos_send')}</button>
          </div>
        </div>
      )}
      {step === 'sending' && <p role="status" className="text-sm text-red-700">{t('walk_home.sos_sending')}</p>}
      {step === 'sent' && (
        <p role="status" data-testid="wh-sos-sent" className="text-sm font-semibold text-emerald-700 dark:text-emerald-400">
          {t('walk_home.sos_sent', { incident: incident ? ` (#${incident})` : '' })}
        </p>
      )}
      {step === 'queued' && <p role="status" data-testid="wh-sos-queued" className="text-sm font-semibold text-amber-700">{t('walk_home.sos_queued')}</p>}
      {step === 'failed' && <p role="alert" data-testid="wh-sos-failed" className="text-sm font-semibold text-red-700">{t('walk_home.sos_failed')}</p>}
      {(step === 'failed' || step === 'sent' || step === 'queued') && (
        <div className="flex gap-2">
          <a href={`tel:${NATIONAL_EMERGENCY_NUMBER}`}
            className="flex-1 text-center text-sm font-semibold py-2 rounded-lg border border-red-300 text-red-700">
            📞 {t('walk_home.sos_call')} ({NATIONAL_EMERGENCY_NUMBER})
          </a>
          <button type="button" onClick={() => setStep('idle')}
            className="text-xs font-semibold px-3 rounded-lg border border-slate-300">OK</button>
        </div>
      )}
    </div>
  )
}
