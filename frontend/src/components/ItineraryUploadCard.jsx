import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Card } from './ui.jsx'
import {
  uploadItineraryDocument, updateItineraryDocument, confirmItineraryDocument, createItineraryFromText,
} from '../lib/itineraryService.js'
import TripTravelInfo from './TripTravelInfo.jsx'
import { geocodePlace } from '../lib/mapsService.js'

// Itinerary document upload: PDF/DOCX/text/photo -> real text extraction
// (OCR for photos, via a local Tesseract engine -- see
// backend/app/services/itinerary_extract.py) -> heuristic structured parse
// (destinations/hotels/transport/activities). The tourist always reviews
// and can edit before anything is written into their real itinerary
// (Tourist.itinerary, what the map/route/AI-copilot already read from);
// extraction is a heuristic, not a guarantee, so this step is never skipped.
export default function ItineraryUploadCard({ touristId, onConfirmed }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [doc, setDoc] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [confirming, setConfirming] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [unresolvedCount, setUnresolvedCount] = useState(0)
  const [locating, setLocating] = useState(null) // index currently being located
  const [autoProgress, setAutoProgress] = useState(null) // {done, total} while auto-locating
  const fileRef = useRef(null)
  const [source, setSource] = useState('upload') // 'upload' | 'type'
  const [planText, setPlanText] = useState('')
  const [building, setBuilding] = useState(false)

  // Locate every destination the parser couldn't already place, one at a
  // time (politeness towards the free Nominatim geocoding service, and so
  // the tourist sees live progress rather than a long silent pause) --
  // this is what turns "18 stops need locating" into "done automatically"
  // instead of 18 manual taps.
  const autoLocateAll = async (destinations) => {
    const todo = destinations
      .map((d, i) => i)
      .filter((i) => destinations[i].lat == null && destinations[i].name?.trim())
    if (!todo.length) return
    setAutoProgress({ done: 0, total: todo.length })
    let current = destinations
    for (let n = 0; n < todo.length; n++) {
      const i = todo[n]
      try {
        const result = await geocodePlace(current[i].name.trim())
        current = current.map((d, j) => (j === i ? { ...d, lat: result.lat, lng: result.lng, location_demo: result.demo } : d))
        setDoc((prev) => prev && { ...prev, extracted: { ...prev.extracted, destinations: current } })
      } catch {
        // leave that one unresolved -- never fabricate a location
      }
      setAutoProgress({ done: n + 1, total: todo.length })
    }
    setAutoProgress(null)
  }

  const upload = async (file) => {
    setError('')
    setUploading(true)
    setConfirmed(false)
    try {
      const data = await uploadItineraryDocument(touristId, file)
      setDoc(data)
      if (data.status === 'failed') setError(data.error)
      else await autoLocateAll(data.extracted.destinations)
    } catch (err) {
      const timedOut = err.code === 'ECONNABORTED'
      setError(
        err.response?.data?.detail
          || (timedOut ? t('itinerary_upload.upload_timeout') : t('itinerary_upload.upload_failed'))
      )
    } finally {
      setUploading(false)
    }
  }

  // Typed natural language ("visit the Taj Mahal tomorrow, then Agra Fort at 5pm")
  // -> the same reviewable document an upload produces.
  const buildFromText = async (e) => {
    e?.preventDefault()
    if (!planText.trim() || building) return
    setError('')
    setBuilding(true)
    setConfirmed(false)
    try {
      const data = await createItineraryFromText(touristId, planText.trim())
      setDoc(data)
      if (data.status === 'failed') setError(data.error)
      else await autoLocateAll(data.extracted.destinations)
    } catch (err) {
      const detail = err.response?.data?.detail
      setError(typeof detail === 'string' ? detail : t('itinerary_upload.build_failed', "Couldn't build your itinerary. Check your connection and try again."))
    } finally {
      setBuilding(false)
    }
  }

  const setDestinations = (list) => setDoc((prev) => ({ ...prev, extracted: { ...prev.extracted, destinations: list } }))
  const updateField = (i, field, value) => setDestinations(
    doc.extracted.destinations.map((d, j) => (j === i ? { ...d, [field]: value || null } : d)),
  )
  const moveDestination = (i, dir) => {
    const list = [...doc.extracted.destinations]
    const j = i + dir
    if (j < 0 || j >= list.length) return
    ;[list[i], list[j]] = [list[j], list[i]]
    setDestinations(list)
  }

  const updateDestination = (i, name) => {
    const next = { ...doc, extracted: { ...doc.extracted, destinations: [...doc.extracted.destinations] } }
    // The old lat/lng belonged to the old name -- editing the text makes it
    // stale, so drop it rather than silently keep pointing at the wrong
    // place. The tourist re-locates it (or the auto-locate-on-blur below
    // finds it) before it can be saved into the real itinerary.
    next.extracted.destinations[i] = { ...next.extracted.destinations[i], name, lat: null, lng: null, location_demo: null }
    setDoc(next)
  }
  const removeDestination = (i) => {
    const next = { ...doc, extracted: { ...doc.extracted, destinations: doc.extracted.destinations.filter((_, j) => j !== i) } }
    setDoc(next)
  }
  const addDestination = () => {
    const next = { ...doc, extracted: { ...doc.extracted, destinations: [...doc.extracted.destinations, { name: '' }] } }
    setDoc(next)
  }

  const locateDestination = async (i) => {
    const name = doc.extracted.destinations[i].name.trim()
    if (!name) return
    setLocating(i)
    try {
      const result = await geocodePlace(name)
      const next = { ...doc, extracted: { ...doc.extracted, destinations: [...doc.extracted.destinations] } }
      next.extracted.destinations[i] = {
        ...next.extracted.destinations[i],
        lat: result.lat, lng: result.lng, location_demo: result.demo,
      }
      setDoc(next)
    } catch {
      // leave it unresolved -- never fabricate a location
    } finally {
      setLocating(null)
    }
  }

  const saveEdits = () => updateItineraryDocument(touristId, doc.id, doc.extracted)

  const confirm = async () => {
    setConfirming(true)
    setError('')
    try {
      // Compute the unresolved count from what we're actually sending
      // (rather than the PATCH response) -- the backend just persists this
      // same list verbatim, so this is exactly what it saw.
      const named = doc.extracted.destinations.filter((d) => d.name?.trim())
      const located = named.filter((d) => d.lat != null && d.lng != null)
      await saveEdits()
      await confirmItineraryDocument(touristId, doc.id)
      setUnresolvedCount(named.length - located.length)
      setConfirmed(true)
      onConfirmed?.()
    } catch {
      setError(t('itinerary_upload.save_failed'))
    } finally {
      setConfirming(false)
    }
  }

  const reset = () => {
    setDoc(null)
    setError('')
    setConfirmed(false)
    setUnresolvedCount(0)
    setPlanText('')
    if (fileRef.current) fileRef.current.value = ''
  }

  return (
    <div>
      <button onClick={() => setOpen((v) => !v)}
        className="w-full text-sm font-semibold text-sky-700 dark:text-sky-400 bg-sky-50 dark:bg-sky-900/30 rounded-xl py-2">
        {open ? t('itinerary_upload.toggle_hide') : t('itinerary_upload.toggle_show')}
      </button>

      {open && (
        <div className="mt-3">
          <Card title={t('itinerary_upload.card_title')}>
            {!doc && (
              <div className="flex gap-1.5 mb-3" role="tablist" aria-label={t('itinerary_upload.source', 'How do you want to add your itinerary?')}>
                {[['upload', t('itinerary_upload.tab_upload', '📄 Upload a file')], ['type', t('itinerary_upload.tab_type', '✍️ Type your plan')]].map(([key, label]) => (
                  <button key={key} type="button" role="tab" aria-selected={source === key} onClick={() => setSource(key)}
                    className={`flex-1 text-xs font-semibold py-1.5 rounded-lg border ${source === key
                      ? 'bg-sky-600 text-white border-sky-600' : 'border-slate-300 dark:border-slate-600 text-slate-600 dark:text-slate-300'}`}>
                    {label}
                  </button>
                ))}
              </div>
            )}

            {!doc && source === 'type' && (
              <form onSubmit={buildFromText} className="space-y-2">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {t('itinerary_upload.type_intro', 'Write your plans in your own words — one place or several, with dates and times if you like. We find each place on the map and work out the travel time from where you are.')}
                </p>
                <textarea value={planText} onChange={(e) => setPlanText(e.target.value)} rows={4} maxLength={2000}
                  aria-label={t('itinerary_upload.type_label', 'Your travel plan')}
                  placeholder={t('itinerary_upload.type_placeholder', 'e.g. I want to visit the Taj Mahal tomorrow morning, then Agra Fort at 5pm')}
                  className="w-full text-sm border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded-lg px-2 py-1.5" />
                {error && <div className="text-xs text-red-600 dark:text-red-400" role="alert">{error}</div>}
                <button disabled={building || !planText.trim()}
                  className="w-full text-sm font-semibold bg-sky-600 hover:bg-sky-700 disabled:opacity-50 text-white py-2 rounded-lg">
                  {building ? t('itinerary_upload.building', 'Finding your places…') : t('itinerary_upload.build', 'Build my itinerary')}
                </button>
              </form>
            )}

            {!doc && source === 'upload' && (
              <div className="space-y-2">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {t('itinerary_upload.intro')}
                </p>
                <input ref={fileRef} type="file"
                  accept=".pdf,.docx,.txt,text/plain,application/pdf,.doc,.jpg,.jpeg,.png,.webp,.bmp,.tiff,image/*"
                  onChange={(e) => e.target.files[0] && upload(e.target.files[0])}
                  disabled={uploading}
                  className="w-full text-xs file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-sky-600 file:text-white file:text-xs file:font-semibold" />
                {uploading && (
                  <div className="text-xs text-slate-400 flex items-center gap-1.5">
                    <span className="inline-block w-3 h-3 border-2 border-slate-300 border-t-sky-500 rounded-full animate-spin" />
                    {t('itinerary_upload.reading_document')}
                  </div>
                )}
                {error && <div className="text-xs text-red-600 dark:text-red-400">{error}</div>}
              </div>
            )}

            {doc && !confirmed && (
              <div className="space-y-3 text-sm">
                {error && (
                  <div className="text-xs text-orange-700 dark:text-orange-300 bg-orange-50 dark:bg-orange-900/30 rounded-lg p-2">
                    ⚠ {error}
                  </div>
                )}
                <div>
                  <div className="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1.5">
                    {t('itinerary_upload.destinations_help')}
                  </div>
                  {autoProgress && (
                    <div className="text-xs text-sky-600 dark:text-sky-400 mb-1.5">
                      {t('itinerary_upload.locating_progress', { done: autoProgress.done, total: autoProgress.total })}
                    </div>
                  )}
                  <div className="space-y-1.5">
                    {doc.extracted.destinations.map((d, i) => (
                      <div key={i} className="space-y-1">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs text-slate-400 w-4">{i + 1}.</span>
                        <input value={d.name} onChange={(e) => updateDestination(i, e.target.value)}
                          disabled={!!autoProgress}
                          className="flex-1 border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded-lg px-2 py-1 text-xs disabled:opacity-60" />
                        {d.lat != null ? (
                          <span className="text-[10px] text-green-600 dark:text-green-400" title={t('itinerary_upload.location_found')}>📍✓</span>
                        ) : (
                          <button onClick={() => locateDestination(i)} disabled={locating === i || !!autoProgress || !d.name?.trim()}
                            className="text-[10px] font-semibold text-orange-600 dark:text-orange-400 disabled:opacity-50 whitespace-nowrap"
                            title={t('itinerary_upload.locate_tooltip')}>
                            {locating === i ? t('itinerary_upload.locating') : t('itinerary_upload.locate_button')}
                          </button>
                        )}
                        <button onClick={() => moveDestination(i, -1)} disabled={!!autoProgress || i === 0}
                          aria-label={t('itinerary_upload.move_up', 'Move up')} className="text-xs text-slate-500 disabled:opacity-30">▲</button>
                        <button onClick={() => moveDestination(i, 1)} disabled={!!autoProgress || i === doc.extracted.destinations.length - 1}
                          aria-label={t('itinerary_upload.move_down', 'Move down')} className="text-xs text-slate-500 disabled:opacity-30">▼</button>
                        <button onClick={() => removeDestination(i)} disabled={!!autoProgress}
                          aria-label={t('itinerary_upload.remove', 'Remove stop')} className="text-xs text-red-500 disabled:opacity-50">✕</button>
                      </div>
                      <div className="flex gap-1.5 pl-5">
                        <input type="date" value={d.date || ''} onChange={(e) => updateField(i, 'date', e.target.value)}
                          aria-label={t('itinerary_upload.date_for', 'Date for stop {{n}}', { n: i + 1 })}
                          className="border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded-lg px-1.5 py-0.5 text-[11px]" />
                        <input type="time" value={d.time || ''} onChange={(e) => updateField(i, 'time', e.target.value)}
                          aria-label={t('itinerary_upload.time_for', 'Preferred time for stop {{n}}', { n: i + 1 })}
                          className="border border-slate-300 dark:border-slate-600 dark:bg-slate-900 rounded-lg px-1.5 py-0.5 text-[11px]" />
                      </div>
                      </div>
                    ))}
                    {doc.extracted.destinations.length === 0 && (
                      <div className="text-xs text-slate-400">{t('itinerary_upload.no_destinations')}</div>
                    )}
                    <div className="flex gap-3">
                      <button onClick={addDestination} disabled={!!autoProgress} className="text-xs text-sky-600 dark:text-sky-400 font-semibold disabled:opacity-50">{t('itinerary_upload.add_destination')}</button>
                      {doc.extracted.destinations.some((d) => d.lat == null && d.name?.trim()) && (
                        <button onClick={() => autoLocateAll(doc.extracted.destinations)} disabled={!!autoProgress}
                          className="text-xs text-sky-600 dark:text-sky-400 font-semibold disabled:opacity-50">
                          {t('itinerary_upload.locate_all')}
                        </button>
                      )}
                    </div>
                  </div>
                </div>

                {doc.extracted.hotels.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('itinerary_upload.hotels_label')}</div>
                    {doc.extracted.hotels.map((h, i) => (
                      <div key={i} className="text-xs text-slate-600 dark:text-slate-300">🏨 {h.name}</div>
                    ))}
                  </div>
                )}
                {doc.extracted.transport.length > 0 && (
                  <div>
                    <div className="text-xs font-semibold text-slate-500 dark:text-slate-400 mb-1">{t('itinerary_upload.transport_label')}</div>
                    {doc.extracted.transport.map((tp, i) => (
                      <div key={i} className="text-xs text-slate-600 dark:text-slate-300">🚄 {tp.detail}</div>
                    ))}
                  </div>
                )}
                {(doc.extracted.trip_start || doc.extracted.trip_end) && (
                  <div className="text-xs text-slate-500 dark:text-slate-400">
                    {t('itinerary_upload.trip_dates', { start: doc.extracted.trip_start || '—', end: doc.extracted.trip_end || '—' })}
                  </div>
                )}

                <TripTravelInfo touristId={touristId} stops={doc.extracted.destinations} />

                <div className="flex gap-2 pt-1">
                  <button onClick={confirm} disabled={confirming || !!autoProgress}
                    className="flex-1 bg-sky-600 hover:bg-sky-700 disabled:opacity-60 text-white text-xs font-semibold py-2 rounded-lg">
                    {confirming ? t('itinerary_upload.saving') : t('itinerary_upload.confirm_save')}
                  </button>
                  <button onClick={reset} disabled={!!autoProgress} className="text-xs text-slate-400 disabled:opacity-50">{t('itinerary_upload.start_over')}</button>
                </div>
              </div>
            )}

            {confirmed && (
              <div className="text-center py-2 space-y-2">
                <div className="text-green-600 dark:text-green-400 text-sm font-semibold">{t('itinerary_upload.saved')}</div>
                {unresolvedCount > 0 && (
                  <div className="text-xs text-orange-600 dark:text-orange-400 max-w-xs mx-auto">
                    {t('itinerary_upload.unresolved_warning', { count: unresolvedCount })}
                  </div>
                )}
                <div className="text-left"><TripTravelInfo touristId={touristId} stops={doc.extracted.destinations} /></div>
                <button onClick={reset} className="text-xs text-sky-600 dark:text-sky-400">{t('itinerary_upload.upload_another')}</button>
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  )
}
