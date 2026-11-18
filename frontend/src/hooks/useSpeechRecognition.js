import { useCallback, useRef, useState } from 'react'
import { SpeechRecognition as NativeSpeech } from '@capacitor-community/speech-recognition'
import { isNativeApp } from '../lib/platform'
import { requestMicPermission } from '../lib/micPermission'

// Thin wrapper over speech recognition. In a browser it is the Web Speech API
// (no Firefox; prefixed in Chrome/Safari); in the Android app it is the phone's
// own recognizer through a Capacitor plugin, behind the same interface. Callers
// must check `supported` and offer a typed fallback when false.
//
// `error` is the raw code; `errorKind` is what the UI should tell the user:
//   permission | no-mic | network | no-speech | language | unavailable | failed
// (null when there is no error).
export function classifyError(code) {
  const c = String(code || '').toLowerCase()
  if (!c) return null
  if (/not-?allowed|permission|denied|service-not-allowed/.test(c)) return 'permission'
  if (/audio-capture|notfound|no.?mic|microphone/.test(c)) return 'no-mic'
  if (/network|connection|timeout|server/.test(c)) return 'network'
  if (/no-speech|no.?match|nomatch|didn't hear|speech.?timeout/.test(c)) return 'no-speech'
  if (/language/.test(c)) return 'language'
  if (/not.?available|unavailable|not.?supported|service-unavailable|recognizer/.test(c)) return 'unavailable'
  return 'failed'
}

export default function useSpeechRecognition({ lang = 'en-IN' } = {}) {
  const [listening, setListening] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [error, setError] = useState(null)
  const recognitionRef = useRef(null)
  const native = isNativeApp()

  const SpeechRecognitionCtor =
    typeof window !== 'undefined'
      ? window.SpeechRecognition || window.webkitSpeechRecognition
      : null
  const supported = native || Boolean(SpeechRecognitionCtor)

  const startNative = useCallback(async () => {
    setListening(true)
    try {
      const { available } = await NativeSpeech.available()
      if (!available) {
        setError('not-available')
        return
      }
      // Real Android runtime permission prompt (RECORD_AUDIO).
      const { speechRecognition } = await NativeSpeech.requestPermissions()
      if (speechRecognition !== 'granted') {
        setError('not-allowed')
        return
      }
      const { matches } = await NativeSpeech.start({
        language: lang, maxResults: 1, partialResults: false, popup: false,
      })
      const heard = (matches?.[0] || '').trim()
      if (heard) setTranscript(heard)
      else setError('no-speech')
    } catch (e) {
      setError(e?.message || 'speech-recognition-error')
    } finally {
      setListening(false)
    }
  }, [lang])

  const beginRecognition = useCallback(() => {
    const recognition = new SpeechRecognitionCtor()
    recognition.lang = lang
    recognition.interimResults = false
    recognition.maxAlternatives = 1

    recognition.onresult = (event) => {
      const text = Array.from(event.results)
        .map((r) => r[0].transcript)
        .join(' ')
        .trim()
      setTranscript(text)
    }
    // A failure (mic permission denied, no microphone, the browser refusing a
    // second concurrent session) must surface, or the button would look stuck
    // on "Listening..." with no way to retry.
    recognition.onerror = (event) => {
      setError(event.error || 'speech-recognition-error')
      setListening(false)
    }
    recognition.onend = () => setListening(false)

    recognitionRef.current = recognition
    try {
      recognition.start()
      setListening(true)
    } catch (e) {
      // Some browsers throw synchronously (e.g. InvalidStateError) instead of
      // firing onerror -- must still be caught.
      setError(e?.name || 'speech-recognition-error')
      setListening(false)
    }
  }, [SpeechRecognitionCtor, lang])

  const start = useCallback(() => {
    if (!supported || listening) return
    setError(null)
    if (native) {
      startNative()
      return
    }

    // SpeechRecognition is supposed to trigger the browser's own mic prompt on
    // first use, but that has proven unreliable -- some builds silently do
    // nothing. Asking via getUserMedia first forces the standard permission UI;
    // SpeechRecognition then reuses that grant.
    if (navigator.mediaDevices?.getUserMedia) {
      navigator.mediaDevices.getUserMedia({ audio: true })
        .then((stream) => {
          stream.getTracks().forEach((t) => t.stop())
          beginRecognition()
        })
        .catch((e) => {
          setError(e?.name || 'not-allowed')
          setListening(false)
        })
      return
    }
    beginRecognition()
  }, [supported, listening, native, startNative, beginRecognition])

  const stop = useCallback(() => {
    if (native) {
      NativeSpeech.stop().catch(() => {})
      return
    }
    recognitionRef.current?.stop()
  }, [native])

  const reset = useCallback(() => setTranscript(''), [])
  const clearError = useCallback(() => setError(null), [])

  return {
    supported, listening, transcript, error, errorKind: classifyError(error),
    start, stop, reset, clearError, requestPermission: requestMicPermission,
  }
}
