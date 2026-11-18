import { describe, it, expect, vi } from 'vitest'
import { placeUrl, directionsUrl, openDirections } from './mapsLinks'

describe('mapsLinks', () => {
  it('builds a place link from real coordinates', () => {
    expect(placeUrl({ lat: 27.1751, lng: 78.0421 }))
      .toBe('https://www.google.com/maps/search/?api=1&query=27.1751,78.0421')
  })

  it('builds directions with origin, waypoints and travel mode', () => {
    const u = new URL(directionsUrl({
      origin: { lat: 1, lng: 2 }, destination: { lat: 5, lng: 6 },
      stops: [{ lat: 3, lng: 4 }, { lat: 4, lng: 5 }], mode: 'walking',
    }))
    expect(u.searchParams.get('origin')).toBe('1,2')
    expect(u.searchParams.get('destination')).toBe('5,6')
    expect(u.searchParams.get('waypoints')).toBe('3,4|4,5')
    expect(u.searchParams.get('travelmode')).toBe('walking')
  })

  it('omits origin so Maps starts from the live device location', () => {
    const u = new URL(directionsUrl({ destination: { lat: 5, lng: 6 } }))
    expect(u.searchParams.has('origin')).toBe(false)
    expect(u.searchParams.get('travelmode')).toBe('driving')
  })

  it('falls back to same-window navigation when popups are blocked', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    const loc = window.location
    delete window.location
    window.location = { href: '' }
    openDirections({ destination: { lat: 5, lng: 6 } })
    expect(window.location.href).toContain('google.com/maps/dir')
    window.location = loc
    open.mockRestore()
  })
})
