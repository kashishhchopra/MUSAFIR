import { SpeechRecognition as NativeSpeech } from '@capacitor-community/speech-recognition'
import { isNativeApp } from './platform'

// Microphone permission for the voice translator, one place for the Android app
// (the speech-recognition plugin's runtime permission) and the browser
// (getUserMedia). States: 'granted' | 'denied' | 'prompt' | 'unknown'.

export async function checkMicPermission() {
  if (isNativeApp()) {
    try {
      const { speechRecognition } = await NativeSpeech.checkPermissions()
      return speechRecognition === 'granted' ? 'granted' : speechRecognition === 'denied' ? 'denied' : 'prompt'
    } catch {
      return 'unknown'
    }
  }
  try {
    if (navigator.permissions?.query) {
      const s = await navigator.permissions.query({ name: 'microphone' })
      return s.state
    }
  } catch {
    // Firefox/Safari don't know the 'microphone' permission name -- fall through.
  }
  return 'unknown'
}

// Triggers the real OS/browser permission prompt. Resolves 'granted' or 'denied'.
export async function requestMicPermission() {
  if (isNativeApp()) {
    try {
      const { speechRecognition } = await NativeSpeech.requestPermissions()
      return speechRecognition === 'granted' ? 'granted' : 'denied'
    } catch {
      return 'denied'
    }
  }
  if (!navigator.mediaDevices?.getUserMedia) return 'unknown'
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.getTracks().forEach((track) => track.stop()) // only needed the grant
    return 'granted'
  } catch (e) {
    return e?.name === 'NotFoundError' ? 'no-mic' : 'denied'
  }
}
