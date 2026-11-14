import { useEffect, useRef, useState } from 'react'

// Seconds until `deadline`, anchored on the SERVER's clock.
//
// `serverTime` is the server's "now" from the same API response as `deadline`.
// Remaining time = deadline - (serverTime + time elapsed since that response
// arrived, measured with performance.now()). The browser's own wall clock is
// never read, so a wrong device clock cannot move a deadline, and a refresh
// simply re-reads both values from the backend. This hook only ANIMATES the
// number; the backend enforces the deadline and does the escalating.
//
// Both timestamps come from one response, so even a timezone-less ISO string
// (as older fields are serialised) cancels out in the subtraction.
export default function useCountdown(deadline, serverTime, enabled = true) {
  const anchor = useRef({ perf: 0, serverMs: 0, key: null })
  const [, setTick] = useState(0)

  if (serverTime && anchor.current.key !== serverTime) {
    anchor.current = { perf: performance.now(), serverMs: Date.parse(serverTime), key: serverTime }
  }

  useEffect(() => {
    if (!enabled || !deadline) return undefined
    const iv = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(iv)
  }, [enabled, deadline])

  if (!deadline || !serverTime) return null
  const nowMs = anchor.current.serverMs + (performance.now() - anchor.current.perf)
  return (Date.parse(deadline) - nowMs) / 1000
}
