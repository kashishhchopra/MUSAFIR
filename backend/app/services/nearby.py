"""Nearby emergency services and transport: hospitals, pharmacies, police, transport.

Live OpenStreetMap, from free keyless services (see services/osm_source.py):

  1. Overpass API (the proper radius query), then
  2. Nominatim category searches in expanding boxes (nearest first) when Overpass is
     unavailable, then
  3. for hospitals and police only, the saved OpenStreetMap import already in the
     database (clearly labelled as saved copy) -- an emergency list should not be empty
     just because a public server is busy, and these are real mapped places, not fixtures,
     but they can be out of date, so the app says so.

If none of that is available the caller gets NearbyUnavailable (HTTP 503), never an empty
list posing as "nothing nearby". Results are cached per cell/radius/category; "open now"
is derived only from a plainly parseable `opening_hours` tag and the user's own clock.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.police import PoliceUnit
from app.services import maps, osm_source
from app.services import nearby_tags as tg
from app.services.geo import haversine_m
from app.services.osm_source import ProviderError

logger = logging.getLogger(__name__)

NEARBY_CATEGORIES = tg.CATEGORIES
MAX_RADIUS_KM = 50.0
_RESULT_CAP = 200
_STALE_OK_S = 24 * 3600
_NOMINATIM_BUDGET_S = 30.0
_SEARCH_STEPS_KM = (1.0, 3.0)  # nested boxes tried before the full radius, so the NEAREST places come first


class NearbyUnavailable(Exception):
    """No source could answer and nothing is cached or saved."""


_lock = threading.Lock()
_cache: dict[tuple, dict] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()
    osm_source.reset()


# ----------------------------------------------------------------------- sources
def _overpass_post(query: str) -> list[dict]:
    return osm_source.overpass_post(query)


def _overpass_places(category: str, lat: float, lng: float, radius_km: float) -> list[dict]:
    query = osm_source.overpass_query(tg.OVERPASS_SELECTORS[category], lat, lng, radius_km)
    out = []
    for el in _overpass_post(query):
        point = el.get("center") or el
        p = tg.build_place(el.get("type", "node"), el.get("id"), point.get("lat"), point.get("lon"), el.get("tags") or {})
        if p and p["category"] == category:
            out.append(p)
    return out


def _nominatim_places(category: str, lat: float, lng: float, radius_km: float) -> list[dict]:
    started = time.monotonic()
    out: list[dict] = []
    answered = 0
    steps = sorted({min(s, radius_km) for s in _SEARCH_STEPS_KM} | {radius_km})
    for phrase in tg.NOMINATIM_QUERIES[category]:
        for r in steps:
            if time.monotonic() - started > _NOMINATIM_BUDGET_S:
                return out
            try:
                rows = osm_source.nominatim_rows(phrase, lat, lng, r)
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("Nominatim nearby search %s failed: %s", phrase, e)
                if not answered:
                    raise ProviderError(f"Nominatim unavailable ({type(e).__name__})") from e
                break
            answered += 1
            for row in rows:
                p = tg.build_place(row.get("osm_type", "node"), row.get("osm_id"), row.get("lat"), row.get("lon"),
                                   osm_source.row_to_tags(row), osm_source.row_area_hint(row))
                if p and p["category"] == category:
                    out.append(p)
            if len(rows) >= 15:  # enough near the user for this kind: do not widen the box
                break
    return out


def _saved_places(db: Session, lat: float, lng: float, category: str, radius_km: float) -> list[dict]:
    """The committed OpenStreetMap police/hospital import (services/poi.py). Real mapped
    places but a snapshot, hence labelled; phone numbers are NOT taken from it (the
    column defaults to a placeholder, not a mapped number)."""
    unit_type = {tg.HOSPITAL: "ambulance", tg.POLICE: "police"}.get(category)
    if not unit_type:
        return []
    out = []
    for u in db.query(PoliceUnit).filter(PoliceUnit.unit_type == unit_type, PoliceUnit.osm_id.isnot(None)).all():
        if haversine_m(lat, lng, u.lat, u.lng) / 1000 <= radius_km:
            kind = "Hospital" if category == tg.HOSPITAL else "Police station"
            out.append({
                "id": f"saved:{u.id}", "osm_url": None, "name": u.name, "category": category, "kind": kind,
                "operator_type": None, "emergency": False, "lat": u.lat, "lng": u.lng, "address": None,
                "phone": None, "phones": [], "website": None, "opening_hours": None, "open_24h": False,
                "open_now": None, "details": {}, "rank": 1 if category == tg.HOSPITAL else 0, "source": "saved_osm",
            })
    return out


def _dedupe(places: list[dict]) -> list[dict]:
    def richness(p):
        return sum(bool(p.get(k)) for k in ("address", "phone", "website", "opening_hours"))

    def collapse(items, keyfn):
        best: dict = {}
        for p in items:
            k = keyfn(p)
            if k not in best or richness(p) > richness(best[k]):
                best[k] = p
        return list(best.values())

    return collapse(collapse(places, lambda p: p["id"]),
                    lambda p: (p["name"].strip().lower(), p["kind"], round(p["lat"], 3), round(p["lng"], 3)))


def _key(category: str, lat: float, lng: float, radius_km: float) -> tuple:
    n = osm_source.cache_precision(radius_km)
    return (category, round(lat, n), round(lng, n), radius_km)


def _category_places(db: Session, category: str, lat: float, lng: float, radius_km: float, refresh: bool) -> dict:
    key = _key(category, lat, lng, radius_km)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and not refresh and now - hit["at"] < settings.NEARBY_CACHE_TTL_SECONDS:
        return {**hit, "cached": True, "stale": False}

    errors: list[str] = []
    for provider, fetch in (("overpass", _overpass_places), ("nominatim", _nominatim_places)):
        try:
            places = _dedupe(fetch(category, lat, lng, radius_km))
        except ProviderError as e:
            errors.append(str(e))
            continue
        entry = {"places": places, "provider": provider, "at": time.monotonic()}
        with _lock:
            if len(_cache) > 400:
                _cache.clear()
            _cache[key] = entry
        return {**entry, "cached": False, "stale": False}

    if hit and now - hit["at"] < _STALE_OK_S:  # both down: an old live answer beats none, but say so
        return {**hit, "cached": True, "stale": True}
    saved = _saved_places(db, lat, lng, category, radius_km)
    if saved:
        return {"places": saved, "provider": "saved", "at": now, "cached": False, "stale": True}
    raise NearbyUnavailable("; ".join(errors))


def nearby(db: Session, lat: float, lng: float, category: str, radius_km: float = 5.0,
           refresh: bool = False, tz_offset_min: int | None = None) -> dict:
    """Real places of `category` within `radius_km`, nearest first."""
    if category not in NEARBY_CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(NEARBY_CATEGORIES)}")
    radius_km = max(0.5, min(float(radius_km), MAX_RADIUS_KM))
    if not settings.NEARBY_LIVE_ENABLED:
        raise NearbyUnavailable("Live nearby lookup is switched off on this server")

    got = _category_places(db, category, lat, lng, radius_km, refresh)
    now_local = tg.local_time(tz_offset_min)
    out = []
    for p in got["places"]:
        dist = haversine_m(lat, lng, p["lat"], p["lng"]) / 1000
        if dist > radius_km:
            continue
        item = dict(p)
        item["distance_km"] = round(dist, 2)
        if p["source"] == "osm":  # the clock moves, so this is never cached
            item.update(tg.opening_status(p["opening_hours"], now_local))
        out.append(item)
    out.sort(key=lambda r: (r["distance_km"], r["name"]))
    out = out[:_RESULT_CAP]
    region = maps.reverse_geocode(lat, lng)
    return {
        "lat": lat, "lng": lng, "radius_km": radius_km, "category": category, "places": out, "count": len(out),
        "provider": got["provider"],
        "degraded": got["provider"] in ("nominatim", "saved"),
        "cached": got["cached"], "stale": got["stale"], "generated_at": utc_now().isoformat(),
        "region": {"name": region.get("name"), "country_code": region.get("country_code")} if region else None,
    }
