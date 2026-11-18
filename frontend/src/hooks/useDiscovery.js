import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getDiscovery } from '../lib/discoveryService'

export const DISCOVERY_CATEGORIES = ['hidden_gem', 'regional_food', 'homestay']
const DEBOUNCE_MS = 350

// Why a request failed, in terms the screen can explain.
//   offline      the request never reached the server (no connection)
//   unavailable  the server could not reach the free map data services (HTTP 503)
//   failed       anything else
export function reasonOf(err) {
  if (!err?.response) return 'offline'
  return err.response.status === 503 ? 'unavailable' : 'failed'
}
const WORST = { unavailable: 3, offline: 2, failed: 1 }

// Real places around `center` ({lat,lng}) within `radiusKm`. The three categories are
// requested in parallel and appear as each one finishes. Changing the centre or the
// radius re-queries after a short pause (so tapping through radii never fires a burst);
// a late answer for an old query is discarded. An empty list is only ever "the map data
// has nothing here" -- a failure is reported as an error, never as an empty list.
export default function useDiscovery({ center, radiusKm }) {
  const [status, setStatus] = useState('idle') // idle | loading | ok | error
  const [byCat, setByCat] = useState({})
  const [failed, setFailed] = useState([])
  const [error, setError] = useState(null)
  const [meta, setMeta] = useState({ provider: null, degraded: false, stale: false, region: null })
  const run = useRef(0)
  const ctl = useRef(null)
  const [nonce, setNonce] = useState({ n: 0, refresh: false })

  const lat = center?.lat
  const lng = center?.lng

  useEffect(() => {
    const id = ++run.current
    ctl.current?.abort()
    if (lat == null || lng == null) {
      setStatus('idle'); setByCat({}); setFailed([]); setError(null)
      return undefined
    }
    const controller = new AbortController()
    ctl.current = controller
    setStatus('loading'); setByCat({}); setFailed([]); setError(null)
    const timer = setTimeout(() => {
      const fails = []
      let ok = 0
      Promise.all(DISCOVERY_CATEGORIES.map((cat) => getDiscovery(lat, lng, radiusKm, [cat],
        { refresh: nonce.refresh, signal: controller.signal })
        .then((r) => {
          if (id !== run.current) return
          ok += 1
          setByCat((prev) => ({ ...prev, [cat]: r.places }))
          setMeta((m) => ({
            provider: r.provider || m.provider,
            degraded: m.degraded || !!r.degraded,
            stale: m.stale || !!r.stale,
            region: r.region || m.region,
          }))
        })
        .catch((e) => {
          if (id !== run.current || controller.signal.aborted || e?.code === 'ERR_CANCELED') return
          fails.push({ cat, why: reasonOf(e) })
        }),
      )).then(() => {
        if (id !== run.current) return
        setFailed(fails.map((f) => f.cat))
        if (ok === 0 && fails.length) {
          setError(fails.map((f) => f.why).sort((a, b) => WORST[b] - WORST[a])[0])
          setStatus('error')
        } else setStatus('ok')
      })
    }, DEBOUNCE_MS)
    setMeta({ provider: null, degraded: false, stale: false, region: null })
    return () => { clearTimeout(timer); controller.abort() }
  }, [lat, lng, radiusKm, nonce])

  const refresh = useCallback(() => setNonce((s) => ({ n: s.n + 1, refresh: true })), [])
  const retry = useCallback(() => setNonce((s) => ({ n: s.n + 1, refresh: false })), [])

  const places = useMemo(
    () => DISCOVERY_CATEGORIES.flatMap((c) => byCat[c] || []).sort((a, b) => a.distance_km - b.distance_km),
    [byCat])
  const counts = useMemo(
    () => Object.fromEntries(DISCOVERY_CATEGORIES.map((c) => [c, (byCat[c] || []).length])), [byCat])
  const loaded = DISCOVERY_CATEGORIES.filter((c) => byCat[c])

  return { status, places, counts, loaded, failed, error, meta, refresh, retry }
}
