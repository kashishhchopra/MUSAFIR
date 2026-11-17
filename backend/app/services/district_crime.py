"""Real district-level crime for the safety score.

Answers "which Indian district is this GPS point in, and how much crime that a
visitor could suffer does NCRB record there?" -- fully offline, from two
committed files built by app/ml/etl/build_district_crime.py:

  data/reference/india_districts_lgd.geojson  LGD district boundaries
  data/reference/district_crime_india.json    NCRB 2017-2022 per district

`crime_index` is the district's percentile rank (0-100) by recorded
visitor-relevant crimes per lakh people, e.g. ~98 for Guwahati (Kamrup
Metro), single digits for quiet hill districts. Points outside India (or in
the sea) return None so callers keep their own fallback.
"""
from __future__ import annotations

import json
import logging
import threading
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_REF = Path(__file__).resolve().parents[1] / "data" / "reference"
_CRIME_PATH = _REF / "district_crime_india.json"
_GEO_PATH = _REF / "india_districts_lgd.geojson"

_lock = threading.Lock()
_index: dict | None = None


@lru_cache(maxsize=1)
def load_crime_table() -> dict:
    with open(_CRIME_PATH, encoding="utf-8") as f:
        return json.load(f)


def _load_index() -> dict | None:
    """Build (once) an STRtree over the district polygons. None if the
    boundary file is missing or unreadable -- scoring must never crash."""
    global _index
    if _index is not None:
        return _index or None
    with _lock:
        if _index is not None:
            return _index or None
        try:
            import shapely
            from shapely.geometry import shape

            with open(_GEO_PATH, encoding="utf-8") as f:
                features = json.load(f)["features"]
            geoms = [shape(feat["geometry"]) for feat in features]
            props = [feat["properties"] for feat in features]
            for g in geoms:
                shapely.prepare(g)
            _index = {"tree": shapely.STRtree(geoms), "geoms": geoms, "props": props}
        except (OSError, ValueError, KeyError, ImportError) as e:
            logger.warning("district boundaries unavailable, district crime disabled: %s", e)
            _index = {}
    return _index or None


def district_at(lat: float | None, lng: float | None) -> dict | None:
    """{dist_lgd, district, state, state_lgd} for the district containing the
    point, or None (outside India / no boundaries)."""
    if lat is None or lng is None:
        return None
    idx = _load_index()
    if idx is None:
        return None
    from shapely.geometry import Point

    pt = Point(float(lng), float(lat))
    for i in idx["tree"].query(pt):
        if idx["geoms"][i].covers(pt):
            return dict(idx["props"][i])
    return None


def crime_at(lat: float | None, lng: float | None) -> dict | None:
    """Real crime figures for the point's district, or None.

    {district, state, crime_index, rate_per_lakh, rate_basis, years, source}.
    A district with a boundary but no NCRB row (very new districts) uses its
    state's figure, flagged rate_basis="state"."""
    d = district_at(lat, lng)
    if d is None:
        return None
    table = load_crime_table()
    row = table["districts"].get(str(d["dist_lgd"]))
    years = table["_meta"]["years"]
    if row is not None:
        return {
            "district": row["district"], "state": row["state"],
            "crime_index": row["crime_index"], "rate_per_lakh": row["rate_per_lakh"],
            "rate_basis": row["rate_basis"], "years": [years[0], years[-1]],
            "source": "NCRB district-wise IPC crimes",
        }
    state = table["states"].get(str(d["state_lgd"]))
    if state is None:
        return None
    return {
        "district": d["district"], "state": state["state"],
        "crime_index": state["crime_index"], "rate_per_lakh": state["rate_per_lakh"],
        "rate_basis": "state", "years": [years[0], years[-1]],
        "source": "NCRB district-wise IPC crimes",
    }


def clear_cache() -> None:
    global _index
    _index = None
    load_crime_table.cache_clear()
