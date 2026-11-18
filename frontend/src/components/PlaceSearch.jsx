import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { searchPlaces, suggestPlaces } from '../lib/routeService.js'
import { openPlace } from '../lib/mapsLinks.js'

// Type a place name -> real candidate matches (Google/OpenStreetMap) -> tap the
// right one. Nothing is guessed: if the search finds nothing the tourist is
// told, and `onSelect` only ever receives a place with real coordinates.
// `selected` is {name, lat, lng} | null.
//
// With `autocomplete`, suggestions appear while typing (>= 3 characters, 450 ms
// after the last keystroke, previous request cancelled, answers cached) so the
// free OpenStreetMap services are never hit per keystroke. `near` ({lat,lng})
// only biases ranking towards the user.
const MIN_CHARS = 3
const DEBOUNCE_MS = 450
export default function PlaceSearch({ selected, onSelect, placeholder, autocomplete = false, near = null, label = null, icon = '🏁' }) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  const NONE = t('place_search.none', "No matching place found -- try a more specific name (add the city).")

  const cache = useRef(new Map())
  const abort = useRef(null)
  const typed = useRef(false)

  useEffect(() => {
    if (!autocomplete || !typed.current) return undefined
    const q = query.trim()
    if (q.length < MIN_CHARS) { setResults([]); setMessage(''); setBusy(false); return undefined }
    const hit = cache.current.get(q.toLowerCase())
    if (hit) { setResults(hit); setMessage(hit.length ? '' : NONE); return undefined }
    const timer = setTimeout(async () => {
      abort.current?.abort()
      const ctl = new AbortController()
      abort.current = ctl
      setBusy(true)
      try {
        const found = await suggestPlaces(q, { near, signal: ctl.signal })
        if (ctl.signal.aborted) return
        cache.current.set(q.toLowerCase(), found)
        setResults(found)
        setMessage(found.length ? '' : NONE)
      } catch (err) {
        if (ctl.signal.aborted || err?.code === 'ERR_CANCELED') return
        setMessage(t('place_search.failed', "Couldn't search for places right now. Check your connection and try again."))
      } finally {
        if (!ctl.signal.aborted) setBusy(false)
      }
    }, DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [query, autocomplete]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => abort.current?.abort(), [])

  const search = async (e) => {
    e?.preventDefault()
    if (!query.trim() || busy) return
    abort.current?.abort()
    setBusy(true)
    setMessage('')
    setResults([])
    try {
      const found = await searchPlaces(query.trim())
      if (!found.length) setMessage(NONE)
      setResults(found)
    } catch {
      setMessage(t('place_search.failed', "Couldn't search for places right now. Check your connection and try again."))
    } finally {
      setBusy(false)
    }
  }

  if (selected) {
    return (
      <div className="flex items-start justify-between gap-2 text-sm border border-slate-200 dark:border-slate-600 rounded-lg px-2 py-1.5">
        <span className="min-w-0">
          <span className="block text-xs text-slate-400">{icon} {label || t('place_search.destination', 'Destination')}</span>
          <span className="block truncate">{selected.name}</span>
        </span>
        <span className="flex gap-3 shrink-0 text-xs font-semibold text-sky-700 dark:text-sky-400">
          <button type="button" onClick={() => openPlace(selected)}>{t('location.open_in_maps', 'Open in Maps')}</button>
          <button type="button" onClick={() => { onSelect(null); setResults([]) }}>{t('place_search.change', 'Change')}</button>
        </span>
      </div>
    )
  }

  return (
    <div className="space-y-1.5">
      <form onSubmit={search} className="flex gap-2">
        <input value={query} onChange={(e) => { typed.current = true; setQuery(e.target.value) }} aria-label={placeholder}
          placeholder={placeholder || t('place_search.placeholder', 'Search a place')}
          className="flex-1 min-w-0 text-sm border border-slate-200 dark:border-slate-600 dark:bg-slate-700 rounded-lg px-2 py-1.5" />
        <button disabled={busy || !query.trim()}
          className="text-sm font-semibold bg-sky-600 hover:bg-sky-700 disabled:opacity-50 text-white px-3 rounded-lg">
          {busy ? '…' : t('place_search.search', 'Search')}
        </button>
      </form>
      {message && <div className="text-xs text-amber-700 dark:text-amber-300">{message}</div>}
      {results.length > 0 && (
        <ul className="border border-slate-100 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700">
          {results.map((r, i) => (
            <li key={`${r.lat},${r.lng},${i}`}>
              <button type="button" onClick={() => { onSelect(r); setResults([]) }}
                className="w-full text-left text-xs px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-slate-700/50">
                <span className="font-semibold">📍 {r.name.split(', ')[0]}</span>
                {r.name.includes(', ') && (
                  <span className="block text-slate-500 dark:text-slate-400 pl-5 truncate">
                    {r.name.slice(r.name.indexOf(', ') + 2)}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
