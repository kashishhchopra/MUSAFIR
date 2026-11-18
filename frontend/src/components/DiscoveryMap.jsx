import { useEffect, useRef } from 'react'
import { MapContainer, TileLayer, Marker, Polyline, Popup, useMap } from 'react-leaflet'
import { TILE_ATTRIBUTION, TILE_SUBDOMAINS, TILE_URL } from '../lib/mapConfig'
import { pin } from './mapIcons'
import { CATEGORY_COLOR, CATEGORY_ICON, formatKm } from './DiscoveryPlaceCard.jsx'

// Built on first use so importing this file never touches Leaflet until a map is drawn.
const icons = {}
const icon = (key, color, glyph) => (icons[key] ||= pin(color, glyph))
const hereIcon = () => icon('here', '#16a34a', '📍')
const placeIcon = (cat, styles) => icon(`${styles === DEFAULT_STYLES ? '' : 'x:'}${cat}`, styles[cat].color, styles[cat].glyph)
const DEFAULT_STYLES = Object.fromEntries(Object.keys(CATEGORY_ICON).map((c) => [c, { glyph: CATEGORY_ICON[c], color: CATEGORY_COLOR[c] }]))

// Re-frames only when the set of results (or the centre) really changes, not on every
// re-render -- otherwise the map would snap back while the user zooms and pans.
function Fit({ points, signature }) {
  const map = useMap()
  useEffect(() => {
    if (!points.length) return
    if (points.length === 1) map.setView(points[0], 14)
    else map.fitBounds(points, { padding: [28, 28] })
  }, [map, signature]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

function Focus({ place }) {
  const map = useMap()
  useEffect(() => { if (place) map.setView([place.lat, place.lng], Math.max(map.getZoom(), 15), { animate: true }) }, [map, place])
  return null
}

// The user's position plus every discovered place, with a different pin per category.
// Tap a pin for its details and directions; the map frames all the results.
// `styles` maps a place's category to its pin ({glyph, color}); `routePath` ([[lat,lng],...])
// draws a road route to the selected place. Both default to Discovery's own look.
export default function DiscoveryMap({ center, places, selectedId, onSelect, onDirections, height = 340, styles = DEFAULT_STYLES, routePath = null }) {
  const markers = useRef({})
  const shown = places.slice(0, 150)
  const points = [[center.lat, center.lng], ...shown.map((p) => [p.lat, p.lng])]
  const selected = shown.find((p) => p.id === selectedId) || null

  useEffect(() => {
    if (selectedId) markers.current[selectedId]?.openPopup?.()
  }, [selectedId])

  return (
    <div className="rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700" style={{ height }} data-testid="discovery-map">
      <MapContainer center={[center.lat, center.lng]} zoom={13} style={{ height: '100%' }}>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains={TILE_SUBDOMAINS} />
        <Marker position={[center.lat, center.lng]} icon={hereIcon()} />
        {shown.map((p) => (
          <Marker key={p.id} position={[p.lat, p.lng]} icon={placeIcon(p.category, styles)}
            ref={(m) => { if (m) markers.current[p.id] = m }}
            eventHandlers={{ click: () => onSelect?.(p.id) }}>
            <Popup>
              <div className="text-sm" data-testid="map-popup">
                <div className="font-semibold">{p.name}</div>
                <div className="text-xs text-slate-600">{p.kind} · {formatKm(p.distance_km)}</div>
                {p.address && <div className="text-xs text-slate-500 mt-0.5">{p.address}</div>}
                {p.phone && (
                  <a href={`tel:${p.phone.replace(/[^+\d]/g, '')}`} data-testid="popup-call" className="block text-xs font-semibold text-sky-700 mt-1">📞 {p.phone}</a>
                )}
                <button type="button" onClick={() => onDirections(p)} data-testid="popup-directions"
                  className="mt-1.5 text-xs font-semibold text-white bg-sky-600 rounded px-2 py-1">🧭 Directions</button>
              </div>
            </Popup>
          </Marker>
        ))}
        {routePath?.length > 1 && <Polyline positions={routePath} pathOptions={{ color: '#4f46e5', weight: 5, opacity: 0.85 }} />}
        <Fit points={points} signature={`${center.lat},${center.lng}|${shown.map((p) => p.id).join(',')}`} />
        <Focus place={selected} />
      </MapContainer>
    </div>
  )
}
