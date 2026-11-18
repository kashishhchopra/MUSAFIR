import { describe, it, expect, vi, beforeEach } from 'vitest'

const env = vi.hoisted(() => ({ native: false }))
vi.mock('./platform', () => ({ isNativeApp: () => env.native }))

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

  describe('opening a link', () => {
    let assign
    beforeEach(() => {
      env.native = false
      assign = vi.fn()
      vi.spyOn(window, 'location', 'get').mockReturnValue({ assign, href: '' })
    })

    it('opens a new tab in the browser and does not navigate the app away', () => {
      const open = vi.spyOn(window, 'open').mockReturnValue({})
      openDirections({ destination: { lat: 5, lng: 6 } })
      expect(open).toHaveBeenCalledWith(expect.stringContaining('google.com/maps/dir'), '_blank')
      expect(assign).not.toHaveBeenCalled()
    })

    it('falls back to same-tab navigation only when the popup was really blocked', () => {
      vi.spyOn(window, 'open').mockReturnValue(null)
      openDirections({ destination: { lat: 5, lng: 6 } })
      expect(assign).toHaveBeenCalledWith(expect.stringContaining('google.com/maps/dir'))
    })

    it('in the Android app hands the URL to the system via navigation (Maps app opens), never window.open', () => {
      env.native = true
      const open = vi.spyOn(window, 'open')
      openDirections({ destination: { lat: 5, lng: 6 } })
      expect(open).not.toHaveBeenCalled()
      expect(assign).toHaveBeenCalledWith(expect.stringContaining('google.com/maps/dir'))
    })
  })
})
