import { describe, it, expect, vi } from 'vitest'
import { createFallDetector } from './fallDetector'

// Mirrors the shape of GET /api/fall-detection/config (thresholds + cooldown).
// These numbers exist only to drive the tests; the app reads the real ones from
// the server.
const CFG = {
  freefall_g: 0.4, freefall_min_ms: 80, impact_g: 2.5, freefall_impact_window_ms: 600,
  crash_impact_g: 4.0, crash_min_speed_kmh: 20, stillness_dev_g: 0.15, stillness_gyro_dps: 40,
  stillness_ms: 2000, post_impact_observe_ms: 8000, cooldown_seconds: 60,
}
const DT = 20 // 50 Hz

// Feeds `ms` of a constant reading starting at t0; returns the next t.
function hold(det, t0, ms, a, gyro = 0) {
  let t = t0
  for (; t < t0 + ms; t += DT) det.push({ t, ax: 0, ay: 0, az: a, gyro })
  return t
}
// Walking: ~2 Hz bounce between 0.8 g and 1.6 g.
function walk(det, t0, ms) {
  let t = t0
  for (; t < t0 + ms; t += DT) {
    det.push({ t, ax: 0, ay: 0, az: 1.2 + 0.4 * Math.sin((t / 1000) * 2 * Math.PI * 2), gyro: 90 })
  }
  return t
}
const make = () => {
  const onDetect = vi.fn()
  return { det: createFallDetector(CFG, onDetect), onDetect }
}

describe('fall detector', () => {
  it('does not fire on normal walking', () => {
    const { det, onDetect } = make()
    walk(det, 0, 20_000)
    expect(onDetect).not.toHaveBeenCalled()
  })

  it('does not fire on a sharp shake or bump (impact with no stillness afterwards)', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 1000, 1)
    det.push({ t, ax: 0, ay: 0, az: 5, gyro: 200 }); t += DT
    t = walk(det, t, 9000) // keeps moving
    expect(onDetect).not.toHaveBeenCalled()
    expect(det.state).toBe('idle')
  })

  it('does not fire when the phone is dropped and picked straight back up', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 500, 1)
    t = hold(det, t, 200, 0.05) // free-fall
    det.push({ t, ax: 0, ay: 0, az: 6, gyro: 300 }); t += DT // impact
    t = walk(det, t, 8500) // someone picks it up
    expect(onDetect).not.toHaveBeenCalled()
  })

  it('detects a fall: free-fall -> impact -> stillness', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 500, 1)
    t = hold(det, t, 240, 0.05)
    det.push({ t, ax: 0, ay: 0, az: 5.4, gyro: 400 }); t += DT
    hold(det, t, 3000, 1.0)
    expect(onDetect).toHaveBeenCalledTimes(1)
    const hit = onDetect.mock.calls[0][0]
    expect(hit.type).toBe('fall')
    expect(hit.peak_g).toBeCloseTo(5.4, 1)
    expect(hit.freefall_ms).toBeGreaterThanOrEqual(CFG.freefall_min_ms)
    expect(hit.stillness_ms).toBeGreaterThanOrEqual(CFG.stillness_ms)
    expect(hit.speed_kmh).toBeNull()
  })

  it('classes a hard impact at speed as a crash', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 500, 1)
    det.setSpeed(62, t)
    det.push({ t, ax: 0, ay: 0, az: 9, gyro: 500 }); t += DT
    det.setSpeed(0, t) // GPS drops to zero right after
    hold(det, t, 3000, 1.0)
    expect(onDetect).toHaveBeenCalledTimes(1)
    expect(onDetect.mock.calls[0][0]).toMatchObject({ type: 'crash', speed_kmh: 62 })
  })

  it('never calls a hard impact a crash without a real speed reading', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 500, 1)
    det.push({ t, ax: 0, ay: 0, az: 9, gyro: 500 }); t += DT
    hold(det, t, 3000, 1.0)
    expect(onDetect.mock.calls[0][0].type).toBe('fall')
  })

  it('does not call a slow hard impact a crash', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 500, 1)
    det.setSpeed(CFG.crash_min_speed_kmh - 1, t)
    det.push({ t, ax: 0, ay: 0, az: 9, gyro: 500 }); t += DT
    hold(det, t, 3000, 1.0)
    expect(onDetect.mock.calls[0][0].type).toBe('fall')
  })

  it('ignores a moderate impact with no free-fall (e.g. jogging, a bumpy road)', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 500, 1)
    det.push({ t, ax: 0, ay: 0, az: 3, gyro: 50 }); t += DT
    hold(det, t, 5000, 1.0)
    expect(onDetect).not.toHaveBeenCalled()
  })

  it('stays quiet during the cooldown, then can fire again', () => {
    const { det, onDetect } = make()
    const fall = (t0) => {
      let t = hold(det, t0, 300, 1)
      t = hold(det, t, 240, 0.05)
      det.push({ t, ax: 0, ay: 0, az: 5, gyro: 300 }); t += DT
      return hold(det, t, 3000, 1.0)
    }
    let t = fall(0)
    expect(onDetect).toHaveBeenCalledTimes(1)
    t = fall(t + 100) // inside the cooldown
    expect(onDetect).toHaveBeenCalledTimes(1)
    t = hold(det, t, CFG.cooldown_seconds * 1000, 1.0)
    fall(t)
    expect(onDetect).toHaveBeenCalledTimes(2)
  })

  it('treats a missing gyroscope as "no rotation information", not as motion', () => {
    const { det, onDetect } = make()
    let t = hold(det, 0, 500, 1)
    t = hold(det, t, 240, 0.05)
    det.push({ t, ax: 0, ay: 0, az: 5 }); t += DT
    for (let i = t; i < t + 3000; i += DT) det.push({ t: i, ax: 0, ay: 0, az: 1 }) // no gyro key
    expect(onDetect).toHaveBeenCalledTimes(1)
  })

  it('ignores malformed samples', () => {
    const { det, onDetect } = make()
    det.push({ t: 0, ax: null, ay: 0, az: 1 })
    det.push({ t: NaN, ax: 0, ay: 0, az: 1 })
    det.push({ t: 10, ax: 0, ay: 0, az: Infinity })
    expect(onDetect).not.toHaveBeenCalled()
  })
})
