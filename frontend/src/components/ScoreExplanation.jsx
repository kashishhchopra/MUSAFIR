// Renders the per-factor contributions from a safety-score breakdown (exact
// formula terms by default, SHAP values when the server scores with the ML
// model), plus where the real-world inputs came from.
// English-only labels: like the admin dashboard, this is a supplementary
// technical view rather than core safety-critical UI, so it wasn't included
// in the i18n pass (see the multilingual commit, which scoped translation to
// the SOS/tracking/geofence flow a tourist actually depends on).
const LABELS = {
  zone_risk: 'Zone risk level',
  hour: 'Time of day',
  anomaly_score: 'Movement anomaly',
  crime_index: "Area's crime index",
  weather_risk: 'Weather conditions',
}

const WEATHER_SOURCE = {
  'open-meteo': 'live weather from Open-Meteo',
  openweathermap: 'live weather from OpenWeatherMap',
  mock: 'estimated (no live weather service reachable)',
}

function crimeSource(b) {
  if (b?.crime_source === 'ncrb_district' || b?.crime_source === 'ncrb_state') {
    const where = [b.district, b.state].filter(Boolean).join(', ')
    const basis = b.crime_source === 'ncrb_state' ? ' (state-wide figure)' : ''
    const rate = b.district_crime_rate_per_lakh != null ? ` — ${b.district_crime_rate_per_lakh} recorded per lakh people a year${basis}` : ''
    return `NCRB police records 2017–2022 for ${where}${rate}`
  }
  if (b?.crime_source === 'zone') return "this zone's assigned crime level"
  return null
}

export default function ScoreExplanation({ explanation, breakdown }) {
  if (!explanation) return null
  const crime = crimeSource(breakdown)
  const weather = WEATHER_SOURCE[breakdown?.weather_source]

  const rows = Object.entries(explanation.contributions)
    .map(([key, value]) => ({ key, label: LABELS[key] || key, value }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.value)))

  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-sky-600 font-medium select-none">
        Why this score?
      </summary>
      <div className="mt-2 space-y-1.5">
        <div className="text-slate-400">
          Starting from a baseline of {explanation.base_value}, each factor below
          moved your score up (safer) or down (riskier).
        </div>
        {rows.map((r) => (
          <div key={r.key} className="flex items-center gap-2">
            <span className="w-32 text-slate-600 shrink-0">{r.label}</span>
            <div className="flex-1 h-3 bg-slate-100 rounded relative overflow-hidden">
              <div
                className={`absolute top-0 h-full ${r.value >= 0 ? 'bg-green-500 left-1/2' : 'bg-red-500 right-1/2'}`}
                style={{ width: `${(Math.abs(r.value) / maxAbs) * 50}%` }}
              />
              <div className="absolute left-1/2 top-0 h-full w-px bg-slate-300" />
            </div>
            <span className={`w-12 text-right font-mono ${r.value >= 0 ? 'text-green-700' : 'text-red-700'}`}>
              {r.value >= 0 ? '+' : ''}{r.value}
            </span>
          </div>
        ))}
        {(crime || weather) && (
          <ul className="pt-1 text-slate-400 space-y-0.5">
            {crime && <li>Crime: {crime}</li>}
            {weather && <li>Weather: {weather}</li>}
          </ul>
        )}
      </div>
    </details>
  )
}
