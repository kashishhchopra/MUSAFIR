import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import useOverlay from '../hooks/useOverlay'

function ago(ms, t) {
  const m = Math.floor((Date.now() - ms) / 60000)
  if (m < 1) return t('sysnav.just_now', 'Just now')
  if (m < 60) return t('sysnav.min_ago', '{{n}} min ago', { n: m })
  return t('sysnav.hours_ago', '{{n}} h ago', { n: Math.floor(m / 60) })
}

// The "recent screens" overview: every screen visited this session, once each,
// newest first. Tap one to go back to it; close with ×, the backdrop, Escape,
// or the Back button. `screens` is [{tab, label, icon}] already resolved by the caller.
export default function RecentScreensPanel({ open, onClose, recents, screens, current, onSelect }) {
  const { t } = useTranslation()
  const panel = useRef(null)
  useOverlay(open, onClose)

  useEffect(() => {
    if (!open) return undefined
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    panel.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  const byTab = Object.fromEntries(screens.map((s) => [s.tab, s]))
  const items = recents.filter((r) => byTab[r.tab])

  return (
    <div className="fixed inset-0 z-[2050] flex items-end justify-center" data-testid="recents-panel">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden="true" data-testid="recents-backdrop" />
      <div ref={panel} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t('sysnav.recents', 'Recent screens')}
        className="relative w-full max-w-md bg-slate-100 dark:bg-slate-900 rounded-t-2xl shadow-2xl p-4 outline-none max-h-[75vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-3">
          <h2 className="font-bold text-slate-900 dark:text-slate-100">{t('sysnav.recents', 'Recent screens')}</h2>
          <button type="button" onClick={onClose} aria-label={t('sysnav.close', 'Close')} data-testid="recents-close"
            className="text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 text-2xl leading-none px-2">×</button>
        </div>
        <ul className="grid grid-cols-2 gap-3">
          {items.map((r) => {
            const s = byTab[r.tab]
            const here = r.tab === current
            return (
              <li key={r.tab}>
                <button type="button" onClick={() => onSelect(r.tab)} data-testid={`recent-${r.tab}`}
                  aria-current={here ? 'page' : undefined}
                  className={`w-full text-left rounded-2xl p-3 border transition-colors active:scale-[0.98] ${here
                    ? 'bg-sky-600/10 border-sky-500'
                    : 'bg-white dark:bg-slate-800 border-slate-200 dark:border-slate-700 hover:border-sky-400'}`}>
                  <span className="text-3xl block leading-none">{s.icon}</span>
                  <span className="block mt-2 text-sm font-semibold text-slate-800 dark:text-slate-100">{s.label}</span>
                  <span className="block text-[11px] text-slate-500 dark:text-slate-400">
                    {here ? t('sysnav.current', 'Current screen') : ago(r.at, t)}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
