import { Capacitor } from '@capacitor/core'

// True inside the native app (Capacitor: Android or iOS), false in a normal browser.
export const isNativeApp = () => Capacitor.isNativePlatform()

// 'android' | 'ios' | 'web'
export const nativePlatform = () => Capacitor.getPlatform()
export const isIOSApp = () => isNativeApp() && nativePlatform() === 'ios'
