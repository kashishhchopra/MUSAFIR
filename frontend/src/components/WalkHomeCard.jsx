import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import WalkHomeMap from './WalkHomeMap.jsx'
import WalkHomeRoutePlanner, { thin } from './WalkHomeRoutePlanner.jsx'
import WalkHomeContacts from './WalkHomeContacts.jsx'
import WalkHomeSOS from './WalkHomeSOS.jsx'
import { clock, loadPlan } from '../lib/walkHomeService'
import { getRoute, formatDuration } from '../lib/routeService'
import { distanceM, progressAlong } from '../lib/routeGeometry'
import '../lib/walkHomeStrings'

const INPUT = 'w-full border border-slate-300 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 rounded-lg px-3 py-2 text-sm'
const CHIP = 'px-3 py-1.5 rounded-full text-xs font-semibold border'
const chip = (on) => `${CHIP} ${on
  ? 'bg-sky-600 text-white border-sky-600'
  : 'bg-white dark:bg-slate-800 text-slate-600 dark:text-slate-300 border-slate-300 dark:border-slate-600'}`

const errorMessage = (t, error) => (
  error === 'offline' ? t('walk_home.offline_action')
    : error === 'generic' ? t('walk_home.generic_error')
      : error)

// "12 min ago" style, from a seconds value.
function ago(seconds) {
  const s = Math.max(0, Math.round(seconds || 0))
  if (s < 90) return `${s}s`
  if (s < 5400) return `${Math.round(s / 60)} min`
  return `${Math.round(s / 3600)} h`
}

// Minutes picker: server-supplied presets plus a custom value inside the server's bounds.
function MinutesPicker({ label, spec, value, onChange, id }) {
  const { t } = useTranslation()
  const isPreset = spec.presets.includes(value)
  const [custom, setCustom] = useState(!isPreset)
  return (
    <div>
      <label htmlFor={`${id}-custom`} className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">
        {label}
      </label>
      <div className="flex flex-wrap gap-2 items-center">
        {spec.presets.map((m) => (
          <button type="button" key={m} onClick={() => { setCustom(false); onChange(m) }}
            className={chip(!custom && value === m)} aria-pressed={!custom && value === m}>
            {t('walk_home.minutes', { n: m })}
          </button>
        ))}
        <button type="button" onClick={() => setCustom(true)} className={chip(custom)} aria-pressed={custom}>
          {t('walk_home.custom')}
        </button>
        {custom && (
          <input id={`${id}-custom`} type="number" min={spec.min} max={spec.max} value={value}
            onChange={(e) => onChange(Number(e.target.value))} aria-label={label}
            className={`${INPUT} !w-24`} />
        )}
      </div>
    </div>
  )
}

function Setup({ walk }) {
  const { t } = useTranslation()
  const { config, guardians, busy, error } = walk
  const [plan, setPlan] = useState({ dest: null, route: null, state: 'idle', minutes: null, tooLong: false })
  const [manual, setManual] = useState(false)
  const [manualMin, setManualMin] = useState(config.duration.default)
  const [interval, setIntervalMin] = useState(config.interval.default)
  const [grace, setGrace] = useState(config.grace.default)
  const [picked, setPicked] = useState([])
  const [step, setStep] = useState('form') // form | review
  const [localError, setLocalError] = useState(null)

  // When the route cannot give a time (or the traveller prefers their own), the
  // manual picker is the explicit fallback -- never an invented duration.
  const needManual = plan.dest && (plan.state === 'error' || (plan.state === 'idle' && plan.dest) || plan.tooLong)
  const useManual = manual || needManual
  const duration = useManual ? manualMin : plan.minutes
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))

  const problem = () => {
    if (!plan.dest) return t('walk_home.set_dest_first')
    const bad = (v, sp) => !Number.isFinite(v) || v < sp.min || v > sp.max
    if (duration == null || plan.state === 'loading') return t('walk_home.route_loading')
    if (bad(duration, config.duration)) return `${t('walk_home.duration')}: ${config.duration.min}–${config.duration.max}`
    if (bad(interval, config.interval)) return `${t('walk_home.interval')}: ${config.interval.min}–${config.interval.max}`
    if (bad(grace, config.grace)) return `${t('walk_home.grace')}: ${config.grace.min}–${config.grace.max}`
    return null
  }

  const review = (e) => {
    e.preventDefault()
    const p = problem()
    setLocalError(p)
    if (!p) setStep('review')
  }

  const begin = () => walk.start({
    destination_name: plan.dest.name.slice(0, 120),
    dest_lat: plan.dest.lat, dest_lng: plan.dest.lng,
    duration_minutes: duration, check_interval_minutes: interval, grace_minutes: grace,
    guardian_ids: picked,
  }, plan.route ? {
    mode: plan.mode, geometry: plan.route.demo ? null : thin(plan.route.geometry),
    distance_km: plan.route.distance_km, duration_min: plan.route.duration_min, demo: !!plan.route.demo,
    dest: [plan.dest.lat, plan.dest.lng], startedAt: Date.now(),
  } : { mode: plan.mode, dest: [plan.dest.lat, plan.dest.lng], startedAt: Date.now() })

  const chosen = guardians.filter((g) => picked.includes(g.id))

  if (step === 'review') {
    const arrive = new Date(Date.now() + duration * 60_000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    return (
      <div className="space-y-3" data-testid="wh-review">
        <h4 className="font-semibold text-sm">{t('walk_home.review_title')}</h4>
        <dl className="text-sm space-y-1.5">
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.destination')}</dt>
            <dd className="font-medium text-right">{plan.dest.name.split(', ').slice(0, 2).join(', ')}</dd></div>
          {plan.route && (
            <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.route_distance')}</dt>
              <dd className="font-medium">{plan.route.distance_km} km · {t(`walk_home.mode_${plan.mode}`)}</dd></div>
          )}
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.route_deadline')}</dt>
            <dd className="font-medium">{t('walk_home.minutes', { n: duration })}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.route_arrive')}</dt>
            <dd className="font-medium">{arrive}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.interval')}</dt>
            <dd className="font-medium">{t('walk_home.minutes', { n: interval })}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.grace')}</dt>
            <dd className="font-medium">{t('walk_home.minutes', { n: grace })}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.guardians')}</dt>
            <dd className="font-medium text-right">{chosen.length ? chosen.map((g) => g.guardian_name).join(', ') : '—'}</dd></div>
        </dl>
        {chosen.length === 0 && (
          <p className="text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 rounded-lg p-2">
            {t('walk_home.no_guardian_warning')}
          </p>
        )}
        <p className="text-xs text-slate-500 dark:text-slate-400">{t('walk_home.location_will_be_shared')}</p>
        {error && <p role="alert" className="text-xs text-red-600">{errorMessage(t, error)}</p>}
        <div className="flex gap-2">
          <button onClick={() => setStep('form')} disabled={!!busy}
            className="flex-1 text-sm font-semibold py-2.5 rounded-xl border border-slate-300 dark:border-slate-600">
            {t('walk_home.back')}
          </button>
          <button onClick={begin} disabled={!!busy} data-testid="wh-start"
            className="flex-[2] text-sm font-bold py-2.5 rounded-xl bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white">
            {busy === 'start' ? t('walk_home.starting') : t('walk_home.start')}
          </button>
        </div>
      </div>
    )
  }

  return (
    <form onSubmit={review} noValidate className="space-y-4">
      <p className="text-xs text-slate-500 dark:text-slate-400">{t('walk_home.intro')}</p>

      <WalkHomeRoutePlanner config={config} onPlan={setPlan} />

      {plan.dest && (
        <div>
          {useManual ? (
            <MinutesPicker id="wh-dur" label={t('walk_home.duration')} spec={config.duration}
              value={manualMin} onChange={setManualMin} />
          ) : null}
          {!needManual && (
            <button type="button" onClick={() => setManual((m) => !m)} data-testid="wh-manual-toggle"
              className="mt-1 text-xs font-semibold text-sky-700 dark:text-sky-400 underline">
              {manual ? t('walk_home.auto_time') : t('walk_home.manual_time')}
            </button>
          )}
        </div>
      )}

      <MinutesPicker id="wh-int" label={t('walk_home.interval')} spec={config.interval}
        value={interval} onChange={setIntervalMin} />
      <details className="text-sm">
        <summary className="cursor-pointer text-xs font-semibold text-slate-600 dark:text-slate-300">{t('walk_home.advanced')}</summary>
        <div className="mt-2">
          <label htmlFor="wh-grace" className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">
            {t('walk_home.grace')}
          </label>
          <input id="wh-grace" type="number" min={config.grace.min} max={config.grace.max} value={grace}
            onChange={(e) => setGrace(Number(e.target.value))} className={`${INPUT} !w-24`} />
        </div>
      </details>

      <WalkHomeContacts walk={walk} picked={picked} onToggle={toggle}
        onAdded={(id) => setPicked((p) => [...p, id])}
        onRemoved={(id) => setPicked((p) => p.filter((x) => x !== id))} />

      {(localError || error) && <p role="alert" className="text-xs text-red-600">{localError || errorMessage(t, error)}</p>}
      <button className="w-full text-sm font-bold text-white bg-gradient-to-r from-sky-500 to-indigo-500 py-2.5 rounded-xl">
        {t('walk_home.review')}
      </button>
    </form>
  )
}

const OFF_ROUTE_M = 200   // this far from the planned path, on two fixes in a row
const ARRIVED_M = 100     // this close to the destination, on two fixes in a row
const MAX_ACCURACY_M = 100 // a fix less precise than this proves nothing either way
const AUTO_END_KEY = 'walkhome.autoEnd'
const readAutoEnd = () => { try { return localStorage.getItem(AUTO_END_KEY) === '1' } catch { return false } }

function useOnline() {
  const [on, setOn] = useState(typeof navigator === 'undefined' ? true : navigator.onLine !== false)
  useEffect(() => {
    const up = () => setOn(true); const down = () => setOn(false)
    window.addEventListener('online', up); window.addEventListener('offline', down)
    return () => { window.removeEventListener('online', up); window.removeEventListener('offline', down) }
  }, [])
  return on
}

function Active({ walk, onSOS }) {
  const { t } = useTranslation()
  const { journey: j, remaining, sharing, busy, error, config } = walk
  const [confirmCancel, setConfirmCancel] = useState(false)
  const online = useOnline()
  const [plan, setPlan] = useState(() => loadPlan(j.id))
  const [deviation, setDeviation] = useState(null) // metres off route, or null
  const [nearDest, setNearDest] = useState(false)
  const [recalcNote, setRecalcNote] = useState(false)
  const [autoEnd, setAutoEnd] = useState(readAutoEnd)
  const counts = useRef({ off: 0, near: 0, at: null })
  const ended = useRef(false)

  const dest = j.dest_lat != null ? [j.dest_lat, j.dest_lng] : null
  const pos = j.has_location ? [j.last_lat, j.last_lng] : null
  const path = plan?.geometry || null
  const progress = pos && path ? progressAlong(path, pos) : null

  // Route-following checks run once per NEW fix, and only on a live, precise one:
  // a stale or vague reading can neither raise "off route" nor end a journey.
  useEffect(() => {
    if (!pos || j.location_stale || !j.last_location_at || counts.current.at === j.last_location_at) return
    counts.current.at = j.last_location_at
    const precise = j.location_accuracy_m == null || j.location_accuracy_m <= MAX_ACCURACY_M
    if (!precise) { counts.current.off = 0; counts.current.near = 0; return }
    const off = progress ? progress.offM > OFF_ROUTE_M : false
    counts.current.off = off ? counts.current.off + 1 : 0
    if (counts.current.off >= 2) setDeviation(Math.round(progress.offM)); else if (!off) setDeviation(null)
    const near = dest ? distanceM(pos, dest) <= ARRIVED_M : false
    counts.current.near = near ? counts.current.near + 1 : 0
    setNearDest(counts.current.near >= 2)
  }, [j.last_location_at]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (nearDest && autoEnd && !ended.current && j.phase === 'on_track') {
      ended.current = true
      walk.end()
    }
  }, [nearDest, autoEnd]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleAutoEnd = (on) => {
    setAutoEnd(on)
    try { localStorage.setItem(AUTO_END_KEY, on ? '1' : '0') } catch { /* optional */ }
  }

  const recalc = async () => {
    if (!pos || !dest) return
    try {
      const r = await getRoute({ lat: pos[0], lng: pos[1] }, { lat: dest[0], lng: dest[1] }, plan?.mode || 'walking')
      const next = { ...(plan || {}), mode: plan?.mode || 'walking', geometry: r.demo ? null : thin(r.geometry),
        distance_km: r.distance_km, duration_min: r.duration_min, demo: !!r.demo, dest, startedAt: Date.now() }
      walk.updatePlan(j.id, next)
      setPlan(next)
      setDeviation(null)
      counts.current.off = 0
      setRecalcNote(true)
    } catch { walk.clearError() }
  }
  const missed = j.phase === 'missed'
  const escalated = j.phase === 'escalated'
  const sharingText = {
    off: '', starting: t('walk_home.sharing_starting'),
    sharing: t('walk_home.sharing_on', { ago: ago(sharing.sentAt ? (Date.now() - sharing.sentAt) / 1000 : 0) }),
    denied: t('walk_home.sharing_denied'), unavailable: t('walk_home.sharing_unavailable'),
    unsupported: t('walk_home.sharing_unsupported'), offline: t('walk_home.sharing_offline'),
  }[sharing.state]
  const sharingBad = ['denied', 'unavailable', 'unsupported', 'offline'].includes(sharing.state)

  const head = escalated ? 'bg-red-700 text-white' : missed ? 'bg-amber-500 text-white' : 'bg-emerald-600 text-white'

  return (
    <div className="space-y-3" data-testid="wh-active" data-phase={j.phase}>
      <div className={`rounded-2xl p-4 text-center ${head}`}>
        <div className="text-xs font-semibold opacity-90">
          {t(`walk_home.status_${j.phase === 'on_track' ? 'on_track' : j.phase}`)}
        </div>
        <div className="text-sm opacity-90 mt-0.5">🏁 {j.destination_name}</div>
        {escalated ? (
          <div className="mt-2 text-sm font-semibold">
            {t('walk_home.escalated_title')}
            {j.incident_id && <div data-testid="wh-incident" className="text-xs opacity-90">
              {t('walk_home.escalated_incident', { id: j.incident_id })}</div>}
          </div>
        ) : missed ? (
          <>
            <div className="text-4xl font-extrabold tabular-nums mt-1" data-testid="wh-grace-left">
              {clock(remaining.toEscalation)}
            </div>
            <div className="text-xs opacity-90">{t('walk_home.grace_left')}</div>
          </>
        ) : (
          <>
            <div className="text-4xl font-extrabold tabular-nums mt-1" data-testid="wh-remaining">
              {clock(remaining.toDeadline)}
            </div>
            <div className="text-xs opacity-90">
              {j.deadline_passed ? t('walk_home.overdue') : t('walk_home.time_to_checkin')}
            </div>
          </>
        )}
      </div>

      {(missed || escalated) && (
        <div role="alert" className={`rounded-xl p-3 text-sm border ${escalated
          ? 'bg-red-50 border-red-200 text-red-800 dark:bg-red-900/20 dark:text-red-200'
          : 'bg-amber-50 border-amber-200 text-amber-900 dark:bg-amber-900/20 dark:text-amber-200'}`}>
          <div className="font-semibold">{escalated ? t('walk_home.escalated_title') : t('walk_home.missed_title')}</div>
          <p className="text-xs mt-0.5">{escalated ? t('walk_home.escalated_body') : t('walk_home.missed_body')}</p>
        </div>
      )}

      {!online && (
        <p role="alert" data-testid="wh-offline" className="rounded-xl bg-amber-50 dark:bg-amber-900/20 border border-amber-200 text-amber-900 dark:text-amber-200 text-xs p-2">
          📡 {t('walk_home.offline_banner')}
        </p>
      )}

      {nearDest && !autoEnd && j.phase === 'on_track' && (
        <div role="alertdialog" data-testid="wh-arrival-prompt"
          className="rounded-xl border border-emerald-300 bg-emerald-50 dark:bg-emerald-900/20 p-3 space-y-2">
          <div className="text-sm font-semibold text-emerald-800 dark:text-emerald-200">{t('walk_home.arrival_title')}</div>
          <p className="text-xs">{t('walk_home.arrival_body')}</p>
          <div className="flex gap-2">
            <button onClick={() => setNearDest(false)} className="flex-1 py-2 rounded-lg border border-slate-300 text-xs font-semibold">
              {t('walk_home.arrival_no')}</button>
            <button onClick={walk.end} disabled={!!busy} data-testid="wh-arrival-yes"
              className="flex-[2] py-2 rounded-lg bg-emerald-600 text-white text-xs font-bold">{t('walk_home.arrival_yes')}</button>
          </div>
        </div>
      )}

      {deviation != null && (
        <div role="alert" data-testid="wh-deviation"
          className="rounded-xl border border-amber-300 bg-amber-50 dark:bg-amber-900/20 p-3 space-y-2">
          <div className="text-sm font-semibold text-amber-900 dark:text-amber-200">{t('walk_home.deviation_title')}</div>
          <p className="text-xs">{t('walk_home.deviation_body', { m: deviation })}</p>
          <div className="flex gap-2">
            <button onClick={() => { setDeviation(null); counts.current.off = 0 }}
              className="flex-1 py-2 rounded-lg border border-slate-300 text-xs font-semibold">{t('walk_home.dismiss')}</button>
            <button onClick={recalc} data-testid="wh-recalc"
              className="flex-[2] py-2 rounded-lg bg-amber-600 text-white text-xs font-bold">{t('walk_home.recalc')}</button>
          </div>
        </div>
      )}
      {recalcNote && deviation == null && (
        <p role="status" className="text-xs text-emerald-700 dark:text-emerald-400 text-center">{t('walk_home.recalculated')}</p>
      )}

      {!escalated && (
        <button onClick={walk.checkIn} disabled={!!busy} data-testid="wh-checkin"
          className={`w-full text-base font-extrabold py-4 rounded-2xl text-white shadow disabled:opacity-60 ${
            missed ? 'bg-red-600 hover:bg-red-700 sos-pulse' : 'bg-emerald-600 hover:bg-emerald-700'}`}>
          {busy === 'checkin' ? t('walk_home.checking_in')
            : missed ? t('walk_home.checkin_late_button') : t('walk_home.checkin_button')}
        </button>
      )}
      {walk.notice === 'checked_in' && (
        <p role="status" className="text-xs text-emerald-700 dark:text-emerald-400 text-center">
          {t('walk_home.checked_in_ok')}
        </p>
      )}
      {error && <p role="alert" className="text-xs text-red-600 text-center">{errorMessage(t, error)}</p>}

      <dl className="text-sm space-y-1.5">
        <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.next_checkin')}</dt>
          <dd className="font-medium">{new Date(j.next_checkin_deadline).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</dd></div>
        <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.arrive_by')}</dt>
          <dd className="font-medium" data-testid="wh-arrival">
            {new Date(j.arrive_by).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} · {clock(remaining.toArrival)}
          </dd></div>
        <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.last_checkin')}</dt>
          <dd className="font-medium" data-testid="wh-last-checkin">
            {j.last_checkin_at ? new Date(j.last_checkin_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : t('walk_home.never')}
          </dd></div>
      </dl>

      <div className="rounded-xl border border-slate-200 dark:border-slate-700 p-3 space-y-1.5">
        <div className="text-xs font-semibold text-slate-600 dark:text-slate-300">{t('walk_home.sharing_title')}</div>
        {sharingText && (
          <p data-testid="wh-sharing" data-state={sharing.state}
            className={`text-xs ${sharingBad ? 'text-red-600' : 'text-slate-600 dark:text-slate-300'}`}>{sharingText}</p>
        )}
        {j.has_location ? (
          <p className="text-xs flex items-center gap-2">
            <span className={`font-bold rounded px-1.5 py-0.5 text-[10px] ${j.location_stale
              ? 'bg-slate-200 text-slate-700' : 'bg-emerald-100 text-emerald-800'}`}>
              {j.location_stale ? t('walk_home.stale') : t('walk_home.live')}
            </span>
            {t('walk_home.last_known', { ago: ago(j.location_age_seconds) })}
          </p>
        ) : <p className="text-xs text-slate-500">{t('walk_home.no_position')}</p>}
        {progress && !j.location_stale && plan?.duration_min != null && (
          <p className="text-xs text-slate-600 dark:text-slate-300" data-testid="wh-progress">
            {t('walk_home.remaining_distance', { km: (progress.remainingM / 1000).toFixed(1) })}
            {' · '}{t('walk_home.remaining_time', { min: Math.max(1, Math.round(plan.duration_min * progress.fraction)) })}
          </p>
        )}
        {(dest || pos) && <WalkHomeMap dest={dest} pos={pos} stale={j.location_stale} path={path} />}
      </div>

      <div>
        <div className="text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">{t('walk_home.guardians_following')}</div>
        {j.guardians.length === 0 ? <p className="text-xs text-slate-500">{t('walk_home.no_guardian_warning')}</p> : (
          <ul className="text-sm space-y-1" data-testid="wh-guardians">
            {j.guardians.map((g) => (
              <li key={g.id} className="flex justify-between">
                <span>{g.name}</span>
                <span className={`text-xs ${g.has_access ? 'text-emerald-600' : 'text-slate-400'}`}>
                  {g.has_access ? t('walk_home.guardian_access') : t('walk_home.guardian_revoked')}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {j.phase === 'on_track' && j.extensions_left > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-slate-500">{t('walk_home.add_time')} ({t('walk_home.extensions_left', { n: j.extensions_left })})</span>
          {[...new Set([5, 15, config.max_extension_minutes])].filter((m) => m > 0 && m <= config.max_extension_minutes).map((m) => (
            <button key={m} onClick={() => walk.extend(m)} disabled={!!busy}
              className={chip(false)}>{t('walk_home.add_minutes', { n: m })}</button>
          ))}
        </div>
      )}

      {onSOS && <WalkHomeSOS journey={j} onSOS={onSOS} />}

      <label className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={autoEnd} onChange={(e) => toggleAutoEnd(e.target.checked)}
          data-testid="wh-auto-end" className="mt-0.5" />
        <span>{t('walk_home.auto_end')}<span className="block text-[11px] text-slate-500">{t('walk_home.auto_end_hint')}</span></span>
      </label>

      <div className="flex gap-2">
        <button onClick={walk.end} disabled={!!busy} data-testid="wh-end"
          className="flex-[2] text-sm font-bold py-2.5 rounded-xl bg-sky-600 hover:bg-sky-700 text-white disabled:opacity-60">
          {t('walk_home.end_button')}
        </button>
        {!confirmCancel ? (
          <button onClick={() => setConfirmCancel(true)} disabled={!!busy} data-testid="wh-cancel"
            className="flex-1 text-sm font-semibold py-2.5 rounded-xl border border-slate-300 dark:border-slate-600">
            {t('walk_home.cancel_button')}
          </button>
        ) : null}
      </div>
      {confirmCancel && (
        <div className="rounded-xl bg-slate-50 dark:bg-slate-900/40 p-3 text-xs space-y-2" role="alertdialog" aria-label={t('walk_home.cancel_button')}>
          <p>{t('walk_home.cancel_confirm')}</p>
          <div className="flex gap-2">
            <button onClick={() => setConfirmCancel(false)} className="flex-1 py-2 rounded-lg border border-slate-300 font-semibold">
              {t('walk_home.confirm_no')}
            </button>
            <button onClick={() => { setConfirmCancel(false); walk.cancel() }} data-testid="wh-cancel-yes"
              className="flex-1 py-2 rounded-lg bg-red-600 text-white font-semibold">{t('walk_home.confirm_yes')}</button>
          </div>
        </div>
      )}
    </div>
  )
}

const HIST_KEY = { completed: 'hist_completed', cancelled: 'hist_cancelled', escalated: 'hist_escalated', missed: 'hist_missed', planned: 'hist_planned' }

// Tourist-side Walk-Me-Home: setup -> review -> live journey -> history.
export default function WalkHomeCard({ walk, onSOS }) {
  const { t } = useTranslation()
  if (!walk.loaded && !walk.config) return null
  if (walk.config && !walk.config.enabled) return null
  if (!walk.config) return null

  return (
    <Card title={t('walk_home.card_title')} icon="🚶"
      iconColor="bg-emerald-50 text-emerald-600 dark:bg-emerald-900/30 dark:text-emerald-300">
      {walk.notice && walk.notice !== 'checked_in' && !walk.active && (
        <p role="status" data-testid="wh-ended" className="mb-3 text-sm text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 rounded-lg p-2">
          {t(`walk_home.${walk.notice}`)}
        </p>
      )}
      {walk.active ? <Active key={walk.journey.id} walk={walk} onSOS={onSOS} /> : <Setup walk={walk} />}
      <details className="mt-4 text-xs text-slate-500 dark:text-slate-400">
        <summary className="cursor-pointer font-semibold">{t('walk_home.how_title')}</summary>
        <ol className="list-decimal ml-4 mt-1 space-y-0.5">
          <li>{t('walk_home.how_1')}</li><li>{t('walk_home.how_2')}</li><li>{t('walk_home.how_3')}</li>
        </ol>
        <p className="mt-1.5">{t('walk_home.limits')}</p>
      </details>
      {!walk.active && (
        <div className="mt-4">
          <div className="flex items-center justify-between mb-1">
            <div className="text-xs font-semibold text-slate-600 dark:text-slate-300">{t('walk_home.history')}</div>
            {walk.history.length > 0 && (
              <button type="button" data-testid="wh-history-clear"
                onClick={async () => {
                  if (window.confirm(t('walk_home.history_confirm'))) await walk.clearHistory()
                }}
                className="text-[11px] font-semibold text-red-600">{t('walk_home.history_delete')}</button>
            )}
          </div>
          {walk.history.length === 0 ? <p className="text-xs text-slate-400">{t('walk_home.history_empty')}</p> : (
            <ul className="text-xs space-y-1.5" data-testid="wh-history">
              {walk.history.slice(0, 5).map((h) => {
                const mins = h.started_at && h.ended_at ? Math.max(1, Math.round((Date.parse(h.ended_at) - Date.parse(h.started_at)) / 60000)) : null
                return (
                  <li key={h.id} className="flex justify-between gap-2">
                    <span className="min-w-0">
                      <span className="block truncate">{h.destination_name.split(', ')[0]}</span>
                      <span className="block text-[11px] text-slate-400">
                        {h.started_at ? new Date(h.started_at).toLocaleDateString([], { day: 'numeric', month: 'short' }) : ''}
                        {mins != null ? ` · ${formatDuration(mins)}` : ''}
                      </span>
                    </span>
                    <span className="text-slate-500 shrink-0">{t(`walk_home.${HIST_KEY[h.status] || 'hist_planned'}`)}</span>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
