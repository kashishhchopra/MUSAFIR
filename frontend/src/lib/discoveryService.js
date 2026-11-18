import api from '../api'

// Thin wrapper over /discovery/* (see backend/app/api/discovery.py). One category per
// call, so the screen can show each category as soon as it arrives instead of waiting
// for the slowest. `refresh` skips the server's cache; `signal` cancels a superseded request.
export function getDiscovery(lat, lng, radiusKm, categories, { refresh = false, signal } = {}) {
  return api
    .get('/discovery', {
      params: {
        lat, lng, radius_km: radiusKm,
        ...(categories ? { categories: categories.join(',') } : {}),
        ...(refresh ? { refresh: true } : {}),
      },
      timeout: 70_000, // the backup source answers one category at a time at ~1 request/second
      signal,
    })
    .then((r) => r.data)
}

export function getOfflineBundle(lat, lng, radiusKm) {
  return api
    .get('/discovery/offline-bundle', { params: { lat, lng, radius_km: radiusKm }, timeout: 70_000 })
    .then((r) => r.data)
}
