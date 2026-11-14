import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import '../lib/fallDetectionStrings'

const STATUS_STYLE = {
  active: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  starting: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  off: 'bg-slate-100 text-slate-700 dark:bg-slate-700 dark:text-slate-200',
  denied: 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
  unsupported: 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200',
  unavailable: 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200',
}

// Small always-visible reminder that motion monitoring is running, so the user
// is never monitored without knowing it. Rendered by TouristApp on every tab.
export function FallStatusPill({ fall }) {
  const { t } = useTranslation()
  if (!fall.enabled || !fall.monitoring) return null
  return (
    <div className="fixed right-3 top-[4.6rem] z-[1000] text-[11px] font-semibold rounded-full px-2.5 py-1 bg-emerald-600 text-white shadow"
      role="status" data-testid="fall-pill">
      🛡 {t('fall_detection.pill_active')}
    </div>
  )
}

// The tourist's control panel for the feature: status, opt-in toggle, what is
// collected, simulation mode, and recent detections.
export default function FallDetectionCard({ fall }) {
  const { t } = useTranslation()
  if (fall.configError) {
    return (
      <Card title={t('fall_detection.card_title')} icon="🛡">
        <div className="text-sm text-slate-500 dark:text-slate-400">{t('fall_detection.config_error')}</div>
      </Card>
    )
  }
  if (!fall.config) return null
  if (!fall.enabled) {
    return (
      <Card title={t('fall_detection.card_title')} icon="🛡">
        <div className="text-sm text-slate-500 dark:text-slate-400">{t('fall_detection.disabled_by_server')}</div>
      </Card>
    )
  }

  const status = fall.sensorStatus
  const busy = ['countdown', 'sending', 'cancelling'].includes(fall.phase)
  const help = status === 'denied' ? 'denied_help'
    : status === 'unsupported' ? 'unsupported_help'
    : status === 'unavailable' ? (fall.secureContext ? 'unavailable_help' : 'insecure_help')
    : null

  return (
    <Card title={t('fall_detection.card_title')} icon="🛡"
      iconColor="bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-300">
      <div className="space-y-3 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className={`px-2.5 py-1 rounded-full text-xs font-semibold ${STATUS_STYLE[status] || STATUS_STYLE.off}`}
            data-testid="fall-status" role="status">
            {t(`fall_detection.status_${status}`)}
          </span>
          {status === 'active' ? (
            <button onClick={fall.disableMonitoring}
              className="text-xs font-semibold text-slate-600 dark:text-slate-300 border border-slate-300 dark:border-slate-600 rounded-lg px-3 py-1.5">
              {t('fall_detection.disable')}
            </button>
          ) : (
            <button onClick={fall.enableMonitoring} disabled={status === 'starting' || status === 'unsupported'}
              className="text-xs font-semibold text-white bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded-lg px-3 py-1.5">
              {t('fall_detection.enable')}
            </button>
          )}
        </div>

        {help && <div className="text-xs text-amber-800 dark:text-amber-200 bg-amber-50 dark:bg-amber-900/20 rounded-lg px-3 py-2">{t(`fall_detection.${help}`)}</div>}

        <p className="text-xs text-slate-600 dark:text-slate-300">{t('fall_detection.how_it_works')}</p>
        <p className="text-xs text-slate-500 dark:text-slate-400">{t('fall_detection.disclaimer')}</p>

        {fall.config.simulation_enabled && (
          <div className="rounded-xl border border-dashed border-amber-400 p-3 space-y-2" data-testid="fall-sim-panel">
            <div className="text-xs font-bold text-amber-700 dark:text-amber-300">🧪 {t('fall_detection.simulate_title')}</div>
            <p className="text-xs text-slate-500 dark:text-slate-400">{t('fall_detection.simulate_note')}</p>
            <div className="flex gap-2">
              <button onClick={() => fall.simulate('fall')} disabled={busy}
                className="flex-1 text-xs font-semibold bg-amber-100 hover:bg-amber-200 disabled:opacity-50 text-amber-900 rounded-lg py-2">
                {t('fall_detection.simulate_fall')}
              </button>
              <button onClick={() => fall.simulate('crash')} disabled={busy}
                className="flex-1 text-xs font-semibold bg-amber-100 hover:bg-amber-200 disabled:opacity-50 text-amber-900 rounded-lg py-2">
                {t('fall_detection.simulate_crash')}
              </button>
            </div>
          </div>
        )}

        <div>
          <div className="text-xs font-semibold text-slate-700 dark:text-slate-200 mb-1">{t('fall_detection.history_title')}</div>
          {fall.history.length === 0 ? (
            <div className="text-xs text-slate-400">{t('fall_detection.history_empty')}</div>
          ) : (
            <ul className="space-y-1" data-testid="fall-history">
              {fall.history.slice(0, 5).map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-2 text-xs">
                  <span className="min-w-0 truncate">
                    {t(`fall_detection.type_${e.event_type}`)} · {t(`fall_detection.src_${e.source}`)}
                    {e.simulated && ' 🧪'}
                    <span className="text-slate-400"> · {new Date(e.detected_at).toLocaleTimeString()}</span>
                  </span>
                  <span className="shrink-0 font-semibold text-slate-600 dark:text-slate-300">
                    {t(`fall_detection.st_${e.status}`)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  )
}
