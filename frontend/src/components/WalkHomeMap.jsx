import { useEffect } from 'react'
import { MapContainer, TileLayer, Marker, Polyline, useMap } from 'react-leaflet'
import { DEFAULT_MAP } from '../config'
import { TILE_ATTRIBUTION, TILE_SUBDOMAINS, TILE_URL } from '../lib/mapConfig'
import { pin } from './mapIcons'

// Built on first use (not at import time) so merely importing this component --
// e.g. from the guardian page -- never touches Leaflet until a map is drawn.
const icons = {}
const icon = (key, color, glyph = '') => (icons[key] ||= pin(color, glyph))
const destIcon = () => icon('dest', '#0284c7', '🏁')
const liveIcon = () => icon('live', '#16a34a')
const staleIcon = () => icon('stale', '#94a3b8') // a stale fix is drawn grey, never as a live dot

function Follow({ pos, fit }) {
  const map = useMap()
  // With a route, frame the whole journey once; without one, follow the live dot.
  useEffect(() => {
    if (fit?.length > 1) map.fitBounds(fit, { padding: [24, 24] })
  }, [fit?.length]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (pos && !(fit?.length > 1)) map.setView(pos, map.getZoom(), { animate: true })
  }, [pos?.[0], pos?.[1]]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

// Destination + the last position that was actually received (if any). Shared by
// the tourist's journey screen and the guardian's view.
export default function WalkHomeMap({ dest, pos, stale = false, height = 220, path = null, here = null }) {
  const center = pos || here || dest || DEFAULT_MAP.center
  return (
    <div className="rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700" style={{ height }}
      data-testid="walk-map">
      <MapContainer center={center} zoom={15} style={{ height: '100%' }}>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains={TILE_SUBDOMAINS} />
        {dest && <Marker position={dest} icon={destIcon()} />}
        {path?.length > 1 && <Polyline positions={path} pathOptions={{ color: '#4f46e5', weight: 5, opacity: 0.85 }} />}
        {here && !pos && <Marker position={here} icon={liveIcon()} />}
        {pos && <Marker position={pos} icon={stale ? staleIcon() : liveIcon()} />}
        <Follow pos={pos} fit={path?.length > 1 ? path : (here && dest ? [here, dest] : null)} />
      </MapContainer>
    </div>
  )
}
