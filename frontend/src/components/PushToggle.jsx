import { useCallback, useEffect, useState } from 'react'
import { currentSubscription, disablePush, enablePush, permissionState, pushSupported } from '../lib/pushService'

/**
 * Turn emergency push notifications on/off for THIS device. Used by tourists,
 * responders and admins (logged in) and by guardians (`guardianToken`, no account).
 */
export default function PushToggle({ guardianToken, onChange, className = '' }) {
  const [on, setOn] = useState(false)
  const [perm, setPerm] = useState(permissionState())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    try { setOn(Boolean(await currentSubscription())) } catch { setOn(false) }
    // after currentSubscription: in the Android app that call is what reads the permission
    setPerm(permissionState())
  }, [])
  useEffect(() => { refresh() }, [refresh])

  if (!pushSupported()) {
    return <p className={`text-xs text-slate-500 ${className}`}>This browser doesn’t support push notifications. SMS alerts still work.</p>
  }

  const toggle = async () => {
    setBusy(true); setError('')
    try {
      if (on && perm === 'granted') await disablePush({ guardianToken })
      else await enablePush({ guardianToken })
      await refresh()
      onChange?.()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Could not change notification settings.')
      await refresh()
    } finally { setBusy(false) }
  }

  const blocked = perm === 'denied'
  return (
    <div className={className}>
      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={toggle} disabled={busy || blocked} aria-pressed={on}
          className={`px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-60 ${on
            ? 'bg-slate-200 dark:bg-slate-700 text-slate-800 dark:text-slate-100'
            : 'bg-sky-600 hover:bg-sky-500 text-white'}`}>
          {busy ? 'Working…' : on ? '🔕 Turn off alerts on this device' : '🔔 Turn on emergency alerts'}
        </button>
        <span className={`text-xs font-semibold ${on ? 'text-emerald-600' : blocked ? 'text-red-600' : 'text-slate-500'}`}>
          {on ? '● On — you will be alerted even when MUSAFIR is closed' : blocked ? '● Blocked in browser settings' : '○ Off'}
        </span>
      </div>
      {error && <p role="alert" className="text-xs text-red-600 mt-1">{error}</p>}
    </div>
  )
}
