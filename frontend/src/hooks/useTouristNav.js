import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom'
import { closeAllOverlays } from '../lib/navBus'
import { readRecents, writeRecents } from '../lib/navSession'

// Screen navigation for the tourist app, built on the existing React Router so the
// browser Back button, refresh and deep links all work:
//
//   /app            -> Home
//   /app?tab=plan   -> Plan   (explore | help | me likewise)
//
// Every screen change is a real history entry. This hook adds what the router does
// not give: an in-app history stack (so Back never leaves the app or lands on the
// login page), a de-duplicated "recent screens" list, and per-screen scroll memory.
export const TAB_KEYS = ['home', 'plan', 'explore', 'help', 'me']
const MAX_RECENTS = 8

export const tabFromSearch = (search) => {
  const t = new URLSearchParams(search).get('tab')
  return TAB_KEYS.includes(t) ? t : 'home' // an unknown/edited value falls back to Home
}
const urlFor = (tab) => (tab === 'home' ? '/app' : `/app?tab=${tab}`)

export default function useTouristNav(tid) {
  const location = useLocation()
  const navigate = useNavigate()
  const navType = useNavigationType()
  const tab = tabFromSearch(location.search)

  const tabRef = useRef(tab)
  tabRef.current = tab
  const stack = useRef([]) // [{key, tab}] -- screens visited inside the app, oldest first
  const [depth, setDepth] = useState(1)
  const [recents, setRecents] = useState(() => readRecents(tid))
  const scroll = useRef({}) // tab -> last scrollY

  const saveScroll = useCallback(() => {
    scroll.current[tabRef.current] = window.scrollY || 0
  }, [])

  // Keep the in-app stack in step with the router (also correct under StrictMode's
  // double effect run: re-seeing the same entry changes nothing).
  useEffect(() => {
    const s = stack.current
    const entry = { key: location.key, tab }
    if (s[s.length - 1]?.key !== location.key) {
      if (navType === 'PUSH') s.push(entry)
      else if (navType === 'REPLACE' && s.length) s[s.length - 1] = entry
      else {
        const i = s.findIndex((e) => e.key === location.key)
        if (i >= 0) s.length = i + 1 // went back to a known entry
        else s.splice(0, s.length, entry) // first load, refresh or unknown jump: start over
      }
    }
    setDepth(s.length)
  }, [location.key]) // eslint-disable-line react-hooks/exhaustive-deps

  // Recent screens: most recent first, one entry per screen.
  useEffect(() => {
    setRecents((prev) => {
      const next = [{ tab, at: Date.now() }, ...prev.filter((r) => r.tab !== tab)].slice(0, MAX_RECENTS)
      writeRecents(tid, next)
      return next
    })
  }, [tab, tid])

  // Browser/Android back goes through popstate: remember where the page was
  // scrolled before the router swaps the screen.
  useEffect(() => {
    window.addEventListener('popstate', saveScroll)
    return () => window.removeEventListener('popstate', saveScroll)
  }, [saveScroll])

  // Come back to a screen at the scroll position you left it.
  useLayoutEffect(() => {
    const y = scroll.current[tab] || 0
    window.scrollTo(0, y)
    const raf = requestAnimationFrame(() => window.scrollTo(0, y)) // after async content lays out
    return () => cancelAnimationFrame(raf)
  }, [tab])

  const goTab = useCallback((next) => {
    if (!TAB_KEYS.includes(next)) return
    if (next === tabRef.current) return // never stack duplicate entries
    saveScroll()
    navigate(urlFor(next))
  }, [navigate, saveScroll])

  // true when it changed screen, false when already at the root (nothing to go back to).
  const back = useCallback(() => {
    if (stack.current.length > 1) {
      saveScroll()
      navigate(-1)
      return true
    }
    if (tabRef.current !== 'home') { // opened directly on a deep screen: go Home, not out of the app
      navigate('/app', { replace: true })
      return true
    }
    return false
  }, [navigate, saveScroll])

  const home = useCallback(() => {
    closeAllOverlays() // sheets/panels only -- nothing is logged out, saved or cancelled
    if (tabRef.current === 'home') {
      window.scrollTo({ top: 0, behavior: 'smooth' })
      scroll.current.home = 0
    } else goTab('home')
  }, [goTab])

  return { tab, goTab, back, home, recents, canGoBack: depth > 1 || tab !== 'home' }
}
