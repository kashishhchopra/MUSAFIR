// Android-app code paths of the routes, background location and speech
// recognition, with the Capacitor plugins replaced by fakes.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { act, cleanup, render, renderHook, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const fakes = vi.hoisted(() => ({
  user: null,
  watcher: null,
  removed: [],
  speech: { perm: 'granted', matches: ['help me'], available: true },
}))

vi.mock('./lib/platform', () => ({
  isNativeApp: () => true, isIOSApp: () => false, nativePlatform: () => 'android',
}))

vi.mock('@capacitor/core', async (importOriginal) => ({
  ...(await importOriginal()),
  registerPlugin: () => ({
    addWatcher: vi.fn(async (opts, cb) => { fakes.watcher = { opts, cb }; return 'w1' }),
    removeWatcher: vi.fn(async ({ id }) => { fakes.removed.push(id) }),
  }),
}))

vi.mock('@capacitor/app', () => ({
  App: { addListener: vi.fn(async () => ({ remove: vi.fn() })), exitApp: vi.fn() },
}))

vi.mock('@capacitor-community/speech-recognition', () => ({
  SpeechRecognition: {
    available: vi.fn(async () => ({ available: fakes.speech.available })),
    checkPermissions: vi.fn(async () => ({ speechRecognition: fakes.speech.perm })),
    requestPermissions: vi.fn(async () => ({ speechRecognition: fakes.speech.perm })),
    start: vi.fn(async () => ({ matches: fakes.speech.matches })),
    stop: vi.fn(async () => {}),
  },
}))

vi.mock('./lib/pushService', () => ({
  onPushTap: vi.fn(async () => ({ remove: vi.fn() })),
  resyncNativePush: vi.fn(),
}))

vi.mock('./auth.jsx', () => ({
  useAuth: () => ({ user: fakes.user, ready: true, logout: vi.fn() }),
}))

vi.mock('./pages/tourist/TouristApp.jsx', () => ({ default: () => <p>tourist home</p> }))
vi.mock('./pages/Login.jsx', () => ({ default: () => <p>login screen</p> }))

const { default: App } = await import('./App.jsx')
const { default: useGeolocation } = await import('./hooks/useGeolocation')
const { default: useSpeechRecognition } = await import('./hooks/useSpeechRecognition')

afterEach(() => {
  cleanup()
  fakes.user = null
  fakes.watcher = null
  fakes.removed = []
})

function renderAt(path) {
  return render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>)
}

describe('routes in the Android app', () => {
  it('sends staff accounts to the web dashboard instead of the admin console', () => {
    fakes.user = { role: 'admin' }
    renderAt('/admin/incidents')
    expect(screen.getByText('This app is for tourists.')).toBeInTheDocument()
  })

  it('opens the tourist home for tourists', () => {
    fakes.user = { role: 'tourist', tourist_id: 1 }
    renderAt('/')
    expect(screen.getByText('tourist home')).toBeInTheDocument()
  })

  it('has no guardian share page (that stays on the web)', () => {
    renderAt('/guardian/abc')
    expect(screen.getByText('login screen')).toBeInTheDocument()
  })
})

describe('background location in the Android app', () => {
  it('uses the foreground-service watcher and reports fixes in the usual shape', async () => {
    const { result, unmount } = renderHook(() => useGeolocation({ enabled: true }))
    await waitFor(() => expect(fakes.watcher).not.toBeNull())
    expect(fakes.watcher.opts).toMatchObject({ requestPermissions: true, backgroundTitle: expect.any(String) })

    act(() => fakes.watcher.cb({ latitude: 26.1, longitude: 91.7, accuracy: 8, speed: 2, bearing: 90, time: 1 }))
    expect(result.current.position).toEqual({
      lat: 26.1, lng: 91.7, accuracy: 8, speedKmh: 7.2, heading: 90, timestamp: 1,
    })
    expect(result.current.permissionState).toBe('granted')

    unmount()
    expect(fakes.removed).toEqual(['w1'])
  })

  it('reports a denied permission', async () => {
    const { result } = renderHook(() => useGeolocation({ enabled: true }))
    await waitFor(() => expect(fakes.watcher).not.toBeNull())
    act(() => fakes.watcher.cb(null, { code: 'NOT_AUTHORIZED', message: 'Location permission denied' }))
    expect(result.current.permissionState).toBe('denied')
    expect(result.current.error).toBe('Location permission denied')
  })

  it('does nothing while tracking is off', () => {
    renderHook(() => useGeolocation({ enabled: false }))
    expect(fakes.watcher).toBeNull()
  })
})

describe('speech recognition in the Android app', () => {
  it('uses the phone recognizer and returns the transcript', async () => {
    fakes.speech = { perm: 'granted', matches: ['help me'], available: true }
    const { result } = renderHook(() => useSpeechRecognition({ lang: 'en-IN' }))
    expect(result.current.supported).toBe(true)

    act(() => result.current.start())
    await waitFor(() => expect(result.current.transcript).toBe('help me'))
    expect(result.current.listening).toBe(false)
  })

  it('reports a refused microphone', async () => {
    fakes.speech = { perm: 'denied', matches: [], available: true }
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    await waitFor(() => expect(result.current.error).toBe('not-allowed'))
    expect(result.current.errorKind).toBe('permission')
  })

  it('reports when the phone has no speech recognizer installed', async () => {
    fakes.speech = { perm: 'granted', matches: [], available: false }
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    await waitFor(() => expect(result.current.errorKind).toBe('unavailable'))
    expect(result.current.listening).toBe(false)
  })

  it('reports "nothing heard" instead of silently doing nothing', async () => {
    fakes.speech = { perm: 'granted', matches: [], available: true }
    const { result } = renderHook(() => useSpeechRecognition())
    act(() => result.current.start())
    await waitFor(() => expect(result.current.errorKind).toBe('no-speech'))
  })
})
