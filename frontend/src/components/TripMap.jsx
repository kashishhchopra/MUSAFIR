import { useEffect } from 'react'
import { MapContainer, TileLayer, Marker, Polyline, useMap } from 'react-leaflet'
import { DEFAULT_MAP } from '../config'
import { TILE_ATTRIBUTION, TILE_SUBDOMAINS, TILE_URL } from '../lib/mapConfig'
import { pin } from './mapIcons'

const icons = {}
const numbered = (n) => (icons[`n${n}`] ||= pin('#4f46e5', String(n)))
const hereIcon = () => (icons.here ||= pin('#16a34a', '📍'))

function Fit({ points }) {
  const map = useMap()
  useEffect(() => {
    if (!points.length) return
    if (points.length === 1) map.setView(points[0], 14)
    else map.fitBounds(points, { padding: [28, 28] })
  }, [map, points])
  return null
}

// A journey on a map: optional "you are here" start, numbered stops, and the
// route line between them. `lines` is a list of [[lat,lng],...] -- real routed
// geometry per leg when we have it; a leg without geometry is drawn as a
// dashed straight line (so an estimate never looks like a road route). `path`,
// when given, is one full saved polyline drawn instead of per-leg lines.
export default function TripMap({ here, stops, lines = [], path, height = 220 }) {
  const stopPts = stops.map((s) => [s.lat, s.lng])
  const all = [...(here ? [[here.lat, here.lng]] : []), ...stopPts]
  const chain = all
  return (
    <div className="rounded-xl overflow-hidden border border-slate-200 dark:border-slate-700" style={{ height }} data-testid="trip-map">
      <MapContainer center={all[0] || DEFAULT_MAP.center} zoom={13} style={{ height: '100%' }}>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} subdomains={TILE_SUBDOMAINS} />
        {here && <Marker position={[here.lat, here.lng]} icon={hereIcon()} />}
        {stops.map((s, i) => <Marker key={`${s.lat},${s.lng},${i}`} position={[s.lat, s.lng]} icon={numbered(i + 1)} />)}
        {path?.length > 1 && <Polyline positions={path} pathOptions={{ color: '#4f46e5', weight: 5, opacity: 0.85 }} />}
        {!path?.length && chain.slice(1).map((pt, i) => {
          const geometry = lines[i]
          return geometry?.length
            ? <Polyline key={`l${i}`} positions={geometry} pathOptions={{ color: '#4f46e5', weight: 5, opacity: 0.85 }} />
            : <Polyline key={`l${i}`} positions={[chain[i], pt]} pathOptions={{ color: '#94a3b8', weight: 3, dashArray: '6 8' }} />
        })}
        <Fit points={all} />
      </MapContainer>
    </div>
  )
}
