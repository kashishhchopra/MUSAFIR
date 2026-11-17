# Real Data Sources

What real-world data MUSAFIR uses, where it comes from, how it is processed,
and what is still simulated. Every committed file below is *derived* from the
original download by a script in the repo, so it can be rebuilt and checked.

## At a glance

| Used for | Dataset | Licence | Committed file(s) | Built by |
|---|---|---|---|---|
| Safety score: crime in the area | NCRB *Crime in India*, district-wise IPC crimes 2017–2022 | Government of India open data | `backend/app/data/reference/district_crime_india.json` | `backend/app/ml/etl/build_district_crime.py` |
| Crime rate per person | Census of India 2011, Primary Census Abstract (district population) | Government of India open data | (inside the file above) | same |
| Which district a GPS point is in | Local Government Directory (LGD) district boundaries, 785 districts | CC0-1.0 / CC-BY-4.0 | `backend/app/data/reference/india_districts_lgd.geojson` | same |
| Safety score: weather | Open-Meteo current weather (live, no key) — or OpenWeatherMap if a key is set | CC-BY 4.0 (Open-Meteo) | none (live API) | `backend/app/services/weather.py` |
| Movement-anomaly model: normal travel | Microsoft Research GeoLife GPS Trajectories 1.3 | Microsoft Research public release (no explicit licence on the download page; cite Zheng et al. 2010 per the GeoLife user guide) | `backend/data/real/geolife_movement.csv.gz` (+ `.meta.json`) | `backend/app/ml/etl/build_geolife_movement.py` |
| Testing fall detection | UMAFall fall & activity recordings (Universidad de Málaga) | CC BY 4.0 | `docs/FALL_DETECTION_EVALUATION.md`, `docs/fall_detection_eval.json` | `frontend/scripts/evaluate-fall-detector.mjs` |
| Tourist-crime baseline (existing) | NCRB Chapter 13A, crime against foreigners 2014–2021 (Mendeley) | CC BY 4.0 | `backend/app/data/reference/ncrb_crime_against_foreigners.json` | `backend/app/ml/etl/build_crime_index.py` |

Raw downloads are **not** committed (hundreds of MB, and GeoLife contains
people's real location traces); only the derived numbers are.

## 1. District crime (safety score)

- **Downloads:** India Data Portal (Bharti Institute of Public Policy, ISB),
  dataset [crime-statistics](https://ckandev.indiadataportal.com/dataset/crime-statistics),
  resource *Districtwise IPC crimes 2017 onwards*; dataset *census-pca-demography*,
  resource *Districts*; boundaries from
  [yashveeeeeeer/india-geodata](https://github.com/yashveeeeeeer/india-geodata)
  (`LGD_Districts.parquet`).
- **Which crimes:** the IPC heads a visitor could suffer — violent (murder, attempted
  murder, grievous hurt…), sexual offences and harassment, kidnapping/abduction,
  robbery/dacoity/extortion, theft/burglary, cheating/card fraud. Domestic offences,
  negligence deaths, rioting etc. are excluded (listed in the JSON's `_meta`).
- **Method:** mean yearly count per district (all police units in it) ÷ Census 2011
  population → rate per lakh people. `crime_index` = that rate's percentile rank among
  all ~750 districts (0 lowest, 100 highest). Districts formed after 2011 use their
  state's rate (`rate_basis: "state"`).
- **At run time:** `app/services/district_crime.py` finds the district containing the
  tourist's GPS point (offline, from the boundaries file) and uses its `crime_index`
  in the safety score. Outside India it falls back to the zone's value.
- **Limits:** 2011 population (rates somewhat overstated where population grew);
  recorded crime depends on reporting; crimes against everyone, not only tourists (no
  district-level tourist series exists).

## 2. Weather (safety score)

Open-Meteo's free API gives real current conditions with no key. WMO weather codes are
mapped onto the same 0–100 risk scale used for OpenWeatherMap (thunderstorm 80, heavy
rain 60, fog 45, …, clear 5; +10/+20 for strong wind, +15 for extreme heat/cold). The
score's breakdown reports `weather_source` (`open-meteo`, `openweathermap` or `mock`)
so an estimate is never shown as measured.

## 3. Real normal movement (anomaly model)

GeoLife traces (182 people, mostly Beijing, 2007–2012) are re-sampled at the app's own
ping rhythm (≥15 s and ≥25 m, or a 120 s heartbeat) and turned into the same four
features the app scores on every ping. "Planned stops" = trip start, end and stay
points (≥10 min within 200 m). Only city-scale trips (never more than 30 km from their
start) are used: long intercity journeys have no stops for hundreds of km and would
teach the model that being far from every planned stop is normal. Only the derived
numbers are kept.
The anomaly model learns what **real** travel looks like from this; the emergencies it
is evaluated against remain **simulated**, because no public dataset has labelled real
abductions or collapses. See `docs/ML_EVALUATION.md`.

## 4. Fall detection (tested on real falls)

The app's detector code and the server's thresholds were replayed over 746 UMAFall
recordings (208 real falls, 538 everyday activities, 19 people) from a phone in a
trouser pocket. This found that the original "free-fall" threshold missed about half
of real falls; the thresholds were changed on that evidence, checked on held-out
people. Full results: `docs/FALL_DETECTION_EVALUATION.md`.

## What is still simulated

- The labelled emergencies used to score the anomaly model (see above).
- The demo data (tourists, incidents, alerts) and the hot-zone clusters, which need
  real geocoded incident locations — the app's own incident records will provide
  these once it is in real use.
- The optional RandomForest safety model (`SAFETY_SCORE_MODE=model`), trained on
  synthetic rows to reproduce the formula. The default scoring is the formula itself.

## Not added, and why

- **Tourist footfall by state** (Ministry of Tourism, *India Tourism Statistics*):
  published as PDF tables, and whether crowds make a place safer or riskier is not
  established, so it was not given a weight in the score.

## Rebuilding

```bash
# district crime + boundaries (needs pyarrow, dev-only)
python -m app.ml.etl.build_district_crime NCRB.csv CENSUS.csv LGD_Districts.parquet
# real movement features
python -m app.ml.etl.build_geolife_movement "Geolife Trajectories 1.3.zip"
# retrain models (uses the GeoLife file automatically)
python -m app.ml.train_all --crime-index-source ncrb
# fall detection evaluation
node frontend/scripts/evaluate-fall-detector.mjs path/to/UMAFall --write
```
