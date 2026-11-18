import { useCallback, useEffect, useRef, useState } from 'react'
import { getNearby } from '../lib/nearbyService'
import { reasonOf } from './useDiscovery'

export const NEARBY_CATEGORIES = ['hospital', 'pharmacy', 'police', 'transport']
const DEBOUNCE_MS = 300

// Real nearby places for ONE category at a time. Each category's answer is kept for the
// current centre + radius, so switching back is instant and costs no request; changing the
// centre or radius drops them all. Requests are debounced, cancelled when superseded, and a
// failure is reported as an error (offline / unavailable / failed) -- never as an empty list.
//   byCat[cat] = { status: 'loading' | 'ok' | 'error', places, error, meta }
export default function useNearby({ center, radiusKm, category, tzOffsetMin }) {
  const [byCat, setByCat] = useState({})
  const [nonce, setNonce] = useState({ n: 0, refresh: false })
  const ctl = useRef(null)
  const seq = useRef(0)
  const have = useRef({}) // categories answered for the current centre/radius

  const lat = center?.lat
  const lng = center?.lng

  // New place or radius: everything held is for a different question.
  useEffect(() => { have.current = {}; setByCat({}) }, [lat, lng, radiusKm])

  useEffect(() => {
    if (lat == null || lng == null) return undefined
    if (have.current[category] && !nonce.refresh) return undefined
    const id = ++seq.current
    ctl.current?.abort()
    const controller = new AbortController()
    ctl.current = controller
    setByCat((prev) => ({ ...prev, [category]: { places: [], ...(prev[category] || {}), status: 'loading', error: null } }))
    const timer = setTimeout(() => {
      getNearby(lat, lng, category, radiusKm, { refresh: nonce.refresh, signal: controller.signal, tzOffsetMin })
        .then((r) => {
          if (id !== seq.current) return
          have.current[category] = true
          setByCat((prev) => ({ ...prev, [category]: {
            status: 'ok', places: r.places, error: null,
            meta: { provider: r.provider, degraded: !!r.degraded, stale: !!r.stale, region: r.region },
          } }))
        })
        .catch((e) => {
          if (id !== seq.current || controller.signal.aborted || e?.code === 'ERR_CANCELED') return
          setByCat((prev) => ({ ...prev, [category]: { status: 'error', places: [], error: reasonOf(e) } }))
        })
    }, DEBOUNCE_MS)
    return () => { clearTimeout(timer); controller.abort() }
  }, [lat, lng, radiusKm, category, nonce, tzOffsetMin])

  const refresh = useCallback(() => { have.current = {}; setNonce((s) => ({ n: s.n + 1, refresh: true })) }, [])
  const retry = useCallback(() => { delete have.current[category]; setNonce((s) => ({ n: s.n + 1, refresh: false })) }, [category])

  return { byCat, refresh, retry }
}
