// Small geometry helpers for following a route: how far is a position from the
// planned path, and how much of the path is left. Pure functions (no I/O).

const R = 6371000
const rad = (d) => (d * Math.PI) / 180

export function distanceM(a, b) {
  const dLat = rad(b[0] - a[0])
  const dLng = rad(b[1] - a[1])
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLng / 2) ** 2
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(x)))
}

// Distance from point p to segment a-b (equirectangular projection: accurate to
// well under a metre over the few hundred metres between route vertices).
function pointToSegmentM(p, a, b) {
  const k = Math.cos(rad(p[0]))
  const px = p[1] * k; const py = p[0]
  const ax = a[1] * k; const ay = a[0]
  const bx = b[1] * k; const by = b[0]
  const dx = bx - ax; const dy = by - ay
  const len2 = dx * dx + dy * dy
  const u = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2))
  const cx = ax + u * dx; const cy = ay + u * dy
  return distanceM([py, px / k], [cy, cx / k])
}

// -> {offM, remainingM, totalM, fraction}: how far off the path the position is,
// and the path length still ahead of its nearest point. null for an unusable path.
export function progressAlong(path, pos) {
  if (!Array.isArray(path) || path.length < 2 || !pos) return null
  let best = Infinity; let bestIdx = 0
  const seg = []
  let total = 0
  for (let i = 0; i < path.length - 1; i += 1) {
    const l = distanceM(path[i], path[i + 1])
    seg.push(l)
    total += l
    const d = pointToSegmentM(pos, path[i], path[i + 1])
    if (d < best) { best = d; bestIdx = i }
  }
  let remaining = distanceM(pos, path[bestIdx + 1])
  for (let i = bestIdx + 1; i < seg.length; i += 1) remaining += seg[i]
  remaining = Math.min(remaining, total + best)
  return { offM: best, remainingM: remaining, totalM: total, fraction: total ? Math.min(1, remaining / total) : 0 }
}
