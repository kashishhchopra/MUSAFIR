import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '../i18n'
import Login, { loginErrorMessage } from './Login.jsx'

// The real app wraps this screen in a ThemeProvider; the toggle is irrelevant here.
vi.mock('../components/ThemeToggle.jsx', () => ({ default: () => null }))

const loginMock = vi.fn()
vi.mock('../auth.jsx', () => ({ useAuth: () => ({ login: loginMock }) }))

const httpError = (status, headers = {}, data = {}) => ({ response: { status, headers, data } })

async function submitWith(error) {
  loginMock.mockRejectedValueOnce(error)
  const { container } = render(<MemoryRouter><Login /></MemoryRouter>)
  fireEvent.change(container.querySelector('input[type=email]'), { target: { value: 'aarav@example.com' } })
  fireEvent.change(container.querySelector('input[type=password]'), { target: { value: 'tourist123' } })
  fireEvent.submit(container.querySelector('form'))
}

beforeEach(() => loginMock.mockReset())

describe('login error messages', () => {
  it('a wrong password (401) says incorrect email or password', async () => {
    await submitWith(httpError(401))
    expect(await screen.findByText('Incorrect email or password')).toBeInTheDocument()
  })

  it('too many attempts (429) says to wait, NOT that the password is wrong', async () => {
    await submitWith(httpError(429, { 'retry-after': '240' }))
    expect(await screen.findByText(/Too many sign-in attempts\. Please wait 4 minute\(s\)/)).toBeInTheDocument()
    expect(screen.queryByText('Incorrect email or password')).not.toBeInTheDocument()
  })

  it('an unreachable server says so instead of blaming the password', async () => {
    await submitWith(new Error('Network Error'))
    expect(await screen.findByText(/Can't reach the server/)).toBeInTheDocument()
    expect(screen.queryByText('Incorrect email or password')).not.toBeInTheDocument()
  })

  it('a server error (500) gives a generic failure, not "incorrect password"', async () => {
    await submitWith(httpError(500))
    expect(await screen.findByText('Sign-in failed. Please try again.')).toBeInTheDocument()
    expect(screen.queryByText('Incorrect email or password')).not.toBeInTheDocument()
  })

  it('shows the server\'s reason for a blocked account (403)', async () => {
    await submitWith(httpError(403, {}, { detail: 'This account is being deleted and can no longer sign in.' }))
    expect(await screen.findByText(/being deleted/)).toBeInTheDocument()
  })

  it('clears the error and signs in on a later successful attempt', async () => {
    await submitWith(httpError(429, { 'retry-after': '60' }))
    await screen.findByText(/Too many sign-in attempts/)
    loginMock.mockResolvedValueOnce({ role: 'tourist' })
    fireEvent.submit(document.querySelector('form'))
    await waitFor(() => expect(screen.queryByText(/Too many sign-in attempts/)).not.toBeInTheDocument())
  })

  it('rounds the wait up to whole minutes, minimum one', () => {
    const t = (k, o) => `${k}:${o?.minutes}`
    expect(loginErrorMessage(httpError(429, { 'retry-after': '5' }), t)).toBe('auth.too_many_attempts:1')
    expect(loginErrorMessage(httpError(429, { 'retry-after': '301' }), t)).toBe('auth.too_many_attempts:6')
    expect(loginErrorMessage(httpError(429, {}), t)).toBe('auth.too_many_attempts:1')   // header missing
  })
})
