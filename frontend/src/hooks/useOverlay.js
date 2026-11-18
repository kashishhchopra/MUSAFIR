import { useEffect, useRef } from 'react'
import { registerBack, registerCloser } from '../lib/navBus'

// Call from any panel that opens over the screen. While `open`, the system Back
// button / Android back key closes it first, and Home closes it too.
export default function useOverlay(open, onClose) {
  const ref = useRef(onClose)
  ref.current = onClose
  useEffect(() => {
    if (!open) return undefined
    const offBack = registerBack(() => { ref.current?.(); return true })
    const offClose = registerCloser(() => ref.current?.())
    return () => { offBack(); offClose() }
  }, [open])
}
