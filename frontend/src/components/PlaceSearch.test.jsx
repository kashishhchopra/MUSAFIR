import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import api from '../api'
import '../i18n'
import PlaceSearch from './PlaceSearch.jsx'

const mock = new MockAdapter(api)
const RESULT = { name: 'Kamakhya Temple, Guwahati, Assam, India', lat: 26.166, lng: 91.705, demo: false }

beforeEach(() => { mock.reset(); mock.onGet('/maps/search').reply(200, { results: [RESULT] }) })
afterEach(() => vi.restoreAllMocks())

// Real browsers submit the OUTERMOST form when a form sits inside another form, which reloads the
// page (seen in Fair Price). jsdom is lenient about that, so these tests pin the cause directly.
describe('PlaceSearch inside other forms', () => {
  it('is never a <form> itself', () => {
    const { container } = render(<PlaceSearch selected={null} onSelect={() => {}} placeholder="Where to?" />)
    expect(container.querySelector('form')).toBeNull()
    expect(screen.getByRole('button', { name: 'Search' })).toHaveAttribute('type', 'button')
  })

  const inForm = (props = {}) => {
    const submitted = vi.fn((e) => e.preventDefault())
    const utils = render(
      <form onSubmit={submitted}>
        <PlaceSearch selected={null} onSelect={props.onSelect || (() => {})} placeholder="Where to?" />
      </form>)
    return { ...utils, submitted }
  }

  it('the Search button searches and does NOT submit the surrounding form', async () => {
    const onSelect = vi.fn()
    const { submitted } = inForm({ onSelect })
    fireEvent.change(screen.getByLabelText('Where to?'), { target: { value: 'Kamakhya' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    fireEvent.click(await screen.findByText(/Kamakhya Temple/))
    expect(submitted).not.toHaveBeenCalled()
    expect(mock.history.get.filter((c) => c.url === '/maps/search')).toHaveLength(1)
    expect(onSelect).toHaveBeenCalledWith(RESULT)
  })

  it('pressing Enter searches, and the key press is stopped from submitting the surrounding form', async () => {
    const { submitted } = inForm()
    const input = screen.getByLabelText('Where to?')
    fireEvent.change(input, { target: { value: 'Kamakhya' } })
    const notPrevented = fireEvent.keyDown(input, { key: 'Enter' }) // false when preventDefault() was called
    expect(notPrevented).toBe(false)
    expect(await screen.findByText(/Kamakhya Temple/)).toBeInTheDocument()
    expect(submitted).not.toHaveBeenCalled()
    expect(mock.history.get.filter((c) => c.url === '/maps/search')).toHaveLength(1)
  })

  it('keeps what was typed (no reset/refresh) and shows no results for an empty query', async () => {
    inForm()
    const input = screen.getByLabelText('Where to?')
    fireEvent.keyDown(input, { key: 'Enter' }) // nothing typed
    expect(mock.history.get).toHaveLength(0)
    fireEvent.change(input, { target: { value: 'Kamakhya' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() => expect(screen.getByText(/Kamakhya Temple/)).toBeInTheDocument())
    expect(input).toHaveValue('Kamakhya')
  })

  it('other keys do not search', () => {
    inForm()
    const input = screen.getByLabelText('Where to?')
    fireEvent.change(input, { target: { value: 'Kam' } })
    fireEvent.keyDown(input, { key: 'a' })
    expect(mock.history.get).toHaveLength(0)
  })
})
