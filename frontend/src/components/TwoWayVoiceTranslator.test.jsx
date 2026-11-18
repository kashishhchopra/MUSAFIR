import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, waitFor, act, screen } from '@testing-library/react'
import MockAdapter from 'axios-mock-adapter'
import { useState } from 'react'
import '../i18n'
import api from '../api'
import TwoWayVoiceTranslator from './TwoWayVoiceTranslator'
import { classifyError } from '../hooks/useSpeechRecognition'

// Stand-in recognizer: tests push a "heard" transcript into the side that is
// listening in a given locale. start/stop/permission are spies.
const sides = {}
const startSpy = vi.fn()
vi.mock('../hooks/useSpeechRecognition.js', async (orig) => {
  const real = await orig()
  return {
    ...real,
    default: ({ lang }) => {
      const [transcript, setTranscript] = useState('')
      const [error, setError] = useState(null)
      sides[lang] = { setTranscript, setError }
      return {
        supported: true, listening: false, transcript, error,
        errorKind: error ? real.classifyError(error) : null,
        start: startSpy, stop: vi.fn(), reset: () => setTranscript(''), clearError: () => setError(null),
      }
    },
  }
})

const speakWithStatus = vi.fn(async () => 'ok')
vi.mock('../lib/voiceService.js', () => ({
  speakWithStatus: (...a) => speakWithStatus(...a),
  stopSpeaking: vi.fn(),
  speechLocale: (l) => ({ en: 'en-IN', hi: 'hi-IN', fr: 'fr-FR' }[l] || l),
  speak: vi.fn(),
  speechSynthesisSupported: () => true,
}))

const micState = { check: 'granted', request: 'granted' }
vi.mock('../lib/micPermission.js', () => ({
  checkMicPermission: async () => micState.check,
  requestMicPermission: vi.fn(async () => micState.request),
}))

const mock = new MockAdapter(api)
beforeEach(() => {
  mock.reset(); speakWithStatus.mockClear(); startSpy.mockClear()
  micState.check = 'granted'; micState.request = 'granted'
  localStorage.setItem('stsLocalsLanguage', 'hi')
  mock.onGet('/translate/live-languages').reply(200, { en: 'English', hi: 'Hindi', fr: 'French' })
})

describe('classifyError', () => {
  it.each([
    ['not-allowed', 'permission'], ['NotAllowedError', 'permission'], ['Missing permission', 'permission'],
    ['audio-capture', 'no-mic'], ['NotFoundError', 'no-mic'], ['network', 'network'],
    ['no-speech', 'no-speech'], ['No match', 'no-speech'], ['language-not-supported', 'language'],
    ['not-available', 'unavailable'], ['weird', 'failed'],
  ])('%s -> %s', (code, kind) => expect(classifyError(code)).toBe(kind))
  it('null for no error', () => expect(classifyError(null)).toBeNull())
})

describe('TwoWayVoiceTranslator', () => {
  it('explains and requests the microphone permission when it is not granted', async () => {
    micState.check = 'prompt'
    const { findByText } = render(<TwoWayVoiceTranslator />)
    expect(await findByText(/listens through your microphone only while you tap/i)).toBeInTheDocument()
    fireEvent.click(await findByText('Allow microphone'))
    const { requestMicPermission } = await import('../lib/micPermission.js')
    await waitFor(() => expect(requestMicPermission).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByText('Allow microphone')).not.toBeInTheDocument())
  })

  it('shows settings guidance when the microphone is blocked', async () => {
    micState.check = 'denied'
    const { findByText } = render(<TwoWayVoiceTranslator />)
    expect(await findByText(/Microphone access is blocked/)).toBeInTheDocument()
  })

  it('translates what one side said into the other side\'s language and reads it aloud', async () => {
    mock.onPost('/translate/text').reply(200, { text: 'स्टेशन कहाँ है?', demo: false })
    const { findByText } = render(<TwoWayVoiceTranslator />)
    await findByText('You')
    await act(async () => sides['en-IN'].setTranscript('Where is the station?'))
    expect(await findByText('स्टेशन कहाँ है?')).toBeInTheDocument()
    expect(JSON.parse(mock.history.post[0].data)).toMatchObject({
      text: 'Where is the station?', target_lang: 'hi', source_lang: 'en',
    })
    await waitFor(() => expect(speakWithStatus).toHaveBeenCalledWith('स्टेशन कहाँ है?', 'hi'))
    // the speaker's own words stay visible too
    expect(await findByText('Where is the station?')).toBeInTheDocument()
  })

  it('works in the other direction (traveler speaks Hindi, tourist hears English)', async () => {
    mock.onPost('/translate/text').reply(200, { text: 'Straight ahead', demo: false })
    const { findByText } = render(<TwoWayVoiceTranslator />)
    await findByText('Traveler')
    await act(async () => sides['hi-IN'].setTranscript('सीधे जाइए'))
    expect(await findByText('Straight ahead')).toBeInTheDocument()
    expect(JSON.parse(mock.history.post[0].data)).toMatchObject({ target_lang: 'en', source_lang: 'hi' })
    await waitFor(() => expect(speakWithStatus).toHaveBeenCalledWith('Straight ahead', 'en'))
  })

  it('never shows or speaks an untranslated sentence when live translation is down', async () => {
    mock.onPost('/translate/text').reply(200, { text: 'Where is the station?', demo: true, note: 'unavailable' })
    const { findByText, queryAllByText } = render(<TwoWayVoiceTranslator />)
    await findByText('You')
    await act(async () => sides['en-IN'].setTranscript('Where is the station?'))
    expect(await findByText('Translation is unavailable right now.')).toBeInTheDocument()
    expect(speakWithStatus).not.toHaveBeenCalled()
    expect(queryAllByText('Where is the station?')).toHaveLength(1) // only as "Said", not as a translation
    expect(screen.queryByText(/Play again/)).not.toBeInTheDocument()
  })

  it('reports a network failure instead of hanging', async () => {
    mock.onPost('/translate/text').networkError()
    const { findByText } = render(<TwoWayVoiceTranslator />)
    await findByText('You')
    await act(async () => sides['en-IN'].setTranscript('Hello'))
    expect(await findByText('Translation is unavailable right now.')).toBeInTheDocument()
  })

  it('refuses to "translate" when both sides use the same language', async () => {
    localStorage.setItem('stsLocalsLanguage', 'en')
    const { findByText } = render(<TwoWayVoiceTranslator />)
    await findByText('You')
    await act(async () => sides['en-IN'].setTranscript('Hello'))
    expect(await findByText(/same language/)).toBeInTheDocument()
    expect(mock.history.post).toHaveLength(0)
  })

  it('tells the user when the phone has no voice for the target language', async () => {
    speakWithStatus.mockResolvedValueOnce('unsupported-language')
    mock.onPost('/translate/text').reply(200, { text: 'Bonjour', demo: false })
    localStorage.setItem('stsLocalsLanguage', 'fr')
    const { findByText } = render(<TwoWayVoiceTranslator />)
    await findByText('You')
    await act(async () => sides['en-IN'].setTranscript('Hello'))
    expect(await findByText('Bonjour')).toBeInTheDocument()
    expect(await findByText(/no voice for this language/)).toBeInTheDocument()
  })

  it('surfaces recognition errors with a specific message and lets Play replay a translation', async () => {
    mock.onPost('/translate/text').reply(200, { text: 'नमस्ते', demo: false })
    const { findByText } = render(<TwoWayVoiceTranslator />)
    await findByText('You')
    await act(async () => sides['en-IN'].setError('not-allowed'))
    expect(await findByText(/Microphone access is blocked/)).toBeInTheDocument()
    await act(async () => { sides['en-IN'].setError(null); sides['en-IN'].setTranscript('Hello') })
    fireEvent.click(await findByText(/Play again/))
    await waitFor(() => expect(speakWithStatus).toHaveBeenCalledTimes(2))
  })

  it('tapping the mic button starts recognition', async () => {
    const { findAllByText } = render(<TwoWayVoiceTranslator />)
    fireEvent.click((await findAllByText(/Tap to speak/))[0])
    expect(startSpy).toHaveBeenCalled()
  })
})
