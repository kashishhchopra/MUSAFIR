import { useTranslation } from 'react-i18next'
import { clock } from '../lib/walkHomeService'
import '../lib/walkHomeStrings'

// Always-visible strip (every tab) while a journey is live, so a tourist is never
// being monitored without knowing it and can check in from anywhere in the app.
export default function WalkHomeBanner({ walk, onOpen }) {
  const { t } = useTranslation()
  if (!walk.enabled || !walk.active) return null
  const j = walk.journey
  const missed = j.phase === 'missed'
  const escalated = j.phase === 'escalated'
  const tone = escalated ? 'bg-red-700' : missed ? 'bg-amber-600' : 'bg-emerald-600'
  const time = missed ? walk.remaining.toEscalation : walk.remaining.toDeadline
  return (
    <div className={`fixed left-3 right-3 bottom-[calc(4.6rem_+_var(--sysnav-h))] z-[1001] rounded-2xl text-white shadow-lg px-3 py-2 flex items-center gap-3 ${tone}`}
      role="status" data-testid="wh-banner" data-phase={j.phase}>
      <button onClick={onOpen} className="flex-1 min-w-0 text-left">
        <div className="text-[11px] font-semibold opacity-90">
          🚶 {escalated ? t('walk_home.banner_escalated') : missed ? t('walk_home.banner_missed') : t('walk_home.banner_active')}
        </div>
        <div className="text-sm font-bold truncate">
          {j.destination_name}{!escalated && <span className="tabular-nums"> · {clock(time)}</span>}
        </div>
      </button>
      {!escalated && (
        <button onClick={walk.checkIn} disabled={!!walk.busy} data-testid="wh-banner-checkin"
          className="shrink-0 text-xs font-extrabold bg-white text-slate-900 rounded-xl px-3 py-2 disabled:opacity-60">
          {t('walk_home.checkin_button')}
        </button>
      )}
    </div>
  )
}
