// On-device fall / crash detector. Pure logic -- no DOM, no timers -- so it is
// unit-testable with synthetic samples (fallDetector.test.js) and the same code
// runs in the browser (hooks/useMotionSensor.js feeds it real devicemotion).
//
// Every threshold comes in through `cfg`, which the app fetches from the
// backend (GET /api/fall-detection/config <- FALL_* settings). Nothing here is
// hardcoded: change an env var on the server and the detector changes.
//
// What it looks for (and what it deliberately ignores):
//   fall   free-fall (|a| below freefall_g for >= freefall_min_ms), then an
//          impact (|a| >= impact_g within freefall_impact_window_ms), then the
//          phone staying still. A hard impact (>= crash_impact_g) with no
//          free-fall phase also counts, as a stiff fall onto the device.
//   crash  an impact >= crash_impact_g while GPS speed was recently >=
//          crash_min_speed_kmh, then stillness. Needs a real speed reading --
//          with none, a hard impact is classed as a fall, never as a crash.
//   ignored  walking/running/jolts (no free-fall + impact, or no stillness
//          afterwards), a phone dropped and immediately picked up (it moves
//          again), and anything inside the cooldown after a detection.
//
// It cannot detect every fall or crash and will sometimes fire on a phone
// thrown onto a bed -- which is exactly why a detection only opens a 30 s
// "Are you OK?" countdown the user can cancel, never an alert by itself.

export const G = 9.80665 // m/s^2 per g -- a physical constant, not a tunable

export function createFallDetector(cfg, onDetect, { speedWindowMs = 10_000 } = {}) {
  let state = 'idle' // idle | observing
  let ffStart = null // current free-fall run
  let ffLast = null
  let ffDone = null // { start, end } of the last completed free-fall
  let impact = null // { t, peak, type, freefallMs }
  let quietStart = null
  let cooldownUntil = -Infinity
  let speeds = [] // [{ t, kmh }] within speedWindowMs
  let firstT = null
  let lastT = null
  let count = 0

  const recentSpeed = (t) => {
    speeds = speeds.filter((s) => t - s.t <= speedWindowMs)
    return speeds.length ? Math.max(...speeds.map((s) => s.kmh)) : null
  }

  const reset = () => {
    state = 'idle'
    ffStart = ffLast = ffDone = impact = quietStart = null
  }

  // GPS speed in km/h, or null when unknown. Only the max over the last
  // speedWindowMs matters: after a crash the speed drops to ~0 immediately.
  const setSpeed = (kmh, t) => {
    if (typeof kmh === 'number' && Number.isFinite(kmh) && kmh >= 0) speeds.push({ t, kmh })
  }

  // sample: { t (ms, monotonic), ax, ay, az (in g), gyro (deg/s magnitude | null) }
  const push = ({ t, ax, ay, az, gyro = null }) => {
    if (![t, ax, ay, az].every(Number.isFinite)) return
    if (firstT === null) firstT = t
    lastT = t
    count += 1
    if (t < cooldownUntil) return

    const mag = Math.hypot(ax, ay, az)

    if (state === 'idle') {
      // --- free-fall tracking
      if (mag < cfg.freefall_g) {
        if (ffStart === null) ffStart = t
        ffLast = t
      } else if (ffStart !== null) {
        if (ffLast - ffStart >= cfg.freefall_min_ms) ffDone = { start: ffStart, end: ffLast }
        ffStart = ffLast = null
      }

      // --- impact
      if (mag >= cfg.impact_g) {
        const freeFell = ffDone && t - ffDone.end <= cfg.freefall_impact_window_ms
        const hard = mag >= cfg.crash_impact_g
        const speed = recentSpeed(t)
        let type = null
        if (hard && speed !== null && speed >= cfg.crash_min_speed_kmh) type = 'crash'
        else if (freeFell || hard) type = 'fall'
        if (type) {
          impact = {
            t, peak: mag, type,
            freefallMs: freeFell ? ffDone.end - ffDone.start : 0,
            speedKmh: speed,
          }
          state = 'observing'
          quietStart = null
        }
      }
      return
    }

    // --- observing: the phone must now stay still, or it was just a jolt
    impact.peak = Math.max(impact.peak, mag)
    const still = Math.abs(mag - 1) <= cfg.stillness_dev_g
      && (gyro === null || gyro <= cfg.stillness_gyro_dps)
    if (still) {
      if (quietStart === null) quietStart = t
      if (t - quietStart >= cfg.stillness_ms) {
        const hit = {
          type: impact.type,
          peak_g: round(impact.peak, 2),
          freefall_ms: Math.round(impact.freefallMs),
          stillness_ms: Math.round(t - quietStart),
          speed_kmh: impact.speedKmh === null ? null : round(impact.speedKmh, 1),
          sample_rate_hz: sampleRate(),
        }
        cooldownUntil = t + 1000 * (cfg.cooldown_seconds ?? 0)
        reset()
        onDetect(hit)
      }
    } else {
      quietStart = null
    }
    if (state === 'observing' && t - impact.t > cfg.post_impact_observe_ms) reset()
  }

  const sampleRate = () => (count > 1 && lastT > firstT ? round((count - 1) / ((lastT - firstT) / 1000), 1) : null)

  return { push, setSpeed, reset, get state() { return state } }
}

function round(n, d) {
  const f = 10 ** d
  return Math.round(n * f) / f
}
