import { describe, it, expect, afterEach, vi } from 'vitest'
import { render } from '@testing-library/react'
import PushToggle from './PushToggle'

afterEach(() => { vi.unstubAllGlobals(); delete navigator.serviceWorker })

describe('PushToggle', () => {
  it('explains that SMS still works when push is unsupported', () => {
    const { getByText } = render(<PushToggle />)
    expect(getByText(/doesn’t support push notifications/)).toBeTruthy()
  })

  it('shows a clear blocked state when the user denied permission', async () => {
    vi.stubGlobal('Notification', { permission: 'denied', requestPermission: vi.fn() })
    vi.stubGlobal('PushManager', function PushManager() {})
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true, value: { ready: new Promise(() => {}), getRegistration: async () => null },
    })
    const { findByText, getByRole } = render(<PushToggle />)
    expect(await findByText(/Blocked in browser settings/)).toBeTruthy()
    expect(getByRole('button').disabled).toBe(true)
  })
})
