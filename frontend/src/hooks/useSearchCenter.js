import { useEffect, useMemo, useState } from 'react'
import useCurrentLocation from './useCurrentLocation.js'

// Where a location-based search is centred: the device's own GPS position, or a place
// the tourist typed when GPS is off / refused / times out. Never a stored or default
// coordinate. The device is asked as soon as the screen opens (the browser/OS shows its
// own permission prompt); `loc` carries the precise reason when it cannot answer.
export default function useSearchCenter() {
  const loc = useCurrentLocation({ quick: true })
  const [manual, setManual] = useState(null) // {name, lat, lng}
  const [changing, setChanging] = useState(false)

  useEffect(() => { loc.request() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const gpsLat = loc.position?.lat
  const gpsLng = loc.position?.lng
  const gps = useMemo(
    () => (!manual && gpsLat != null ? { lat: gpsLat, lng: gpsLng } : null), [manual, gpsLat, gpsLng])
  const center = useMemo(
    () => (manual ? { lat: manual.lat, lng: manual.lng } : gps), [manual, gps])

  const useMyLocation = () => { setManual(null); setChanging(false); loc.request() }
  const choose = (p) => { setManual({ name: p.name, lat: p.lat, lng: p.lng }); setChanging(false) }

  return { loc, manual, gps, center, changing, setChanging, useMyLocation, choose }
}
