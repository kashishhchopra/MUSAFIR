import useCountdown from '../hooks/useCountdown'
import { formatDuration } from '../lib/responseService'

const TONE = {
  critical: 'bg-red-600 text-white border-red-700 shadow-md',
  high: 'bg-orange-100 text-orange-900 border-orange-400 dark:bg-orange-900/50 dark:text-orange-100',
  medium: 'bg-yellow-100 text-yellow-900 border-yellow-400 dark:bg-yellow-900/50 dark:text-yellow-100',
  low: 'bg-slate-100 text-slate-800 border-slate-300 dark:bg-slate-700 dark:text-slate-100',
}

// Normalises either shape the API returns: a board item (`acknowledged`,
// `response_stage`...) or an IncidentOut row (`acknowledged_at`...).
export default function ResponseChip({ inc, stageLabels }) {
  const deadline = inc.response_deadline
  const acked = inc.acknowledged ?? !!inc.acknowledged_at
  const status = inc.escalation_status
  const counting = !!deadline && !acked && status === 'active'
  const remaining = useCountdown(deadline, inc.server_time, counting)
  if (!deadline) return null
  const stage = inc.response_stage || 1
  const stageText = stageLabels?.[stage - 1] || `Stage ${stage}`

  let state, body
  if (status === 'closed') {
    state = 'closed'
    body = 'Resolved'
  } else if (acked) {
    state = inc.slo_met === false ? 'acked-late' : 'acked'
    body = `✓ Acknowledged · ${inc.slo_met === false ? 'after target' : 'within target'}`
  } else if (status === 'exhausted') {
    state = 'exhausted'
    body = '⛔ Escalation exhausted — manual action needed'
  } else if (remaining != null && remaining < 0) {
    state = 'overdue'
    body = `OVERDUE +${formatDuration(remaining)} · ${stageText}`
  } else {
    state = 'counting'
    body = `${remaining == null ? '--:--' : formatDuration(remaining)} to respond · ${stageText}`
  }

  const tone = state === 'overdue' || state === 'exhausted'
    ? 'bg-red-700 text-white border-red-900 animate-pulse'
    : state === 'closed' || state === 'acked' || state === 'acked-late'
      ? 'bg-slate-50 text-slate-700 border-slate-300 dark:bg-slate-800 dark:text-slate-200'
      : TONE[inc.severity] || TONE.medium

  return (
    <span data-testid="response-chip" data-state={state} data-severity={inc.severity}
      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border text-xs font-bold tabular-nums ${tone}`}>
      {body}
    </span>
  )
}
