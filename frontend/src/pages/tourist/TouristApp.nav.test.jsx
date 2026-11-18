import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, screen, within, act } from '@testing-library/react'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import TouristApp from './TouristApp.jsx'
import { ThemeProvider } from '../../theme.jsx'
import { clearNavSession } from '../../lib/navSession'
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
// The Android hardware Back key goes through this shared handler (lib/navBus).
const back = () => { let handled; act(() => { handled = runBack() }); return handled }
const shown = () => ['home', 'plan', 'explore', 'help', 'me'].filter((k) => screen.queryByTestId(`tab-${k}`))

beforeEach(() => {
  clearNavSession()
  mockData.sosSent = null
  mockData.sendSOS.mockClear()
})
afterEach(() => vi.useRealTimers())

describe('the on-screen Back / Home / Recent bar is gone', () => {
  it('shows no navigation bar, and leaves no layout offset behind', () => {
    renderApp()
    expect(screen.queryByTestId('sysnav')).toBeNull()
    expect(screen.queryByTestId('sysnav-back')).toBeNull()
    expect(screen.queryByTestId('sysnav-home')).toBeNull()
    expect(screen.queryByTestId('sysnav-recents')).toBeNull()
    expect(screen.queryByLabelText('Recent screens')).toBeNull()
    expect(document.body.classList.contains('has-sysnav')).toBe(false)
  })

  it('leaves the SOS button, tab bar and floating assistants in place', () => {
    renderApp()
    const sos = [...document.querySelectorAll('button')].find((b) => b.textContent.includes('SOS'))
    expect(sos).toBeInTheDocument()
    expect(document.querySelector('.tourist-tabbar')).toBeInTheDocument()
    expect(screen.getByLabelText(/voice/i, { selector: 'button' })).toBeInTheDocument()
    fireEvent.click(sos)
    expect(mockData.sendSOS).toHaveBeenCalled() // still one tap
  })
})

describe('hardware Back (Android key) still walks the in-app history', () => {
  it('returns to the previous screen, following the real history', () => {
    renderApp()
    go('Plan')
    go('Help')
    expect(shown()).toEqual(['help'])
    expect(back()).toBe(true)
    expect(shown()).toEqual(['plan'])
    expect(url()).toBe('/app?tab=plan')
    expect(back()).toBe(true)
    expect(shown()).toEqual(['home'])
    expect(url()).toBe('/app')
  })

  it('at the very start there is nothing to go back to, so the native layer may exit', () => {
    renderApp()
    expect(back()).toBe(false)
    expect(shown()).toEqual(['home'])
  })

  it('opened on a deep link, Back goes Home (not out of the app, not to login)', () => {
    renderApp('/app?tab=help')
    expect(shown()).toEqual(['help'])
    expect(back()).toBe(true)
    expect(shown()).toEqual(['home'])
    expect(url()).toBe('/app')
    expect(back()).toBe(false) // now at the root
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

describe('direct URLs and refresh', () => {
  it.each([['plan'], ['explore'], ['help'], ['me']])('opens /app?tab=%s directly', (t) => {
    renderApp(`/app?tab=${t}`)
    expect(shown()).toEqual([t])
  })

  it('falls back to Home for an unknown or edited tab value', () => {
    renderApp('/app?tab=../../admin')
    expect(shown()).toEqual(['home'])
  })

  it('Back still works after landing on a deep link', () => {
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
    fireEvent.click(screen.getByText(/Add details before sending/))
    expect(screen.getByRole('dialog', { name: /describe|SOS|details/i })).toBeInTheDocument()
    act(() => closeAllOverlays())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(shown()).toEqual(['plan'])
  })
})
