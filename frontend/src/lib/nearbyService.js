import api from '../api'

// Real places of one kind around a position (see backend/app/api/nearby.py). The position
// is the caller's -- the phone's GPS or a place the tourist chose -- never a stored one.
// `tzOffsetMin` (the user's own clock offset) lets the server work out "open now"; pass it
// only when the centre is the device's position, because a typed place may be elsewhere.
export function getNearby(lat, lng, category, radiusKm, { refresh = false, signal, tzOffsetMin } = {}) {
  return api
    .get('/nearby', {
      params: {
        lat, lng, category, radius_km: radiusKm,
        ...(refresh ? { refresh: true } : {}),
        ...(tzOffsetMin != null ? { tz_offset_min: tzOffsetMin } : {}),
      },
      timeout: 70_000, // the backup source answers one request per second
      signal,
    })
    .then((r) => r.data)
}

// Minutes this device's clock is ahead of UTC (India: 330).
export const deviceTzOffsetMin = () => -new Date().getTimezoneOffset()
