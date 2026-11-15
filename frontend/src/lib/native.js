// Is this build running inside the Capacitor iOS shell (vs. a normal browser)?
import { Capacitor } from '@capacitor/core'

export const isNative = () => {
  try { return Capacitor.isNativePlatform() } catch { return false }
}
