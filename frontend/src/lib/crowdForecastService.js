import api from '../api'

// Estimated crowd levels around a real position (see backend/app/api/crowd_forecast.py and
// services/crowd_outlook.py). An estimate from real inputs -- never a live head-count.
// `tzOffsetMin` is only used by the server if the weather service cannot give the local time.
export function getCrowdOutlook(lat, lng, { tzOffsetMin, signal } = {}) {
  return api
    .get('/crowd/outlook', {
      params: { lat, lng, ...(tzOffsetMin != null ? { tz_offset_min: tzOffsetMin } : {}) },
      timeout: 40_000, // the free map/weather services can be slow; the server answers within ~10 s
      signal,
    })
    .then((r) => r.data)
}
