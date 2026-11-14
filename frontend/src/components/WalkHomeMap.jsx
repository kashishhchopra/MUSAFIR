import { useEffect } from 'react'
import { MapContainer, TileLayer, Marker, useMap } from 'react-leaflet'
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

function Follow({ pos }) {
  const map = useMap()
  useEffect(() => {
    if (pos) map.setView(pos, map.getZoom(), { animate: true })
  }, [pos?.[0], pos?.[1]]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

// Destination + the last position that was actually received (if any). Shared by
// the tourist's journey screen and the guardian's view.
export default function WalkHomeMap({ dest, pos, stale = false, height = 220 }) {
  const center = pos || dest || DEFAULT_MAP.center
  return (
    <div className="rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700" style={{ height }}
      data-testid="walk-map">
      <MapContainer center={center} zoom={15} style={{ height: '100%' }}>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains={TILE_SUBDOMAINS} />
        {dest && <Marker position={dest} icon={destIcon()} />}
        {pos && <Marker position={pos} icon={stale ? staleIcon() : liveIcon()} />}
        <Follow pos={pos} />
      </MapContainer>
    </div>
  )
}
