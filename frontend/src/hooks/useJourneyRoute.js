import { useCallback, useEffect, useRef, useState } from 'react'
import { getRoute } from '../lib/routeService'

// Real route from the tourist's live position to a chosen destination for one
// travel mode. Recalculates by itself when the destination, mode or the
// (rounded) start point changes; a late answer for an old request is ignored.
//   state: idle (nothing to route yet) | loading | ok | error
//   route: {distance_km, duration_min, geometry, demo, provider} | null
// `demo: true` means only a straight-line estimate was possible; callers must say
// so. Nothing is invented on failure: state becomes 'error' and `route` is null.
export default function useJourneyRoute({ from, to, mode }) {
  const [state, setState] = useState('idle')
  const [route, setRoute] = useState(null)
  const [error, setError] = useState('')
  const seq = useRef(0)
  const [nonce, setNonce] = useState(0)

  // ~100 m grid: GPS jitter must not trigger a new routing request.
  const key = from && to
    ? `${from.lat.toFixed(3)},${from.lng.toFixed(3)}|${to.lat},${to.lng}|${mode}|${nonce}` : null

  useEffect(() => {
    const id = ++seq.current
    if (!key) { setState('idle'); setRoute(null); setError(''); return }
    setState('loading')
    setRoute(null)
    setError('')
    getRoute(from, to, mode)
      .then((r) => { if (id === seq.current) { setRoute(r); setState('ok') } })
      .catch((e) => {
        if (id !== seq.current) return
        const d = e?.response?.data?.detail
        setError(typeof d === 'string' ? d : (e?.response ? 'failed' : 'offline'))
        setState('error')
      })
    return () => { seq.current += 1 }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps

  const retry = useCallback(() => setNonce((n) => n + 1), [])
  return { state, route, error, retry }
}
