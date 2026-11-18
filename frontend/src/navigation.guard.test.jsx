import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import './i18n'
import App from './App.jsx'
import Login from './pages/Login.jsx'
import NativeBridge from './components/NativeBridge.jsx'
import { registerBack } from './lib/navBus'

// ---- auth is switchable per test
const auth = { user: null, ready: true, login: vi.fn(), logout: vi.fn() }
vi.mock('./auth.jsx', () => ({ useAuth: () => auth }))
vi.mock('./components/ThemeToggle.jsx', () => ({ default: () => null }))
// heavy screens are irrelevant to routing
vi.mock('./pages/tourist/TouristApp.jsx', () => ({ default: () => <div data-testid="tourist-app" /> }))
vi.mock('./pages/admin/AdminLayout.jsx', () => ({ default: () => <div data-testid="admin" /> }))

// ---- Capacitor: capture the hardware back listener
const cap = { back: null, exitApp: vi.fn() }
vi.mock('@capacitor/app', () => ({
  App: {
    addListener: vi.fn(async (name, cb) => { if (name === 'backButton') cap.back = cb; return { remove: vi.fn() } }),
    exitApp: (...a) => cap.exitApp(...a),
  },
}))
vi.mock('./lib/pushService', () => ({ onPushTap: async () => ({ remove: vi.fn() }), resyncNativePush: vi.fn() }))

const Where = () => {
  const l = useLocation()
  return <div data-testid="where">{l.pathname + l.search}</div>
}

beforeEach(() => {
  auth.user = null
  auth.login.mockReset()
  cap.back = null
  cap.exitApp.mockClear()
})

describe('protected screens', () => {
  it('a logged-out visitor cannot open /app?tab=plan and is sent to login', () => {
    render(<MemoryRouter initialEntries={['/app?tab=plan']}><App /><Where /></MemoryRouter>)
    expect(screen.queryByTestId('tourist-app')).toBeNull()
    expect(screen.getByTestId('where')).toHaveTextContent('/login')
  })

  it('a logged-in tourist reaches the app, and cannot open the admin area', () => {
    auth.user = { role: 'tourist', tourist_id: 1 }
    render(<MemoryRouter initialEntries={['/admin']}><App /><Where /></MemoryRouter>)
    expect(screen.queryByTestId('admin')).toBeNull()
    expect(screen.getByTestId('tourist-app')).toBeInTheDocument()
  })

  it('does not render anything protected while the session is still being checked', () => {
    auth.ready = false
    render(<MemoryRouter initialEntries={['/app?tab=me']}><App /></MemoryRouter>)
    expect(screen.queryByTestId('tourist-app')).toBeNull()
    auth.ready = true
  })
})

describe('login returns you to where you were going', () => {
  const renderLogin = (from) => render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state: from ? { from } : undefined }]}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>)
  const signIn = (container) => {
    fireEvent.change(container.querySelector('input[type=email]'), { target: { value: 'a@b.com' } })
    fireEvent.change(container.querySelector('input[type=password]'), { target: { value: 'password1' } })
    fireEvent.submit(container.querySelector('form'))
  }

  it('a tourist lands back on the deep link they asked for', async () => {
    auth.login.mockResolvedValue({ role: 'tourist' })
    const { container } = renderLogin('/app?tab=plan')
    signIn(container)
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/app?tab=plan'))
  })

  it.each([['https://evil.example/app'], ['//evil.example'], ['/admin'], ['/app?tab=plan&x=<script>'], [undefined]])(
    'ignores a non-app or unsafe return address (%s)', async (from) => {
      auth.login.mockResolvedValue({ role: 'tourist' })
      const { container } = renderLogin(from)
      signIn(container)
      await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^\/app$/))
    })

  it('staff accounts always go to their own console, whatever was asked for', async () => {
    auth.login.mockResolvedValue({ role: 'admin' })
    const { container } = renderLogin('/app?tab=plan')
    signIn(container)
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/admin'))
  })
})

describe('Android hardware back key', () => {
  const mount = async (path = '/app') => {
    auth.user = { role: 'tourist', tourist_id: 1 }
    render(<MemoryRouter initialEntries={[path]}><NativeBridge /></MemoryRouter>)
    await waitFor(() => expect(cap.back).toBeTypeOf('function'))
  }

  it('lets an open sheet / earlier screen take the press — the app is NOT closed', async () => {
    await mount()
    const handled = vi.fn(() => true)
    const off = registerBack(handled)
    cap.back({ canGoBack: false })
    expect(handled).toHaveBeenCalledTimes(1)
    expect(cap.exitApp).not.toHaveBeenCalled()
    off()
  })

  it('exits only from the root, when nothing is left to go back to', async () => {
    await mount()
    cap.back({ canGoBack: false })
    expect(cap.exitApp).toHaveBeenCalledTimes(1)
  })
})
