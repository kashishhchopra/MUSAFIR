import { useEffect, useRef } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { App as CapApp } from '@capacitor/app'
import { useAuth } from '../auth.jsx'
import { onPushTap, resyncNativePush } from '../lib/pushService'

const EXIT_ON_BACK = ['/', '/app', '/login']

// Native-app glue (Android and iOS): the hardware back button (Android only --
// iOS has none, so that listener never fires), opening the right screen from a
// notification tap, and keeping this phone's push token registered.
export default function NativeBridge() {
  const navigate = useNavigate()
  const location = useLocation()
  const { user } = useAuth()
  const pathRef = useRef(location.pathname)
  pathRef.current = location.pathname

  useEffect(() => {
    const back = CapApp.addListener('backButton', ({ canGoBack }) => {
      if (!canGoBack || EXIT_ON_BACK.includes(pathRef.current)) CapApp.exitApp()
      else window.history.back()
    })
    const tap = onPushTap((path) => navigate(path))
    return () => {
      back.then((h) => h.remove())
      tap.then((h) => h.remove())
    }
  }, [navigate])

  useEffect(() => {
    if (user?.role === 'tourist') resyncNativePush()
  }, [user?.role])

  return null
}
