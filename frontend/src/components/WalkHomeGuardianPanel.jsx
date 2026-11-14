import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { WS_ORIGIN } from '../config'
import { clock, getGuardianJourney } from '../lib/walkHomeService'
import WalkHomeMap from './WalkHomeMap.jsx'
import '../lib/walkHomeStrings'

const POLL_MS = 10_000

function ago(seconds) {
  const s = Math.max(0, Math.round(seconds || 0))
  if (s < 90) return `${s}s`
  if (s < 5400) return `${Math.round(s / 60)} min`
  return `${Math.round(s / 3600)} h`
}

// The guardian's Walk-Me-Home view, shown inside the existing Trip Guardian page
// (pages/GuardianView.jsx). The link token is the credential (a guardian has no
// account); the server returns a journey ONLY if this guardian was selected for
// it and it is still live. Updates arrive over /ws/guardian/{token}, with a
// 10 s poll as the fallback (and the only channel if the socket is blocked).
export default function WalkHomeGuardianPanel({ token }) {
  const { t } = useTranslation()
  const [journey, setJourney] = useState(null)
  const [ended, setEnded] = useState(false)
  const [live, setLive] = useState(false)
  const [, setTick] = useState(0)
  const anchor = useRef({ perf: 0, serverMs: 0 })
  const hadJourney = useRef(false)

  const apply = useCallback((j) => {
    if (j && j.active !== false && j.server_time) {
      anchor.current = { perf: performance.now(), serverMs: Date.parse(j.server_time) }
      hadJourney.current = true
      setEnded(false)
      setJourney(j)
    } else {
      if (hadJourney.current) setEnded(true) // it was live and is now over
      setJourney(null)
    }
  }, [])

  const load = useCallback(() => getGuardianJourney(token).then((r) => apply(r.journey)).catch(() => {}), [token, apply])

  useEffect(() => {
    load()
    const poll = setInterval(load, POLL_MS)
    return () => clearInterval(poll)
  }, [load])

  useEffect(() => {
    if (typeof WebSocket === 'undefined') return undefined
    let ws
    let closed = false
    let retry
    const open = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${WS_ORIGIN || `${proto}://${location.host}`}/ws/guardian/${encodeURIComponent(token)}`)
      ws.onopen = () => setLive(true)
      ws.onmessage = (e) => {
        try {
          const m = JSON.parse(e.data)
          if (m?.event === 'walk_home' && m.journey) apply(m.kind === 'ended' ? null : m.journey)
        } catch { /* ignore */ }
      }
      ws.onclose = () => { setLive(false); if (!closed) retry = setTimeout(open, 5000) }
    }
    open()
    return () => { closed = true; clearTimeout(retry); try { ws.close() } catch { /* already closed */ } }
  }, [token, apply])

  useEffect(() => {
    if (!journey) return undefined
    const iv = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(iv)
  }, [journey])

  const serverNow = () => anchor.current.serverMs + (performance.now() - anchor.current.perf)
  const until = (iso) => (iso ? Math.max(0, (Date.parse(iso) - serverNow()) / 1000) : 0)

  if (!journey) {
    return (
      <div className="bg-white dark:bg-slate-800 rounded-xl shadow-sm p-4" data-testid="wh-guardian-empty">
        <div className="text-sm font-semibold text-slate-800 dark:text-slate-100 mb-1">🚶 {t('walk_home.g_title')}</div>
        <p className="text-xs text-slate-500">{ended ? t('walk_home.g_ended') : t('walk_home.g_none')}</p>
      </div>
    )
  }

  const j = journey
  const missed = j.phase === 'missed'
  const escalated = j.phase === 'escalated'
  const pos = j.has_location ? [j.last_lat, j.last_lng] : null
  const dest = j.dest_lat != null ? [j.dest_lat, j.dest_lng] : null
  const age = j.has_location ? (j.location_age_seconds || 0) + (performance.now() - anchor.current.perf) / 1000 : null
  const stale = j.has_location && (j.location_stale || age > 120)

  return (
    <div className="bg-white dark:bg-slate-800 rounded-xl shadow-sm p-4 space-y-3" data-testid="wh-guardian" data-phase={j.phase}>
      <div>
        <div className="text-sm font-semibold text-slate-800 dark:text-slate-100">🚶 {t('walk_home.g_following', { name: j.tourist_name })}</div>
        <div className="text-xs text-slate-500">{t('walk_home.g_to')} <strong>{j.destination_name}</strong></div>
      </div>

      {(missed || escalated) && (
        <div role="alert" className={`rounded-xl p-3 text-sm font-semibold ${escalated ? 'bg-red-600 text-white' : 'bg-amber-100 text-amber-900'}`}>
          {escalated
            ? t('walk_home.g_alert_escalated', { name: j.tourist_name })
            : t('walk_home.g_alert_missed', { name: j.tourist_name, time: clock(until(j.grace_deadline)) })}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 text-center">
        <div className="rounded-lg bg-slate-50 dark:bg-slate-900/40 p-2">
          <div className="text-2xl font-extrabold tabular-nums" data-testid="wh-g-remaining">
            {clock(until(missed ? j.grace_deadline : j.next_checkin_deadline))}
          </div>
          <div className="text-[11px] text-slate-500">{t('walk_home.g_remaining')}</div>
        </div>
        <div className="rounded-lg bg-slate-50 dark:bg-slate-900/40 p-2">
          <div className="text-sm font-bold" data-testid="wh-g-last">
            {j.last_checkin_at ? new Date(j.last_checkin_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : t('walk_home.never')}
          </div>
          <div className="text-[11px] text-slate-500">{t('walk_home.last_checkin')}</div>
        </div>
      </div>

      <div className="text-xs flex items-center gap-2" data-testid="wh-g-location">
        {pos ? (
          <>
            <span className={`font-bold rounded px-1.5 py-0.5 text-[10px] ${stale ? 'bg-slate-200 text-slate-700' : 'bg-emerald-100 text-emerald-800'}`}>
              {stale ? t('walk_home.stale') : t('walk_home.live')}
            </span>
            {t('walk_home.last_known', { ago: ago(age) })}
          </>
        ) : <span className="text-slate-500">{t('walk_home.no_position')}</span>}
      </div>
      {(pos || dest) && <WalkHomeMap dest={dest} pos={pos} stale={stale} height={240} />}
      <div className="text-[11px] text-slate-400">{live ? t('walk_home.g_connection') : t('walk_home.g_polling')}</div>
    </div>
  )
}
