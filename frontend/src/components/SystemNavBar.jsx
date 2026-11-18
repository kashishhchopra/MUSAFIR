import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

// Android-style three-button navigation: Back (◁), Home (○), Recent screens (▢).
// Phones only (`md:hidden`): on desktop the normal layout is left alone.
//
// While mounted it adds `has-sysnav` to <body>, which publishes the bar's height
// as --sysnav-h (see index.css). Everything else that is pinned to the bottom --
// the tab bar, SOS button, floating assistants, journey strip -- sits above that
// variable, so nothing is ever covered. The bar itself sits above sheets and
// panels so Back/Home stay reachable while one is open.
const Icon = ({ children }) => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{children}</svg>
)
const BackIcon = () => <Icon><path d="M17 4 7 12l10 8V4z" /></Icon>
const HomeIcon = () => <Icon><circle cx="12" cy="12" r="7.5" /></Icon>
const RecentsIcon = () => <Icon><rect x="5" y="5" width="14" height="14" rx="2.5" /></Icon>

const BTN = 'flex items-center justify-center h-12 text-slate-300 transition-colors duration-100 '
  + 'hover:bg-white/10 hover:text-white active:bg-white/20 active:scale-95 '
  + 'focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-sky-400'

// onBack() must return true when it did something, false at the root.
export default function SystemNavBar({ onBack, onHome, onRecents, recentsOpen = false }) {
  const { t } = useTranslation()
  const [notice, setNotice] = useState('')
  const timer = useRef(null)

  useEffect(() => {
    document.body.classList.add('has-sysnav')
    return () => { document.body.classList.remove('has-sysnav'); clearTimeout(timer.current) }
  }, [])

  const back = () => {
    if (onBack()) return
    // Nothing to go back to: say so, rather than closing the app or doing nothing silently.
    setNotice(t('sysnav.at_home', 'You are already on the Home screen'))
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setNotice(''), 2200)
  }

  return (
    <nav aria-label={t('sysnav.aria', 'Navigation buttons')} data-testid="sysnav"
      className="fixed bottom-0 inset-x-0 z-[2100] md:hidden bg-slate-950 border-t border-white/10 pb-[env(safe-area-inset-bottom)]">
      {notice && (
        <div role="status" data-testid="sysnav-notice"
          className="absolute -top-9 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-slate-800 text-slate-100 text-xs px-3 py-1.5 shadow-lg">
          {notice}
        </div>
      )}
      <div className="max-w-md mx-auto grid grid-cols-3">
        <button type="button" onClick={back} aria-label={t('sysnav.back', 'Back')} data-testid="sysnav-back" className={BTN}>
          <BackIcon />
        </button>
        <button type="button" onClick={onHome} aria-label={t('sysnav.home', 'Home')} data-testid="sysnav-home" className={BTN}>
          <HomeIcon />
        </button>
        <button type="button" onClick={onRecents} aria-label={t('sysnav.recents', 'Recent screens')}
          aria-pressed={recentsOpen} data-testid="sysnav-recents" className={`${BTN} ${recentsOpen ? 'bg-white/15 text-white' : ''}`}>
          <RecentsIcon />
        </button>
      </div>
    </nav>
  )
}
