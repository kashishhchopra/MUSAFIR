import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import useSpeechRecognition from '../hooks/useSpeechRecognition.js'
import { speakWithStatus, stopSpeaking, speechLocale } from '../lib/voiceService.js'
import { checkMicPermission, requestMicPermission } from '../lib/micPermission.js'
import {
  listLiveLanguages, listLanguages, translateText as apiTranslateText,
} from '../lib/translationService.js'
import { useLocalsLanguage } from '../lib/touristMode.js'

// Two-way live voice translator: a tourist and a local/foreign traveler talk
// face-to-face -- each side taps, speaks in their own language, and the other
// side sees the translation and hears it read aloud. Real speech recognition
// (the phone's recognizer / Web Speech API), real translation (/translate/text:
// Google Cloud Translation when keyed, otherwise MyMemory) and real
// text-to-speech. If live translation is unavailable the card says so; it never
// shows or speaks an untranslated sentence as if it were a translation.

const ERROR_KEYS = {
  permission: ['voice_translator.err_permission', 'Microphone access is blocked. Allow the microphone for MUSAFIR in your phone settings (Settings → Apps → MUSAFIR → Permissions → Microphone), then tap again.'],
  'no-mic': ['voice_translator.err_no_mic', "No working microphone was found. Check that nothing else is using it."],
  network: ['voice_translator.err_network', 'Speech recognition needs an internet connection. Check your connection and try again.'],
  'no-speech': ['voice_translator.err_no_speech', "I didn't hear anything. Tap the button and speak clearly."],
  language: ['voice_translator.err_language', "This language isn't supported for voice input on this phone. Pick another language, or install it in your phone's speech settings."],
  unavailable: ['voice_translator.err_unavailable', "Speech recognition isn't available on this phone. Install or enable Google's speech services and try again."],
  failed: ['voice_translator.mic_error', "Couldn't access the microphone — check mic permission and try again."],
}

function Speaker({ label, lang, onLangChange, languages, text, onHeard, active, onActivate, busy, replay }) {
  const { t } = useTranslation()
  const sr = useSpeechRecognition({ lang: speechLocale(lang) })
  const { transcript, reset, listening, stop } = sr

  useEffect(() => {
    if (transcript) {
      onHeard(transcript)
      reset()
    }
  }, [transcript]) // eslint-disable-line react-hooks/exhaustive-deps

  // The other side took the mic -> stop this one.
  useEffect(() => {
    if (!active && listening) stop()
  }, [active, listening, stop])

  const toggle = () => {
    if (listening) { stop(); return }
    stopSpeaking() // never listen over our own read-aloud
    sr.clearError()
    onActivate()
    sr.start()
  }

  const [msgKey, msgDefault] = ERROR_KEYS[sr.errorKind] || []
  return (
    <div className="border border-slate-100 dark:border-slate-700 rounded-lg p-3">
      <div className="flex items-center justify-between mb-2 gap-2">
        <span className="text-xs font-semibold text-slate-600 dark:text-slate-300">{label}</span>
        <select value={lang} onChange={(e) => onLangChange(e.target.value)} aria-label={`${label} — ${t('voice_translator.language', 'language')}`}
          disabled={listening}
          className="text-xs border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-1.5 py-1 max-w-[55%]">
          {Object.entries(languages).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
        </select>
      </div>
      <button
        onClick={toggle}
        disabled={!sr.supported || busy}
        className={`w-full text-sm font-semibold py-2.5 rounded-lg text-white disabled:opacity-50 ${listening ? 'bg-red-600 animate-pulse' : 'bg-sky-600 hover:bg-sky-700'}`}>
        {!sr.supported
          ? t('voice_translator.unsupported')
          : listening
            ? `⏹ ${t('voice_translator.stop', 'Stop')} — ${t('voice_translator.listening')}`
            : `🎙 ${t('voice_translator.tap_to_speak')}`}
      </button>
      {sr.errorKind && (
        <div className="mt-2 text-xs text-red-600 dark:text-red-400" role="alert">{t(msgKey, msgDefault)}</div>
      )}
      {text && (
        <div className="mt-2 text-sm bg-slate-50 dark:bg-slate-700/50 rounded-lg p-2">
          <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-0.5">{t('voice_translator.said', 'Said')}</div>
          {text}
        </div>
      )}
      {replay}
    </div>
  )
}

export default function TwoWayVoiceTranslator() {
  const { t, i18n } = useTranslation()
  const [languages, setLanguages] = useState({})
  // "You" defaults to the tourist's own app language; "Traveler" is a persisted
  // choice shared with every other translator card -- see lib/touristMode.js.
  const [langA, setLangA] = useState(i18n.resolvedLanguage || i18n.language || 'en')
  const [langB, setLangB] = useLocalsLanguage()
  const [heard, setHeard] = useState({ A: '', B: '' })        // what each side said
  const [translated, setTranslated] = useState({ A: '', B: '' }) // what each side hears
  const [active, setActive] = useState(null)                  // 'A' | 'B' | null (who has the mic)
  const [translating, setTranslating] = useState(false)
  const [speaking, setSpeaking] = useState(null)              // side whose translation is being read
  const [error, setError] = useState('')
  const [mic, setMic] = useState('unknown')                   // granted|denied|prompt|unknown|no-mic
  const [asking, setAsking] = useState(false)
  const reqId = useRef(0)

  useEffect(() => {
    let cancelled = false
    listLiveLanguages().catch(() => listLanguages())
      .then((l) => { if (!cancelled) setLanguages(l) })
      .catch(() => { if (!cancelled) setError(t('voice_translator.failed')) })
    checkMicPermission().then((s) => { if (!cancelled) setMic(s) })
    return () => { cancelled = true; stopSpeaking() }
  }, [t])

  const allowMic = async () => {
    setAsking(true)
    setMic(await requestMicPermission())
    setAsking(false)
  }

  const say = useCallback(async (side, text, lang) => {
    setSpeaking(side)
    const status = await speakWithStatus(text, lang)
    setSpeaking((s) => (s === side ? null : s))
    if (status === 'unsupported-language') setError(t('voice_translator.err_tts_language', "Your phone has no voice for this language, so the translation can't be read aloud. You can still read it on screen — or install the voice in Settings → Language → Text-to-speech."))
    else if (status === 'no-tts') setError(t('voice_translator.err_no_tts', "This phone can't read text aloud. Read the translation on screen."))
    else if (status === 'failed') setError(t('voice_translator.err_tts_failed', "Couldn't play the translation aloud. Tap Play to retry."))
  }, [t])

  // `from` side spoke `text`: translate to the other side's language, show it, speak it.
  const handleHeard = (from) => async (text) => {
    const to = from === 'A' ? 'B' : 'A'
    const fromLang = from === 'A' ? langA : langB
    const toLang = from === 'A' ? langB : langA
    setError('')
    setHeard((h) => ({ ...h, [from]: text }))
    setTranslated((tr) => ({ ...tr, [to]: '' }))
    setActive(null)
    if (fromLang === toLang) {
      setError(t('voice_translator.same_language', 'Both sides are set to the same language -- pick different languages to translate.'))
      return
    }
    const id = ++reqId.current
    setTranslating(true)
    try {
      const r = await apiTranslateText(text, toLang, fromLang)
      if (id !== reqId.current) return
      if (r.demo) {
        // The server could not reach a translation engine and sent the original text back.
        setError(t('voice_translator.failed'))
        return
      }
      setTranslated((tr) => ({ ...tr, [to]: r.text }))
      await say(to, r.text, toLang)
    } catch {
      if (id === reqId.current) setError(t('voice_translator.failed'))
    } finally {
      if (id === reqId.current) setTranslating(false)
    }
  }

  const replay = (side, lang) => translated[side] && (
    <div className="mt-2 text-sm bg-sky-50 dark:bg-sky-900/20 rounded-lg p-2">
      <div className="text-[10px] uppercase tracking-wide text-sky-600 dark:text-sky-300 mb-0.5">{t('voice_translator.hears', 'Hears (translated)')}</div>
      <div>{translated[side]}</div>
      <div className="mt-1.5 flex gap-3 text-xs font-semibold text-sky-700 dark:text-sky-300">
        {speaking === side
          ? <button type="button" onClick={() => { stopSpeaking(); setSpeaking(null) }}>⏹ {t('voice_translator.stop_audio', 'Stop audio')}</button>
          : <button type="button" onClick={() => say(side, translated[side], lang)}>🔊 {t('voice_translator.play', 'Play again')}</button>}
      </div>
    </div>
  )

  const needsPermission = mic === 'denied' || mic === 'prompt' || mic === 'no-mic'
  return (
    <Card title={t('voice_translator.card_title')} icon="🎙️" iconColor="bg-cyan-50 text-cyan-600 dark:bg-cyan-900/30 dark:text-cyan-300">
      <p className="text-xs text-slate-500 dark:text-slate-400 mb-3">{t('voice_translator.intro')}</p>

      {needsPermission && (
        <div className="mb-3 text-xs bg-sky-50 dark:bg-sky-900/20 rounded-lg p-2.5" role="region" aria-label={t('voice_translator.permission_title', 'Microphone permission')}>
          <div className="font-semibold text-slate-700 dark:text-slate-200 mb-0.5">🎙 {t('voice_translator.permission_title', 'Microphone permission')}</div>
          <div className="text-slate-600 dark:text-slate-300 mb-1.5">
            {mic === 'denied'
              ? t(ERROR_KEYS.permission[0], ERROR_KEYS.permission[1])
              : mic === 'no-mic'
                ? t(ERROR_KEYS['no-mic'][0], ERROR_KEYS['no-mic'][1])
                : t('voice_translator.permission_why', 'The translator listens through your microphone only while you tap the button, to turn your speech into text and translate it. Audio is handled by your phone\'s speech service.')}
          </div>
          {mic !== 'no-mic' && (
            <button type="button" onClick={allowMic} disabled={asking}
              className="font-semibold bg-sky-600 hover:bg-sky-700 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg">
              {asking ? t('voice_translator.asking', 'Waiting for permission…') : t('voice_translator.allow_mic', 'Allow microphone')}
            </button>
          )}
        </div>
      )}

      {error && <div className="text-sm text-red-600 dark:text-red-400 mb-2" role="alert">{error}</div>}
      {translating && (
        <div className="text-xs text-slate-500 dark:text-slate-400 mb-2 flex items-center gap-1.5" role="status">
          <span className="inline-block w-3 h-3 border-2 border-slate-300 border-t-sky-500 rounded-full animate-spin" />
          {t('voice_translator.translating', 'Translating…')}
        </div>
      )}
      {Object.keys(languages).length > 0 && (
        <div className="grid grid-cols-1 gap-2">
          <Speaker label={t('voice_translator.you')} lang={langA} onLangChange={setLangA} languages={languages}
            text={heard.A} active={active === 'A'} onActivate={() => setActive('A')} busy={translating}
            onHeard={handleHeard('A')} replay={replay('A', langA)} />
          <Speaker label={t('voice_translator.traveler')} lang={langB} onLangChange={setLangB} languages={languages}
            text={heard.B} active={active === 'B'} onActivate={() => setActive('B')} busy={translating}
            onHeard={handleHeard('B')} replay={replay('B', langB)} />
        </div>
      )}
    </Card>
  )
}
