import { useCallback, useEffect, useRef, useState } from 'react'
import jsQR from 'jsqr'

// Camera QR reader built on jsQR (the decoder TouristIdScanner already uses --
// no new dependency). Calls `onCode(text)` once per start() and then stops the
// camera. `error` is 'unsupported' | 'denied' | '' so the UI can show the
// right fallback message.
export default function useQrCamera(onCode) {
  const [active, setActive] = useState(false)
  const [error, setError] = useState('')
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const rafRef = useRef(null)
  const canvasRef = useRef(null)
  const cbRef = useRef(onCode)
  cbRef.current = onCode

  const stop = useCallback(() => {
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = null
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setActive(false)
  }, [])

  useEffect(() => stop, [stop])

  const start = useCallback(async () => {
    setError('')
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('unsupported')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play?.()
      }
      setActive(true)
      if (!canvasRef.current) canvasRef.current = document.createElement('canvas')
      const canvas = canvasRef.current
      const ctx = canvas.getContext('2d', { willReadFrequently: true })
      const tick = () => {
        const video = videoRef.current
        if (!video || !streamRef.current) return
        if (video.readyState === video.HAVE_ENOUGH_DATA) {
          canvas.width = video.videoWidth
          canvas.height = video.videoHeight
          ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
          const frame = ctx.getImageData(0, 0, canvas.width, canvas.height)
          const code = jsQR(frame.data, frame.width, frame.height)
          if (code?.data) {
            stop()
            cbRef.current?.(code.data)
            return
          }
        }
        rafRef.current = requestAnimationFrame(tick)
      }
      rafRef.current = requestAnimationFrame(tick)
    } catch {
      setError('denied')
    }
  }, [stop])

  return { videoRef, active, error, start, stop }
}
