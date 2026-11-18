import { useEffect, useMemo, useState } from 'react'
import useCurrentLocation from './useCurrentLocation.js'
import { startDeviceLocation } from '../lib/deviceLocation.js'

// Where a location-based search is centred: the device's own GPS position, or a place
// the tourist typed when GPS is off / refused / times out. Never a stored or default
// coordinate. The device is asked as soon as the screen opens (the browser/OS shows its
// own permission prompt); `loc` carries the precise reason when it cannot answer.
export default function useSearchCenter() {
  const loc = useCurrentLocation({ quick: true })
  const [manual, setManual] = useState(null) // {name, lat, lng}
  const [changing, setChanging] = useState(false)

  // The shared store makes ONE request for the whole app (and may already hold a remembered fix).
  useEffect(() => { startDeviceLocation() }, [])

  const gpsLat = loc.position?.lat
  const gpsLng = loc.position?.lng
  // `precise`: a fresh device fix (not a remembered or approximate one). Only a precise position is
  // offered to Maps as the start of directions -- otherwise Maps is left to use the live position.
  const precise = loc.source === 'gps' && !loc.stale
  const gps = useMemo(
    () => (!manual && gpsLat != null ? { lat: gpsLat, lng: gpsLng, precise } : null), [manual, gpsLat, gpsLng, precise])
  const center = useMemo(
    () => (manual ? { lat: manual.lat, lng: manual.lng } : gps ? { lat: gps.lat, lng: gps.lng } : null), [manual, gps])

  const useMyLocation = () => { setManual(null); setChanging(false); loc.request() }
  const choose = (p) => { setManual({ name: p.name, lat: p.lat, lng: p.lng }); setChanging(false) }

  return { loc, manual, gps, center, changing, setChanging, useMyLocation, choose }
}
