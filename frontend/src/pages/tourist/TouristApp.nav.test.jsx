import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, screen, within, act } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import TouristApp from './TouristApp.jsx'
import { ThemeProvider } from '../../theme.jsx'
import { clearNavSession, readRecents } from '../../lib/navSession'
import { runBack, closeAllOverlays } from '../../lib/navBus'

vi.mock('../../auth.jsx', () => ({
  useAuth: () => ({ user: { tourist_id: 1, role: 'tourist', full_name: 'Aarav' }, logout: vi.fn() }),
}))
const mockData = {
  ready: true, online: true, me: { id: 1, digital_id: 'STS-001', full_name: 'Aarav' }, toast: null,
  sendSOS: vi.fn(), posRef: { current: [26.14, 91.73] }, emergencyMessage: '', setEmergencyMessage: vi.fn(),
  sosSent: null, sosQueued: false, pendingCount: 0,
}
vi.mock('./useTouristData.js', () => ({ default: () => mockData }))
vi.mock('./tabs/HomeTab.jsx', () => ({ default: () => <div data-testid="tab-home">home tab</div> }))
vi.mock('./tabs/PlanTab.jsx', () => ({ default: () => <div data-testid="tab-plan">plan tab</div> }))
vi.mock('./tabs/ExploreTab.jsx', () => ({ default: () => <div data-testid="tab-explore">explore tab</div> }))
vi.mock('./tabs/HelpTab.jsx', () => ({ default: () => <div data-testid="tab-help">help tab</div> }))
vi.mock('./tabs/MeTab.jsx', () => ({ default: () => <div data-testid="tab-me">me tab</div> }))
vi.mock('../../components/DuressLock.jsx', () => ({ DuressLockButton: () => <div /> }))
// A running emergency is rendered by the app shell, outside every tab.
vi.mock('../../components/EmergencyModeCard.jsx', () => ({
  default: ({ sosSent }) => (sosSent ? <div data-testid="emergency-active">SOS ACTIVE</div> : null),
}))

let nav
const Probe = () => {
  const l = useLocation()
  nav = useNavigate()
  return <div data-testid="url">{l.pathname + l.search}</div>
}
const renderApp = (url = '/app') => render(
  <MemoryRouter initialEntries={Array.isArray(url) ? url : [url]}>
    <ThemeProvider><TouristApp /><Probe /></ThemeProvider>
  </MemoryRouter>)
const url = () => screen.getByTestId('url').textContent
const tabBar = () => within(document.querySelector('.tourist-tabbar'))
const go = (label) => fireEvent.click(tabBar().getByText(label))
const back = () => fireEvent.click(screen.getByTestId('sysnav-back'))
const home = () => fireEvent.click(screen.getByTestId('sysnav-home'))
const recents = () => fireEvent.click(screen.getByTestId('sysnav-recents'))
const shown = () => ['home', 'plan', 'explore', 'help', 'me'].filter((k) => screen.queryByTestId(`tab-${k}`))

beforeEach(() => {
  clearNavSession()
  mockData.sosSent = null
  mockData.sendSOS.mockClear()
})
afterEach(() => vi.useRealTimers())

describe('three-button navigation bar — presence', () => {
  it('shows Back, Home and Recent screens and publishes its height to the layout', () => {
    renderApp()
    const bar = screen.getByTestId('sysnav')
    expect(within(bar).getAllByRole('button').map((b) => b.getAttribute('aria-label')))
      .toEqual(['Back', 'Home', 'Recent screens'])
    expect(bar.className).not.toContain('hidden') // visible at every width in the tourist app
    expect(bar.className).toContain('fixed')
    expect(bar.className).toContain('safe-area-inset-bottom') // clear of the gesture area
    expect(document.body.classList.contains('has-sysnav')).toBe(true) // -> --sysnav-h for pinned controls
  })

  it('removes its layout offset when it goes away', () => {
    const { unmount } = renderApp()
    unmount()
    expect(document.body.classList.contains('has-sysnav')).toBe(false)
  })

  it('leaves the SOS button, tab bar and floating assistants in place', () => {
    renderApp()
    const sos = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('SOS'))
    expect(sos).toBeInTheDocument()
    expect(document.querySelector('.tourist-tabbar')).toBeInTheDocument()
    expect(screen.getByLabelText(/voice/i, { selector: 'button' })).toBeInTheDocument()
    // every pinned control reserves room for the bar rather than sitting under it
    const pinned = [...document.querySelectorAll('.fixed')].filter((e) => !e.matches('[data-testid="sysnav"]'))
    expect(pinned.some((e) => e.className.includes('var(--sysnav-h)'))).toBe(true)
    fireEvent.click(sos)
    expect(mockData.sendSOS).toHaveBeenCalled() // still one tap, bar or no bar
  })
})

describe('Back', () => {
  it('returns to the previous screen, following the real history', () => {
    renderApp()
    go('Plan')
    go('Help')
    expect(shown()).toEqual(['help'])
    back()
    expect(shown()).toEqual(['plan'])
    expect(url()).toBe('/app?tab=plan')
    back()
    expect(shown()).toEqual(['home'])
    expect(url()).toBe('/app')
  })

  it('at the very start it stays in the app and says so', () => {
    vi.useFakeTimers()
    renderApp()
    back()
    expect(shown()).toEqual(['home'])
    expect(screen.getByTestId('sysnav-notice')).toHaveTextContent(/already on the Home screen/i)
    act(() => { vi.advanceTimersByTime(2500) })
    expect(screen.queryByTestId('sysnav-notice')).toBeNull()
  })

  it('opened on a deep link, Back goes Home (not out of the app, not to login)', () => {
    renderApp('/app?tab=help')
    expect(shown()).toEqual(['help'])
    back()
    expect(shown()).toEqual(['home'])
    expect(url()).toBe('/app')
    back() // now at the root: nothing happens except the notice
    expect(shown()).toEqual(['home'])
  })

  it('closes an open sheet first, then goes back a screen', () => {
    renderApp()
    go('Plan')
    fireEvent.click(screen.getByText(/Add details before sending/))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    back()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(shown()).toEqual(['plan']) // the first press only closed the sheet
    back()
    expect(shown()).toEqual(['home'])
  })

  it('also works through the shared handler the Android hardware back key uses', () => {
    renderApp()
    go('Me')
    let handled
    act(() => { handled = runBack() })
    expect(handled).toBe(true)
    expect(shown()).toEqual(['home'])
    act(() => { handled = runBack() })
    expect(handled).toBe(false) // root: the native layer may then exit the app
  })

  it('follows the browser back button too (same history)', () => {
    renderApp()
    go('Explore')
    go('Me')
    act(() => nav(-1))
    expect(shown()).toEqual(['explore'])
    back()
    expect(shown()).toEqual(['home'])
  })

  it('does not loop or pile up duplicate entries when the same screen is tapped repeatedly', () => {
    renderApp()
    go('Plan'); go('Plan'); go('Plan')
    back()
    expect(shown()).toEqual(['home']) // one Back undoes all the repeated taps
  })
})

describe('Home', () => {
  it('jumps to the main screen from any depth', () => {
    renderApp()
    go('Plan'); go('Explore'); go('Help'); go('Me')
    home()
    expect(shown()).toEqual(['home'])
    expect(url()).toBe('/app')
    back() // the trail is still there: Home is a step, not a reset
    expect(shown()).toEqual(['me'])
  })

  it('closes sheets and panels but keeps an active SOS and the session', () => {
    mockData.sosSent = { incident_id: 9 }
    renderApp()
    go('Help')
    fireEvent.click(screen.getByText(/Add details before sending/))
    recents()
    expect(screen.getByTestId('recents-panel')).toBeInTheDocument()
    home()
    expect(screen.queryByTestId('recents-panel')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(shown()).toEqual(['home'])
    expect(screen.getByTestId('emergency-active')).toBeInTheDocument() // emergency untouched
    expect(mockData.sendSOS).not.toHaveBeenCalled()
  })

  it('on Home already: just clears overlays and scrolls to the top', () => {
    renderApp()
    const spy = vi.spyOn(window, 'scrollTo')
    home()
    expect(spy).toHaveBeenCalledWith({ top: 0, behavior: 'smooth' })
    expect(shown()).toEqual(['home'])
    expect(url()).toBe('/app')
  })
})

describe('Recent screens', () => {
  it('lists each visited screen once, newest first, and marks the current one', () => {
    renderApp()
    go('Plan'); go('Explore'); go('Plan'); go('Help')
    recents()
    const items = screen.getAllByTestId(/^recent-/).map((e) => e.getAttribute('data-testid'))
    expect(items).toEqual(['recent-help', 'recent-plan', 'recent-explore', 'recent-home'])
    expect(screen.getByTestId('recent-help')).toHaveAttribute('aria-current', 'page')
    expect(screen.getByTestId('recent-help')).toHaveTextContent('Current screen')
  })

  it('shows names and icons, and tapping one opens it and closes the panel', () => {
    renderApp()
    go('Plan'); go('Explore')
    recents()
    expect(screen.getByTestId('recent-plan')).toHaveTextContent('Plan')
    expect(screen.getByTestId('recent-plan')).toHaveTextContent('🧭')
    fireEvent.click(screen.getByTestId('recent-plan'))
    expect(screen.queryByTestId('recents-panel')).toBeNull()
    expect(shown()).toEqual(['plan'])
    expect(url()).toBe('/app?tab=plan')
    back() // the jump is a real history step
    expect(shown()).toEqual(['explore'])
  })

  it('can be closed with ×, the backdrop, Escape, the button again, or Back', () => {
    renderApp()
    go('Me')
    const open = () => { recents(); expect(screen.getByTestId('recents-panel')).toBeInTheDocument() }
    const closed = () => expect(screen.queryByTestId('recents-panel')).toBeNull()
    open(); fireEvent.click(screen.getByTestId('recents-close')); closed()
    open(); fireEvent.click(screen.getByTestId('recents-backdrop')); closed()
    open(); fireEvent.keyDown(document, { key: 'Escape' }); closed()
    open(); recents(); closed()
    open(); back(); closed()
    expect(shown()).toEqual(['me']) // closing never navigated anywhere
  })

  it('survives a refresh within the session and never lists an unknown screen', () => {
    sessionStorage.setItem('musafir.nav.recents.1', JSON.stringify([{ tab: 'help', at: Date.now() }, { tab: 'secret-admin', at: 1 }]))
    renderApp('/app?tab=plan')
    recents()
    const items = screen.getAllByTestId(/^recent-/).map((e) => e.getAttribute('data-testid'))
    expect(items).toEqual(['recent-plan', 'recent-help']) // current first, then remembered; junk ignored
    expect(readRecents(1).map((r) => r.tab)).toContain('plan')
  })

  it('is wiped on logout so the next person never sees it', () => {
    renderApp()
    go('Plan')
    expect(readRecents(1).length).toBeGreaterThan(0)
    clearNavSession()
    expect(readRecents(1)).toEqual([])
  })
})

describe('direct URLs and refresh', () => {
  it.each([['plan'], ['explore'], ['help'], ['me']])('opens /app?tab=%s directly', (t) => {
    renderApp(`/app?tab=${t}`)
    expect(shown()).toEqual([t])
  })

  it('falls back to Home for an unknown or edited tab value', () => {
    renderApp('/app?tab=../../admin')
    expect(shown()).toEqual(['home'])
  })

  it('the bar keeps working after landing on a deep link', () => {
    renderApp('/app?tab=me')
    go('Plan')
    back()
    expect(shown()).toEqual(['me'])
  })
})

describe('overlay bus', () => {
  it('closeAllOverlays closes every registered panel and nothing else', () => {
    renderApp()
    go('Plan')
    recents()
    fireEvent.click(screen.getByText(/Add details before sending/))
    expect(screen.getByRole('dialog', { name: /describe|SOS|details/i })).toBeInTheDocument()
    act(() => closeAllOverlays())
    expect(screen.queryByTestId('recents-panel')).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(shown()).toEqual(['plan'])
  })
})
