"""One-off ETL: real "normal movement" for the anomaly detector, from GeoLife.

Turns Microsoft Research's GeoLife GPS trajectories (182 people, 2007-2012,
mostly Beijing) into the exact four features the app scores on every GPS ping
(services/ml_service.anomaly_features), as if each trip had been recorded by
this app:

  1. Clean: drop GPS glitches (implied speed > 400 km/h between fixes).
  2. Re-sample to the app's own ping rhythm (hooks/useGeolocation.js): a ping
     after >= 15 s and >= 25 m of movement, or a 120 s heartbeat when still.
  3. Features per ping, computed like services/monitoring.process_ping:
       speed_kmh          distance / time since the previous ping
       dist_from_prev_m   distance since the previous ping
       inactivity_min     minutes since the previous ping
       dist_from_route_m  distance to the nearest "planned stop" (see below)
  4. Gaps over 10 minutes inside a trace are logger gaps (device off), not
     behaviour, and are skipped rather than learnt as normal.
  5. Only city-scale trips (never more than 30 km from their start) are used:
     long intercity journeys have no planned stops for hundreds of km.

"Planned stops": GeoLife has no itineraries, so a trip's stops are its start,
its end, and every stay point (stayed within 200 m for >= 10 min) -- the
standard stay-point definition from the GeoLife papers (Li et al. 2008).

Only the four derived numbers (plus an anonymous person index) are written:
no coordinates or timestamps from anyone's trace end up in the repo.

Source: Microsoft Research, "GeoLife GPS Trajectories" 1.3
https://www.microsoft.com/en-us/download/details.aspx?id=52367
Zheng et al., "GeoLife: A Collaborative Social Networking Service among User,
Location and Trajectory", IEEE Data Eng. Bull. 33(2), 2010.

Usage:
    python -m app.ml.etl.build_geolife_movement "path/to/Geolife Trajectories 1.3.zip"
"""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "real"
OUT_CSV = OUT_DIR / "geolife_movement.csv.gz"
OUT_META = OUT_DIR / "geolife_movement.meta.json"

EARTH_R = 6_371_000.0
MIN_INTERVAL_S, MIN_MOVE_M, HEARTBEAT_S = 15.0, 25.0, 120.0   # app ping gating
MAX_GAP_S = 600.0            # longer gap inside a trace = logger off
GLITCH_KMH = 400.0
STAY_DIST_M, STAY_TIME_S = 200.0, 600.0
MAX_TRAJ_PER_USER = 60       # keep heavy loggers from dominating
MAX_EXTENT_M = 30_000        # city-scale trips only (see features_for_trace)
MAX_ROWS = 120_000
SEED = 42


def _hav(lat1, lng1, lat2, lng2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = (np.sin((p2 - p1) / 2) ** 2
         + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lng2 - lng1) / 2) ** 2)
    return 2 * EARTH_R * np.arcsin(np.sqrt(a))


def read_plt(raw: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    df = pd.read_csv(io.BytesIO(raw), skiprows=6, header=None,
                     usecols=[0, 1, 4], names=["lat", "lng", "days"])
    return df["lat"].to_numpy(float), df["lng"].to_numpy(float), df["days"].to_numpy(float) * 86400.0


def clean(lat, lng, t):
    keep = [0]
    for i in range(1, len(lat)):
        j = keep[-1]
        dt = t[i] - t[j]
        if dt <= 0:
            continue
        if _hav(lat[j], lng[j], lat[i], lng[i]) / dt * 3.6 > GLITCH_KMH:
            continue
        keep.append(i)
    k = np.array(keep)
    return lat[k], lng[k], t[k]


def stay_points(lat, lng, t) -> list[tuple[float, float]]:
    out, i, n = [], 0, len(lat)
    while i < n:
        j = i + 1
        while j < n and _hav(lat[i], lng[i], lat[j], lng[j]) <= STAY_DIST_M:
            j += 1
        if t[j - 1] - t[i] >= STAY_TIME_S:
            out.append((float(lat[i:j].mean()), float(lng[i:j].mean())))
            i = j
        else:
            i += 1
    return out


def resample(lat, lng, t) -> np.ndarray:
    keep = [0]
    for i in range(1, len(lat)):
        j = keep[-1]
        dt = t[i] - t[j]
        if dt >= HEARTBEAT_S or (dt >= MIN_INTERVAL_S and _hav(lat[j], lng[j], lat[i], lng[i]) >= MIN_MOVE_M):
            keep.append(i)
    return np.array(keep)


def features_for_trace(lat, lng, t) -> np.ndarray | None:
    if len(lat) < 10:
        return None
    lat, lng, t = clean(lat, lng, t)
    if len(lat) < 10:
        return None
    # A tourist's day plays out within a city/region whose stops are on the
    # itinerary. Long intercity journeys in GeoLife (trains between cities,
    # with no stop for hundreds of km) would teach the model that being 50+ km
    # from every planned stop is normal -- exactly the deviation it must flag.
    if np.max(_hav(lat[0], lng[0], lat, lng)) > MAX_EXTENT_M:
        return None
    stops = [(lat[0], lng[0]), (lat[-1], lng[-1]), *stay_points(lat, lng, t)]
    k = resample(lat, lng, t)
    if len(k) < 3:
        return None
    la, ln, tt = lat[k], lng[k], t[k]
    dist_prev = _hav(la[:-1], ln[:-1], la[1:], ln[1:])
    dt = tt[1:] - tt[:-1]
    s_lat = np.array([s[0] for s in stops])
    s_lng = np.array([s[1] for s in stops])
    route = np.min(_hav(la[1:, None], ln[1:, None], s_lat[None, :], s_lng[None, :]), axis=1)
    ok = (dt > 0) & (dt <= MAX_GAP_S)
    speed = np.where(dt > 0, dist_prev / np.maximum(dt, 1e-9) * 3.6, 0.0)
    return np.column_stack([speed, dist_prev, dt / 60.0, route])[ok]


def build(zip_path: str) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(SEED)
    with zipfile.ZipFile(zip_path) as zf:
        by_user: dict[str, list[str]] = {}
        for name in zf.namelist():
            m = re.search(r"Data/(\d{3})/Trajectory/.+\.plt$", name)
            if m:
                by_user.setdefault(m.group(1), []).append(name)
        users = sorted(by_user)
        frames, n_traces, n_points = [], 0, 0
        for ui, user in enumerate(users):
            files = sorted(by_user[user])
            if len(files) > MAX_TRAJ_PER_USER:
                files = list(rng.choice(files, MAX_TRAJ_PER_USER, replace=False))
            for name in files:
                lat, lng, t = read_plt(zf.read(name))
                n_points += len(lat)
                f = features_for_trace(lat, lng, t)
                if f is None or len(f) == 0:
                    continue
                n_traces += 1
                df = pd.DataFrame(f, columns=["speed_kmh", "dist_from_prev_m", "inactivity_min",
                                              "dist_from_route_m"])
                df["person"] = ui
                frames.append(df)
            if ui % 20 == 0:
                print(f"  person {ui + 1}/{len(users)}: {sum(len(x) for x in frames):,} pings so far",
                      flush=True)
    all_rows = pd.concat(frames, ignore_index=True)
    total = len(all_rows)
    if total > MAX_ROWS:
        # Equal share per person (capped by what each person has), then top up.
        per = MAX_ROWS // all_rows["person"].nunique()
        parts = [g.sample(min(len(g), per), random_state=SEED) for _, g in all_rows.groupby("person")]
        sample = pd.concat(parts)
        rest = all_rows.drop(sample.index)
        if len(sample) < MAX_ROWS:
            sample = pd.concat([sample, rest.sample(MAX_ROWS - len(sample), random_state=SEED)])
        all_rows = sample.sample(frac=1, random_state=SEED).reset_index(drop=True)
    all_rows = all_rows.round({"speed_kmh": 2, "dist_from_prev_m": 1, "inactivity_min": 3,
                               "dist_from_route_m": 1})
    meta = {
        "title": "Real movement features from GeoLife GPS trajectories, at this app's ping rate",
        "source": "Microsoft Research, GeoLife GPS Trajectories 1.3 "
                  "(https://www.microsoft.com/en-us/download/details.aspx?id=52367)",
        "citation": "Zheng, Xie, Ma. GeoLife: A Collaborative Social Networking Service among "
                    "User, Location and Trajectory. IEEE Data Eng. Bull. 33(2), 2010.",
        "built": date.today().isoformat(),
        "people": len(users), "traces_used": n_traces, "raw_gps_points_read": n_points,
        "pings_after_resampling": total, "rows_written": len(all_rows),
        "method": {
            "ping_gating": f">= {MIN_INTERVAL_S:.0f} s and >= {MIN_MOVE_M:.0f} m, or a "
                           f"{HEARTBEAT_S:.0f} s heartbeat (as hooks/useGeolocation.js)",
            "glitch_filter_kmh": GLITCH_KMH, "max_gap_s": MAX_GAP_S,
            "max_trip_extent_m": MAX_EXTENT_M,
            "planned_stops": f"trip start, trip end, stay points (<= {STAY_DIST_M:.0f} m for "
                             f">= {STAY_TIME_S / 60:.0f} min)",
            "max_traces_per_person": MAX_TRAJ_PER_USER, "seed": SEED,
        },
        "caveats": [
            "Recorded mostly in Beijing, 2007-2012 -- walking, cycling, bus, car, metro and "
            "train behaviour transfers, but it is not Indian tourist data.",
            "Speed is distance/time between pings, not the phone's instantaneous GPS speed.",
            "Contains only normal movement: real labelled emergencies (abduction, collapse) "
            "do not exist in any public dataset, so evaluation anomalies remain simulated.",
        ],
        "columns": ["speed_kmh", "dist_from_prev_m", "inactivity_min", "dist_from_route_m", "person"],
        "summary": {c: {"p50": round(float(all_rows[c].median()), 2),
                        "p95": round(float(all_rows[c].quantile(0.95)), 2),
                        "p99": round(float(all_rows[c].quantile(0.99)), 2)}
                    for c in ["speed_kmh", "dist_from_prev_m", "inactivity_min", "dist_from_route_m"]},
    }
    return all_rows, meta


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    rows, meta = build(sys.argv[1])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows.to_csv(OUT_CSV, index=False, compression="gzip")
    OUT_META.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_CSV} ({len(rows):,} rows, {OUT_CSV.stat().st_size / 1e6:.1f} MB)")
    print(json.dumps(meta["summary"], indent=2))
