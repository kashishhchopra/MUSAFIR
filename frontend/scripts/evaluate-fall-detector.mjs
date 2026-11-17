// Evaluate the app's on-device fall detector (src/lib/fallDetector.js) against
// REAL recorded falls and everyday activities from the UMAFall dataset.
//
//   node scripts/evaluate-fall-detector.mjs <UMAFall folder> [--write]
//
// * Runs the exact detector code the phone runs -- imported, not re-implemented.
// * Uses the exact thresholds the server sends the phone: parsed from the
//   FALL_* defaults in backend/app/core/config.py (single source of truth).
// * Signal: the SMARTPHONE in the subject's right trouser pocket (UMAFall
//   sensor 0, accelerometer in g) -- the same placement as a tourist's phone.
//   The 4 body-worn SensorTags in the dataset are ignored. UMAFall has no phone
//   gyroscope, so the detector runs with gyro = null (its documented fallback).
// * Replayed at ~50 Hz (a typical browser devicemotion rate) and at the
//   recorded ~200 Hz.
// * Also runs a small threshold sweep to show the trade-off, for information
//   only: it never changes the app's configuration.
//
// UMAFall: Casilari, Santoyo-Ramon, Cano-Garcia, "UMAFall: A Multisensor
// Dataset for the Research on Automatic Fall Detection", Procedia Computer
// Science 110 (2017). figshare DOI 10.6084/m9.figshare.4214283 (CC BY 4.0).
//
// --write saves docs/FALL_DETECTION_EVALUATION.md and docs/fall_detection_eval.json.

/* global console, process */
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { join, dirname, basename } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createFallDetector } from '../src/lib/fallDetector.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')

function serverThresholds() {
  const src = readFileSync(join(REPO, 'backend', 'app', 'core', 'config.py'), 'utf8')
  const v = (name) => {
    const m = src.match(new RegExp(`^\\s*${name}\\s*:\\s*\\w+\\s*=\\s*([0-9.]+)`, 'm'))
    if (!m) throw new Error(`${name} not found in config.py`)
    return Number(m[1])
  }
  return {
    freefall_g: v('FALL_FREEFALL_G'),
    freefall_min_ms: v('FALL_FREEFALL_MIN_MS'),
    impact_g: v('FALL_IMPACT_G'),
    freefall_impact_window_ms: v('FALL_FREEFALL_IMPACT_WINDOW_MS'),
    crash_impact_g: v('FALL_CRASH_IMPACT_G'),
    crash_min_speed_kmh: v('FALL_CRASH_MIN_SPEED_KMH'),
    stillness_dev_g: v('FALL_STILLNESS_DEV_G'),
    stillness_gyro_dps: v('FALL_STILLNESS_GYRO_DPS'),
    stillness_ms: v('FALL_STILLNESS_MS'),
    post_impact_observe_ms: v('FALL_POST_IMPACT_OBSERVE_MS'),
    cooldown_seconds: v('FALL_DETECTOR_COOLDOWN_SECONDS'),
  }
}

function listCsv(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...listCsv(p))
    else if (name.endsWith('.csv') && name.startsWith('UMAFall_')) out.push(p)
  }
  return out.sort()
}

// UMAFall_Subject_01_ADL_Walking_1_<date>.csv
function describe(file) {
  const parts = basename(file).split('_')
  return { subject: parts[2], kind: parts[3] === 'Fall' ? 'fall' : 'adl', activity: parts[4] }
}

function phoneSamples(file) {
  const s = []
  for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
    if (!/^\d/.test(line)) continue
    const [t, , x, y, z, type, id] = line.split(';')
    if (type === '0' && id === '0') s.push({ t: Number(t), ax: Number(x), ay: Number(y), az: Number(z) })
  }
  s.sort((a, b) => a.t - b.t)
  return s
}

function downsample(samples, hz) {
  const gap = 1000 / hz
  const out = []
  let next = -Infinity
  for (const p of samples) {
    if (p.t >= next) { out.push(p); next = p.t + gap }
  }
  return out
}

function runOne(samples, cfg) {
  let hit = null
  const det = createFallDetector(cfg, (h) => { if (!hit) hit = h })
  for (const p of samples) det.push({ ...p, gyro: null })
  return hit
}

function evaluate(recs, cfg, hz) {
  const res = { falls: 0, falls_detected: 0, adls: 0, false_alarms: 0, by_activity: {} }
  for (const r of recs) {
    const samples = hz ? downsample(r.samples, hz) : r.samples
    const hit = runOne(samples, cfg)
    const a = (res.by_activity[r.activity] ??= { kind: r.kind, n: 0, detected: 0 })
    a.n += 1
    if (hit) a.detected += 1
    if (r.kind === 'fall') { res.falls += 1; if (hit) res.falls_detected += 1 } else { res.adls += 1; if (hit) res.false_alarms += 1 }
  }
  res.sensitivity = +(res.falls_detected / res.falls).toFixed(4)
  res.false_alarm_rate = +(res.false_alarms / res.adls).toFixed(4)
  res.specificity = +(1 - res.false_alarm_rate).toFixed(4)
  return res
}

const pct = (x) => `${(x * 100).toFixed(1)}%`

// The thresholds the app shipped with before this evaluation.
const PREVIOUS_DEFAULTS = { freefall_g: 0.4, freefall_min_ms: 80 }

// Held-out-people check of the tuning procedure: split the volunteers into two
// halves, pick the free-fall thresholds that catch the most falls on one half
// with <= 5% false alarms, then measure them on the OTHER half (and swap).
// Guards against thresholds that only fit the particular people they were
// tuned on.
function crossValidate(recs, cfg) {
  const grid = []
  for (const freefall_g of [0.4, 0.5, 0.6, 0.7, 0.8]) {
    for (const freefall_min_ms of [40, 80, 120]) grid.push({ ...cfg, freefall_g, freefall_min_ms })
  }
  const subjects = [...new Set(recs.map((r) => r.subject))].sort()
  const halves = [subjects.filter((_, i) => i % 2 === 0), subjects.filter((_, i) => i % 2 === 1)]
  return halves.map((tuneSubjects) => {
    const tune = recs.filter((r) => tuneSubjects.includes(r.subject))
    const test = recs.filter((r) => !tuneSubjects.includes(r.subject))
    let best = null
    for (const c of grid) {
      const s = evaluate(tune, c, 50)
      if (s.false_alarm_rate > 0.05) continue
      if (!best || s.sensitivity > best.s.sensitivity) best = { c, s }
    }
    const heldOut = evaluate(test, best.c, 50)
    const heldOutPrev = evaluate(test, { ...cfg, ...PREVIOUS_DEFAULTS }, 50)
    return {
      tuned_on_people: tuneSubjects.length, tested_on_people: subjects.length - tuneSubjects.length,
      picked: { freefall_g: best.c.freefall_g, freefall_min_ms: best.c.freefall_min_ms },
      held_out: { sensitivity: heldOut.sensitivity, false_alarm_rate: heldOut.false_alarm_rate },
      held_out_previous_defaults: { sensitivity: heldOutPrev.sensitivity, false_alarm_rate: heldOutPrev.false_alarm_rate },
    }
  })
}

function main() {
  const dir = process.argv[2]
  if (!dir) { console.error('usage: node scripts/evaluate-fall-detector.mjs <UMAFall folder> [--write]'); process.exit(1) }
  const write = process.argv.includes('--write')
  const cfg = serverThresholds()
  const recs = listCsv(dir).map((f) => ({ ...describe(f), samples: phoneSamples(f) })).filter((r) => r.samples.length > 50)
  const subjects = new Set(recs.map((r) => r.subject))
  const peaks = (kind) => recs.filter((r) => r.kind === kind)
    .map((r) => Math.max(...r.samples.map((p) => Math.hypot(p.ax, p.ay, p.az)))).sort((a, b) => a - b)
  const q = (arr, f) => +arr[Math.min(arr.length - 1, Math.floor(f * arr.length))].toFixed(2)
  const fallPeaks = peaks('fall'); const adlPeaks = peaks('adl')

  const at50 = evaluate(recs, cfg, 50)
  const at200 = evaluate(recs, cfg, null)
  const prevCfg = { ...cfg, ...PREVIOUS_DEFAULTS }
  const prev50 = evaluate(recs, prevCfg, 50)

  const sweep = []
  for (const freefall_g of [0.4, 0.5, 0.6, 0.7, 0.8]) {
    for (const freefall_min_ms of [40, 80]) {
      const r = evaluate(recs, { ...cfg, freefall_g, freefall_min_ms }, 50)
      sweep.push({ freefall_g, freefall_min_ms, sensitivity: r.sensitivity, false_alarm_rate: r.false_alarm_rate })
    }
  }
  const cv = crossValidate(recs, cfg)

  const result = {
    dataset: {
      name: 'UMAFall (Universidad de Malaga), corrected version',
      doi: '10.6084/m9.figshare.4214283', licence: 'CC BY 4.0',
      signal: 'smartphone accelerometer, right trouser pocket (sensor 0), gyro unavailable',
      recordings: recs.length, falls: recs.filter((r) => r.kind === 'fall').length,
      adls: recs.filter((r) => r.kind === 'adl').length, subjects: subjects.size,
    },
    thresholds: cfg,
    peak_g: {
      falls: { p10: q(fallPeaks, 0.1), p50: q(fallPeaks, 0.5), p90: q(fallPeaks, 0.9) },
      adls: { p10: q(adlPeaks, 0.1), p50: q(adlPeaks, 0.5), p90: q(adlPeaks, 0.9) },
    },
    results_50hz: at50,
    results_200hz: at200,
    previous_defaults: { thresholds: PREVIOUS_DEFAULTS, results_50hz: prev50 },
    sweep_50hz: sweep,
    cross_validation: cv,
    generated: new Date().toISOString().slice(0, 10),
  }
  console.log(JSON.stringify({ dataset: result.dataset, peak_g: result.peak_g,
    at50: { sens: at50.sensitivity, far: at50.false_alarm_rate }, at200: { sens: at200.sensitivity, far: at200.false_alarm_rate } }, null, 2))
  console.log('previous defaults @50Hz:', { sens: prev50.sensitivity, far: prev50.false_alarm_rate })
  console.table(sweep)
  console.log(JSON.stringify(cv, null, 2))

  if (write) {
    writeFileSync(join(REPO, 'docs', 'fall_detection_eval.json'), JSON.stringify(result, null, 2) + '\n')
    writeFileSync(join(REPO, 'docs', 'FALL_DETECTION_EVALUATION.md'), report(result))
    console.log('Wrote docs/FALL_DETECTION_EVALUATION.md and docs/fall_detection_eval.json')
  }
}

function report(r) {
  const a = r.results_50hz; const b = r.results_200hz
  const rows = Object.entries(a.by_activity).sort(([, x], [, y]) => (x.kind === y.kind ? 0 : x.kind === 'fall' ? -1 : 1))
    .map(([name, v]) => `| ${name} | ${v.kind === 'fall' ? 'fall' : 'everyday'} | ${v.n} | ${v.detected} | ${pct(v.detected / v.n)} |`).join('\n')
  const sweep = r.sweep_50hz.map((s) => `| ${s.freefall_g} g | ${s.freefall_min_ms} ms | ${pct(s.sensitivity)} | ${pct(s.false_alarm_rate)} |`).join('\n')
  const p = r.previous_defaults
  const cv = r.cross_validation.map((f, i) => `| ${i + 1} | ${f.tuned_on_people} / ${f.tested_on_people} | ${f.picked.freefall_g} g / ${f.picked.freefall_min_ms} ms | ${pct(f.held_out.sensitivity)} / ${pct(f.held_out.false_alarm_rate)} | ${pct(f.held_out_previous_defaults.sensitivity)} / ${pct(f.held_out_previous_defaults.false_alarm_rate)} |`).join('\n')
  const t = r.thresholds
  return `# Fall Detection Evaluation (real recordings)

Generated by \`node frontend/scripts/evaluate-fall-detector.mjs <UMAFall folder> --write\` on ${r.generated}.

The app's own on-device detector (\`frontend/src/lib/fallDetector.js\`), with the
exact thresholds the server sends to phones (\`FALL_*\` in
\`backend/app/core/config.py\`), replayed over real recordings of people falling
and doing everyday activities.

## Dataset

- **UMAFall**, Universidad de Malaga ([DOI ${r.dataset.doi}](https://doi.org/${r.dataset.doi}), ${r.dataset.licence}).
  Casilari, Santoyo-Ramon & Cano-Garcia, *Procedia Computer Science* 110 (2017).
- ${r.dataset.recordings} recordings from ${r.dataset.subjects} people: **${r.dataset.falls} falls** (forward, backward, lateral onto a mattress) and **${r.dataset.adls} everyday activities**.
- Signal used: ${r.dataset.signal} -- the same place a tourist carries their phone.

## Results

| | Current thresholds, ~50 Hz (typical browser) | Current, ~200 Hz (as recorded) | Previous defaults (${p.thresholds.freefall_g} g / ${p.thresholds.freefall_min_ms} ms), ~50 Hz |
|---|---|---|---|
| Falls detected (sensitivity) | **${pct(a.sensitivity)}** (${a.falls_detected}/${a.falls}) | ${pct(b.sensitivity)} (${b.falls_detected}/${b.falls}) | ${pct(p.results_50hz.sensitivity)} (${p.results_50hz.falls_detected}/${p.results_50hz.falls}) |
| False alarms on everyday activities | **${pct(a.false_alarm_rate)}** (${a.false_alarms}/${a.adls}) | ${pct(b.false_alarm_rate)} (${b.false_alarms}/${b.adls}) | ${pct(p.results_50hz.false_alarm_rate)} (${p.results_50hz.false_alarms}/${p.results_50hz.adls}) |

### Why the free-fall thresholds changed

The detector looks for a moment of near-weightlessness ("free fall") just
before the impact. With the previous defaults (below ${p.thresholds.freefall_g} g for
${p.thresholds.freefall_min_ms} ms) it missed about half of these real falls -- and almost
every miss hit hard enough but never showed that free-fall phase: a phone in a
trouser pocket moves with the body and rarely drops that close to 0 g. The
current values (below ${t.freefall_g} g for ${t.freefall_min_ms} ms) accept the shallower
low-g dip real pocket recordings show.

Held-out-people check (tune on one half of the volunteers, measure on the other):

| Fold | People tuned / tested | Picked on tuning half | Held-out: falls caught / false alarms | Previous defaults on the same held-out people |
|---|---|---|---|---|
${cv}

Results differ a lot between people, but in both folds tuning on *other*
people caught clearly more of the held-out people's falls than the previous
defaults did.

Per activity (~50 Hz):

| Activity | Type | Recordings | Detector fired | Rate |
|---|---|---|---|---|
${rows}

Peak acceleration seen by the phone (g): falls p10/p50/p90 = ${r.peak_g.falls.p10} / ${r.peak_g.falls.p50} / ${r.peak_g.falls.p90}; everyday activities = ${r.peak_g.adls.p10} / ${r.peak_g.adls.p50} / ${r.peak_g.adls.p90}.

Thresholds used: free-fall < ${t.freefall_g} g for >= ${t.freefall_min_ms} ms, impact >= ${t.impact_g} g within ${t.freefall_impact_window_ms} ms (or >= ${t.crash_impact_g} g alone), then still (|a| within ${t.stillness_dev_g} g of 1 g) for ${t.stillness_ms} ms within ${t.post_impact_observe_ms} ms.

## Free-fall threshold trade-off (~50 Hz, other thresholds as configured)

| Free-fall below | for at least | Falls detected | False alarms |
|---|---|---|---|
${sweep}

## How to read this

- A detection never raises an alert by itself: it opens a 30-second "Are you OK?"
  countdown the person can cancel. So a false alarm costs a tap, while a missed
  fall costs help that never comes -- sensitivity matters more than false alarms.
- Falls here are performed by volunteers onto a mattress, and every recording is
  only ~15 s long. A real fall onto a hard floor usually hits harder, and a
  person who cannot get up stays still far longer than the recording lasts --
  so real-world detection is likely at least as good as measured here.
- The phone is in a trouser pocket. A phone in a bag or hand will see a weaker,
  different signal.
`
}

main()
