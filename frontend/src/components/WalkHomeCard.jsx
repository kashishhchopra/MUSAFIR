import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import WalkHomeMap from './WalkHomeMap.jsx'
import { clock, searchPlaces } from '../lib/walkHomeService'
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
  const [destination, setDestination] = useState('')
  const [coords, setCoords] = useState(null) // {lat,lng,name,address} | 'none' | 'error'
  const [matches, setMatches] = useState(null) // search results to pick from
  const [finding, setFinding] = useState(false)
  const [duration, setDuration] = useState(config.duration.default)
  const [interval, setIntervalMin] = useState(config.interval.default)
  const [grace, setGrace] = useState(config.grace.default)
  const [picked, setPicked] = useState([])
  const [step, setStep] = useState('form') // form | review
  const [gName, setGName] = useState('')
  const [gContact, setGContact] = useState('')
  const [localError, setLocalError] = useState(null)

  // Search, then let the tourist pick: several real places can share a name
  // (a road in two cities, a market and a bus stop), so never auto-pick one.
  const find = async () => {
    const q = destination.trim()
    if (q.length < 2) return
    setFinding(true)
    setCoords(null)
    setMatches(null)
    try {
      const r = await searchPlaces(q)
      const results = r.results || []
      if (results.length === 0) setCoords('none')
      else setMatches(results)
    } catch {
      setCoords('error')
    } finally {
      setFinding(false)
    }
  }

  const choose = (m) => {
    setDestination(m.name)
    setCoords({ lat: m.lat, lng: m.lng, name: m.name, address: m.address })
    setMatches(null)
  }

  const away = (m) => (m.distance_m == null ? null
    : m.distance_m < 1000 ? t('walk_home.place_m', { m: m.distance_m })
      : t('walk_home.place_km', { km: (m.distance_m / 1000).toFixed(m.distance_m < 10_000 ? 1 : 0) }))

  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]))

  const problem = () => {
    if (!destination.trim()) return t('walk_home.destination_ph')
    const bad = (v, s) => !Number.isFinite(v) || v < s.min || v > s.max
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
    destination_name: destination.trim(),
    ...(coords && typeof coords === 'object' ? { dest_lat: coords.lat, dest_lng: coords.lng } : {}),
    duration_minutes: duration, check_interval_minutes: interval, grace_minutes: grace,
    guardian_ids: picked,
  })

  const addGuardian = async (e) => {
    e.preventDefault()
    if (!gName.trim()) return
    const g = await walk.createGuardian(gName.trim(), gContact.trim())
    if (g) { setGName(''); setGContact(''); setPicked((p) => [...p, g.id]) }
  }

  const chosen = guardians.filter((g) => picked.includes(g.id))

  if (step === 'review') {
    return (
      <div className="space-y-3" data-testid="wh-review">
        <h4 className="font-semibold text-sm">{t('walk_home.review_title')}</h4>
        <dl className="text-sm space-y-1.5">
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.destination')}</dt>
            <dd className="font-medium text-right">{destination.trim()}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-slate-500">{t('walk_home.duration')}</dt>
            <dd className="font-medium">{t('walk_home.minutes', { n: duration })}</dd></div>
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

      <div>
        <label htmlFor="wh-dest" className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">
          {t('walk_home.destination')}
        </label>
        <div className="flex gap-2">
          <input id="wh-dest" value={destination} maxLength={120}
            onChange={(e) => { setDestination(e.target.value); setCoords(null); setMatches(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); find() } }}
            placeholder={t('walk_home.destination_ph')} className={INPUT} />
          <button type="button" onClick={find} disabled={finding || destination.trim().length < 2}
            className="shrink-0 text-xs font-semibold px-3 rounded-lg bg-sky-50 dark:bg-sky-900/30 text-sky-700 dark:text-sky-300 disabled:opacity-50">
            {finding ? t('walk_home.finding_place') : t('walk_home.find_place')}
          </button>
        </div>
        {matches && (
          <div className="mt-2" data-testid="wh-matches">
            <p className="text-xs text-slate-500 dark:text-slate-400 mb-1">{t('walk_home.pick_place')}</p>
            <ul className="border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700 overflow-hidden">
              {matches.map((m, i) => (
                <li key={`${m.lat},${m.lng},${i}`}>
                  <button type="button" onClick={() => choose(m)}
                    className="w-full text-left px-3 py-2 hover:bg-sky-50 dark:hover:bg-sky-900/30">
                    <span className="block text-sm font-medium text-slate-800 dark:text-slate-100">📍 {m.name}</span>
                    <span className="block text-[11px] text-slate-500 dark:text-slate-400">
                      {[m.address, away(m)].filter(Boolean).join(' · ')}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {coords && typeof coords === 'object' && (
          <div className="text-xs text-emerald-700 dark:text-emerald-400 mt-1 flex items-start justify-between gap-2" data-testid="wh-found">
            <span>📍 {coords.address ? `${coords.name} — ${coords.address}`
              : t('walk_home.place_found', { lat: coords.lat.toFixed(4), lng: coords.lng.toFixed(4) })}</span>
            <button type="button" onClick={find} className="shrink-0 underline">{t('walk_home.change_place')}</button>
          </div>
        )}
        {coords === 'none' && <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">{t('walk_home.place_not_found')}</p>}
        {coords === 'error' && <p className="text-xs text-amber-700 dark:text-amber-300 mt-1">{t('walk_home.search_failed')}</p>}
      </div>

      <MinutesPicker id="wh-dur" label={t('walk_home.duration')} spec={config.duration}
        value={duration} onChange={setDuration} />
      <MinutesPicker id="wh-int" label={t('walk_home.interval')} spec={config.interval}
        value={interval} onChange={setIntervalMin} />
      <div>
        <label htmlFor="wh-grace" className="block text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">
          {t('walk_home.grace')}
        </label>
        <input id="wh-grace" type="number" min={config.grace.min} max={config.grace.max} value={grace}
          onChange={(e) => setGrace(Number(e.target.value))} className={`${INPUT} !w-24`} />
      </div>

      <fieldset>
        <legend className="text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">{t('walk_home.guardians')}</legend>
        {guardians.length === 0 && <p className="text-xs text-slate-500">{t('walk_home.guardians_none')}</p>}
        <div className="space-y-1.5">
          {guardians.map((g) => (
            <label key={g.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={picked.includes(g.id)} onChange={() => toggle(g.id)} />
              <span>{g.guardian_name}</span>
            </label>
          ))}
        </div>
        <p className="text-[11px] text-slate-500 mt-1">{t('walk_home.guardians_hint')}</p>
        <div className="mt-2 flex flex-col gap-2">
          <input value={gName} onChange={(e) => setGName(e.target.value)} placeholder={t('walk_home.guardian_name')}
            aria-label={t('walk_home.guardian_name')} className={INPUT} />
          <input value={gContact} onChange={(e) => setGContact(e.target.value)} placeholder={t('walk_home.guardian_contact')}
            aria-label={t('walk_home.guardian_contact')} className={INPUT} />
          <button type="button" onClick={addGuardian} disabled={!gName.trim()}
            className="text-xs font-semibold py-2 rounded-lg bg-slate-100 dark:bg-slate-700 disabled:opacity-50">
            {t('walk_home.add_guardian')}
          </button>
        </div>
      </fieldset>

      {(localError || error) && <p role="alert" className="text-xs text-red-600">{localError || errorMessage(t, error)}</p>}
      <button className="w-full text-sm font-bold text-white bg-gradient-to-r from-sky-500 to-indigo-500 py-2.5 rounded-xl">
        {t('walk_home.review')}
      </button>
    </form>
  )
}

function Active({ walk }) {
  const { t } = useTranslation()
  const { journey: j, remaining, sharing, busy, error, config } = walk
  const [confirmCancel, setConfirmCancel] = useState(false)
  const missed = j.phase === 'missed'
  const escalated = j.phase === 'escalated'
  const dest = j.dest_lat != null ? [j.dest_lat, j.dest_lng] : null
  const pos = j.has_location ? [j.last_lat, j.last_lng] : null
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
        {(dest || pos) && <WalkHomeMap dest={dest} pos={pos} stale={j.location_stale} />}
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
export default function WalkHomeCard({ walk }) {
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
      {walk.active ? <Active walk={walk} /> : <Setup walk={walk} />}
      <details className="mt-4 text-xs text-slate-500 dark:text-slate-400">
        <summary className="cursor-pointer font-semibold">{t('walk_home.how_title')}</summary>
        <ol className="list-decimal ml-4 mt-1 space-y-0.5">
          <li>{t('walk_home.how_1')}</li><li>{t('walk_home.how_2')}</li><li>{t('walk_home.how_3')}</li>
        </ol>
        <p className="mt-1.5">{t('walk_home.limits')}</p>
      </details>
      {!walk.active && (
        <div className="mt-4">
          <div className="text-xs font-semibold text-slate-600 dark:text-slate-300 mb-1">{t('walk_home.history')}</div>
          {walk.history.length === 0 ? <p className="text-xs text-slate-400">{t('walk_home.history_empty')}</p> : (
            <ul className="text-xs space-y-1" data-testid="wh-history">
              {walk.history.slice(0, 5).map((h) => (
                <li key={h.id} className="flex justify-between gap-2">
                  <span className="truncate">{h.destination_name}</span>
                  <span className="text-slate-500 shrink-0">{t(`walk_home.${HIST_KEY[h.status] || 'hist_planned'}`)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
