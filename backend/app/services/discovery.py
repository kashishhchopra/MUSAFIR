"""Discovery: real places around the tourist -- hidden gems, regional food, homestays.

Data is live OpenStreetMap, from free keyless services, in this order:

  1. Overpass API   -- the proper way to ask "what is within R km of here"
                       (several endpoints can be configured; see OVERPASS_API_URLS).
  2. Nominatim      -- category searches inside a bounding box, used automatically
                       when every Overpass endpoint is down or at capacity (the
                       public ones often are). Rate-limited to 1 request/second.

Nothing is invented and nothing is served from a hardcoded list. When no source can
answer, `DiscoveryUnavailable` is raised so the API says so (HTTP 503) instead of
presenting an empty list as "no places nearby".

Results are cached per ~1 km grid cell / radius / category (an hour), a failing
Overpass endpoint is skipped for a couple of minutes, and a stale copy is served
(and labelled stale) when both sources fail.
"""
from __future__ import annotations

import logging
import math
import threading
import time

import httpx

from app.core.config import settings
from app.core.time import utc_now
from app.services import discovery_tags as tags_mod
from app.services import maps
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)

DISCOVERY_CATEGORIES = tags_mod.CATEGORIES
MAX_RADIUS_KM = 50.0
_RESULT_CAP = 200          # places returned per request
_RAW_CAP = 1500            # Overpass elements read per category (it does not sort by distance)
_STALE_OK_S = 24 * 3600
_ENDPOINT_DOWN_S = 120
_NOMINATIM_LIMIT = 40
_NOMINATIM_BUDGET_S = 30.0


class ProviderError(Exception):
    """One data source could not answer."""


class DiscoveryUnavailable(Exception):
    """No data source could answer and nothing cached exists."""


_lock = threading.Lock()
_cache: dict[tuple, dict] = {}            # key -> {places, provider, at}
_down_until: dict[str, float] = {}        # overpass endpoint -> monotonic time it may be retried
_indoor_cache: dict[tuple, tuple[list[dict], float]] = {}


def clear_cache() -> None:
    with _lock:
        _cache.clear()
        _down_until.clear()
        _indoor_cache.clear()
    maps.clear_suggest_cache()


def _enabled() -> bool:
    return bool(settings.DISCOVERY_LIVE_ENABLED)


# ------------------------------------------------------------------ Overpass
def _endpoints() -> list[str]:
    urls = [settings.OVERPASS_API_URL] + [u.strip() for u in (settings.OVERPASS_API_URLS or "").split(",")]
    seen: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.append(u)
    return seen


def _overpass_post(query: str) -> list[dict]:
    """Run a query on the first healthy endpoint. An endpoint that errors or times
    out is skipped for _ENDPOINT_DOWN_S so one overloaded server cannot slow every
    request. Raises ProviderError when none answers."""
    now = time.monotonic()
    errors: list[str] = []
    tried = 0
    for url in _endpoints():
        if _down_until.get(url, 0) > now:
            errors.append(f"{url}: skipped (recently failed)")
            continue
        # The first healthy-looking endpoint gets the full time; a second one is only a
        # short extra try, so a dead pair cannot hold the request up for long.
        read = settings.OVERPASS_TIMEOUT_SECONDS if tried == 0 else 5.0
        tried += 1
        try:
            resp = httpx.post(
                url, data={"data": query}, timeout=httpx.Timeout(read, connect=4.0),
                headers={"User-Agent": "MUSAFIR/1.0 (tourist safety app, place discovery)"},
            )
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except (httpx.HTTPError, ValueError) as e:
            _down_until[url] = time.monotonic() + _ENDPOINT_DOWN_S
            errors.append(f"{url}: {type(e).__name__}")
            logger.warning("Overpass endpoint %s failed: %s", url, e)
    raise ProviderError("Overpass unavailable (" + "; ".join(errors) + ")")


def _overpass_query(category: str, lat: float, lng: float, radius_km: float) -> str:
    around = f"(around:{int(radius_km * 1000)},{lat:.6f},{lng:.6f})"
    body = "\n".join(f"  nwr{sel}{around};" for sel in tags_mod.OVERPASS_SELECTORS[category])
    return f"[out:json][timeout:{int(settings.OVERPASS_TIMEOUT_SECONDS)}];\n(\n{body}\n);\nout center tags {_RAW_CAP};"


def _overpass_places(category: str, lat: float, lng: float, radius_km: float) -> list[dict]:
    places = []
    for el in _overpass_post(_overpass_query(category, lat, lng, radius_km)):
        point = el.get("center") or el
        place = tags_mod.build_place(el.get("type", "node"), el.get("id"), point.get("lat"), point.get("lon"), el.get("tags") or {})
        if place and place["category"] == category:
            places.append(place)
    return places


# ----------------------------------------------------------------- Nominatim
def _bbox(lat: float, lng: float, radius_km: float) -> str:
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(0.05, math.cos(math.radians(lat))))
    return f"{lng - dlng:.6f},{lat + dlat:.6f},{lng + dlng:.6f},{lat - dlat:.6f}"  # left,top,right,bottom


def _nominatim_places(category: str, lat: float, lng: float, radius_km: float) -> list[dict]:
    """Category searches in the bounding box. If the very first request fails the
    source is unavailable; once some answered, a later failure just yields fewer places."""
    url = settings.NOMINATIM_URL
    box = _bbox(lat, lng, radius_km)
    started = time.monotonic()
    out: list[dict] = []
    answered = 0
    for q in tags_mod.NOMINATIM_QUERIES[category]:
        if time.monotonic() - started > _NOMINATIM_BUDGET_S:
            break
        try:
            rows = maps.nominatim_request(url, {
                "q": q, "format": "jsonv2", "viewbox": box, "bounded": 1, "limit": _NOMINATIM_LIMIT,
                "addressdetails": 0, "extratags": 1, "accept-language": "en",
            }, timeout=10.0)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("Nominatim category search %s failed: %s", q, e)
            if not answered:
                raise ProviderError(f"Nominatim unavailable ({type(e).__name__})") from e
            continue
        answered += 1
        for r in rows:
            tags = dict(r.get("extratags") or {})
            if r.get("category") and r.get("type"):
                tags.setdefault(r["category"], r["type"])
            tags["name"] = r.get("name") or tags.get("name") or ""
            disp = r.get("display_name", "")
            hint = ", ".join(disp.split(", ")[1:4]) or None  # area after the name; from OSM's own display name
            p = tags_mod.build_place(r.get("osm_type", "node"), r.get("osm_id"), r.get("lat"), r.get("lon"), tags, hint)
            if p and p["category"] == category:
                out.append(p)
    return out


# ------------------------------------------------------------------ pipeline
def _dedupe(places: list[dict]) -> list[dict]:
    """The same OSM object twice, or the same name within ~100 m (a node and the building
    way around it), counts once; the record with more detail wins."""
    def richness(p):
        return sum(bool(p.get(k)) for k in ("address", "phone", "website", "description", "cuisine", "opening_hours"))

    def collapse(items, keyfn):
        best: dict = {}
        for p in items:
            k = keyfn(p)
            if k not in best or richness(p) > richness(best[k]):
                best[k] = p
        return list(best.values())

    by_id = collapse(places, lambda p: p["id"])
    return collapse(by_id, lambda p: (p["name"].strip().lower(), round(p["lat"], 3), round(p["lng"], 3)))


def _cache_key(category: str, lat: float, lng: float, radius_km: float) -> tuple:
    return (category, round(lat, 2), round(lng, 2), radius_km)


def _category_places(category: str, lat: float, lng: float, radius_km: float, refresh: bool) -> dict:
    """-> {'places', 'provider', 'cached', 'stale'} for one category (distances not yet computed)."""
    key = _cache_key(category, lat, lng, radius_km)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and not refresh and now - hit["at"] < settings.DISCOVERY_CACHE_TTL_SECONDS:
        return {"places": hit["places"], "provider": hit["provider"], "cached": True, "stale": False}

    errors: list[str] = []
    for provider, fetch in (("overpass", _overpass_places), ("nominatim", _nominatim_places)):
        try:
            places = _dedupe(fetch(category, lat, lng, radius_km))
        except ProviderError as e:
            errors.append(str(e))
            continue
        with _lock:
            if len(_cache) > 400:
                _cache.clear()
            _cache[key] = {"places": places, "provider": provider, "at": time.monotonic()}
        return {"places": places, "provider": provider, "cached": False, "stale": False}

    if hit and now - hit["at"] < _STALE_OK_S:  # both down: an old answer beats none, but say it is old
        return {"places": hit["places"], "provider": hit["provider"], "cached": True, "stale": True}
    raise DiscoveryUnavailable("; ".join(errors))


def _region(lat: float, lng: float) -> dict | None:
    return maps.reverse_geocode(lat, lng)


def discover(lat: float, lng: float, radius_km: float = 10.0, categories: list[str] | None = None,
             refresh: bool = False) -> dict:
    """Real places near (lat, lng). Raises DiscoveryUnavailable when no source answers."""
    radius_km = max(0.5, min(float(radius_km), MAX_RADIUS_KM))
    cats = [c for c in (categories or DISCOVERY_CATEGORIES) if c in DISCOVERY_CATEGORIES] or list(DISCOVERY_CATEGORIES)
    if not _enabled():
        raise DiscoveryUnavailable("Live place discovery is switched off on this server")

    region = _region(lat, lng) if tags_mod.REGIONAL_FOOD in cats else None
    profile = tags_mod.region_profile(region)

    places: list[dict] = []
    providers: set[str] = set()
    cached_all, stale = True, False
    failures: list[str] = []
    for cat in cats:
        try:
            got = _category_places(cat, lat, lng, radius_km, refresh)
        except DiscoveryUnavailable as e:
            failures.append(str(e))
            continue
        providers.add(got["provider"])
        cached_all &= got["cached"]
        stale |= got["stale"]
        places.extend(got["places"])
    if failures and len(failures) == len(cats):
        raise DiscoveryUnavailable(failures[0])

    out = []
    for p in places:
        dist = haversine_m(lat, lng, p["lat"], p["lng"]) / 1000
        if dist > radius_km:
            continue
        item = {k: v for k, v in p.items() if k != "_tags"}
        item["distance_km"] = round(dist, 2)
        item["tip"] = item.get("description") or ""
        item["regional_match"], item["match_label"] = None, None
        if p["category"] == tags_mod.REGIONAL_FOOD:
            keep, match, why = tags_mod.food_relevance(p["_tags"], profile)
            if not keep:
                continue
            item["regional_match"], item["match_label"] = match, why
        out.append(item)
    out.sort(key=lambda r: (r["distance_km"], r["name"]))
    out = out[:_RESULT_CAP]
    counts = {c: sum(1 for p in out if p["category"] == c) for c in cats}
    return {
        "lat": lat, "lng": lng, "radius_km": radius_km, "places": out, "counts": counts,
        "provider": "nominatim" if "nominatim" in providers else "overpass" if providers else None,
        "degraded": "nominatim" in providers,   # backup source: usually fewer places
        "cached": cached_all, "stale": stale, "generated_at": utc_now().isoformat(),
        "region": ({"name": region.get("name"), "country_code": region.get("country_code"),
                    "cuisine_scope": profile["label"] if profile else None} if region else None),
        "partial": bool(failures),  # some categories could not be loaded
    }


def find_discovery(lat: float, lng: float, radius_km: float = 25.0, categories: list[str] | None = None) -> list[dict]:
    return discover(lat, lng, radius_km, categories)["places"]


def offline_bundle(lat: float, lng: float, radius_km: float = 25.0) -> dict:
    """Same data wrapped with a timestamp so a cached copy on-device can show its age."""
    d = discover(lat, lng, radius_km)
    return {"generated_at": d["generated_at"], "lat": lat, "lng": lng, "radius_km": d["radius_km"], "places": d["places"]}


# ------------------------------------------------- indoor alternatives (trip planner)
_INDOOR_QUERY = """
[out:json][timeout:{timeout}];
(
  node["tourism"="museum"](around:{radius_m},{lat},{lng});
  node["tourism"="gallery"](around:{radius_m},{lat},{lng});
  node["amenity"="arts_centre"](around:{radius_m},{lat},{lng});
  node["shop"="mall"](around:{radius_m},{lat},{lng});
);
out center tags;
"""


def find_indoor_alternatives(lat: float, lng: float, radius_km: float = 15.0) -> list[dict]:
    """Real indoor attractions (museum/gallery/arts centre/mall) near a point, from
    Overpass -- used by the Smart Trip Planner to swap an outdoor day for a real indoor
    option on bad-weather days. [] (never invented) on any failure."""
    if not _enabled():
        return []
    key = _cache_key("indoor", lat, lng, radius_km)
    now = time.monotonic()
    cached = _indoor_cache.get(key)
    if cached and cached[1] > now:
        return cached[0]
    query = _INDOOR_QUERY.format(
        timeout=int(settings.OVERPASS_TIMEOUT_SECONDS), radius_m=int(radius_km * 1000), lat=lat, lng=lng)
    try:
        elements = _overpass_post(query)
    except ProviderError as e:
        logger.warning("Indoor-alternative lookup failed: %s", e)
        return cached[0] if cached else []
    out = []
    for el in elements:
        name = (el.get("tags") or {}).get("name")
        point = el.get("center") or el
        elat, elng = point.get("lat"), point.get("lon")
        if not name or elat is None or elng is None:
            continue
        out.append({"name": name, "lat": elat, "lng": elng,
                    "distance_km": round(haversine_m(lat, lng, elat, elng) / 1000, 2)})
    out.sort(key=lambda r: r["distance_km"])
    _indoor_cache[key] = (out, now + settings.DISCOVERY_CACHE_TTL_SECONDS)
    return out
