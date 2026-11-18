"""Maps service abstraction: geocoding, directions/ETA, and nearby-place
search behind one narrow interface. Geocoding has three tiers: Google Maps
Platform when `settings.GOOGLE_MAPS_API_KEY` is set; otherwise free, keyless
OpenStreetMap/Nominatim geocoding (real results, no API key needed); and
only if both are unavailable, a tiny static gazetteer as a last-resort
fallback. Directions/ETA follows the same "real when a key is configured,
honest deterministic fallback when it isn't" shape as services/weather.py
(Nominatim doesn't cover routing, only place lookup).

Every response carries `"demo": bool` so a caller (and the frontend) always
knows whether it's looking at a real routed/geocoded answer or the
fallback -- never silently passed off as live.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.core.config import settings
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)

# In-process cache of successful Nominatim lookups, keyed by normalized
# place name -- an itinerary upload can name the same place (or near-
# duplicate wording) several times, and re-querying a free public service
# for something we already resolved a moment ago is both wasteful and the
# kind of thing that gets an IP rate-limited. Only successes are cached --
# a miss/429 might just be transient, so it's always retried, never frozen
# into a permanent "unresolvable".
_nominatim_cache: dict[str, dict] = {}
_nominatim_lock = threading.Lock()
_last_nominatim_call = 0.0
# Nominatim's usage policy asks for at most ~1 request/second from one
# client -- this is a real, external, shared service, so this is a hard
# floor on how fast we call it, not a tunable performance knob.
_NOMINATIM_MIN_INTERVAL_S = 1.05

# Assumed travel speed for the no-API-key ETA estimate -- a straight-line
# distance / plausible urban speed, not a routed answer. Real driving
# distance/time only ever comes from the Directions API when a key is set.
_DEMO_SPEED_KMH = 25.0

# A small gazetteer of common Indian tourist-circuit cities, used only when
# no Maps API key is configured -- lets itinerary destinations still get a
# coordinate for the map/timeline without a live geocoding call. Extend
# freely; this is explicitly a fallback, not a claim of completeness.
_CITY_GAZETTEER: dict[str, tuple[float, float]] = {
    "delhi": (28.6139, 77.2090), "new delhi": (28.6139, 77.2090),
    "agra": (27.1767, 78.0081), "jaipur": (26.9124, 75.7873),
    "udaipur": (24.5854, 73.7125), "mumbai": (19.0760, 72.8777),
    "bengaluru": (12.9716, 77.5946), "bangalore": (12.9716, 77.5946),
    "chennai": (13.0827, 80.2707), "kolkata": (22.5726, 88.3639),
    "hyderabad": (17.3850, 78.4867), "goa": (15.2993, 74.1240),
    "varanasi": (25.3176, 82.9739), "amritsar": (31.6340, 74.8723),
    "guwahati": (26.1445, 91.7362), "shillong": (25.5788, 91.8933),
    "darjeeling": (27.0410, 88.2663), "kaziranga": (26.5775, 93.1714),
    "manali": (32.2432, 77.1892), "shimla": (31.1048, 77.1734),
    "rishikesh": (30.0869, 78.2676), "kochi": (9.9312, 76.2673),
    "cochin": (9.9312, 76.2673), "mysuru": (12.2958, 76.6394),
    "mysore": (12.2958, 76.6394), "pushkar": (26.4899, 74.5511),
    "jodhpur": (26.2389, 73.0243), "jaisalmer": (26.9157, 70.9083),
    # Named landmarks/attractions, not just cities -- a real itinerary
    # names specific stops ("Kamakhya Temple"), not just the city they're
    # in, so city-only lookups miss most real uploads. Extend freely.
    "kamakhya temple": (26.1664, 91.7050), "umananda island": (26.1875, 91.7458),
    "umananda temple": (26.1875, 91.7458), "assam state zoo": (26.1560, 91.7960),
    "guwahati city center": (26.1445, 91.7362), "city center": (26.1445, 91.7362),
    "taj mahal": (27.1751, 78.0421), "red fort": (28.6562, 77.2410),
    "india gate": (28.6129, 77.2295), "qutub minar": (28.5245, 77.1855),
    "gateway of india": (18.9220, 72.8347), "golden temple": (31.6200, 74.8765),
    "hawa mahal": (26.9239, 75.8267), "amber fort": (26.9855, 75.8513),
    "mysore palace": (12.3052, 76.6552), "charminar": (17.3616, 78.4747),
    "victoria memorial": (22.5448, 88.3426), "howrah bridge": (22.5851, 88.3468),
    "lotus temple": (28.5535, 77.2588), "akshardham temple": (28.6127, 77.2773),
    "meenakshi temple": (9.9195, 78.1193), "mehrangarh fort": (26.2979, 73.0182),
    "dal lake": (34.1237, 74.8748), "marine drive": (18.9440, 72.8235),
}


def _geocode_nominatim(name: str) -> dict | None:
    """Free, keyless geocoding via OpenStreetMap Nominatim -- real results
    for essentially any named place (landmark, hotel, station, ...), not
    just the ~50 cities/monuments in the static gazetteer below. This is
    what makes itinerary-upload geocoding actually work without anyone
    configuring GOOGLE_MAPS_API_KEY. One on-demand request per call site
    (never bulk/background), with a real identifying User-Agent and a
    self-imposed rate floor, per Nominatim's usage policy."""
    key = name.strip().lower()
    cached = _nominatim_cache.get(key)
    if cached is not None:
        return dict(cached)

    global _last_nominatim_call
    with _nominatim_lock:
        wait = _NOMINATIM_MIN_INTERVAL_S - (time.monotonic() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim_call = time.monotonic()

    try:
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": name, "format": "json", "limit": 1,
                # Soft bias toward South Asia (this app's primary domain)
                # without excluding results elsewhere -- bounded=0 keeps it
                # a bias, not a hard filter, so a tourist can still name an
                # international stop.
                "viewbox": "68,37,97,6", "bounded": 0,
            },
            headers={"User-Agent": "SmartTouristSafety/1.0 (college project, itinerary geocoding)"},
            timeout=4.0,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            found = {"lat": float(results[0]["lat"]), "lng": float(results[0]["lon"]), "demo": False}
            _nominatim_cache[key] = found
            return dict(found)
    except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError) as e:
        logger.warning("Nominatim geocoding request failed, falling back: %s", e)
    return None


def geocode(place_name: str) -> dict | None:
    """Resolve a place name to coordinates. Real (Google Geocoding API) when
    a key is configured; otherwise real, free OpenStreetMap/Nominatim
    geocoding; and only if both are unavailable/unreachable, a tiny static
    gazetteer of common Indian tourist cities/landmarks as a last resort.
    Returns None if the place can't be resolved at all -- callers should let
    the tourist place it manually rather than guess."""
    name = place_name.strip()
    if not name:
        return None

    if settings.GOOGLE_MAPS_API_KEY:
        try:
            resp = httpx.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": name, "key": settings.GOOGLE_MAPS_API_KEY},
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("results"):
                loc = data["results"][0]["geometry"]["location"]
                return {"lat": loc["lat"], "lng": loc["lng"], "demo": False}
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("Geocoding API request failed, falling back: %s", e)

    # Tests must stay hermetic/offline -- see Settings.is_test -- so this
    # step is skipped there and falls straight to the deterministic
    # gazetteer below, same as it always has.
    if not settings.is_test:
        live = _geocode_nominatim(name)
        if live:
            return live

    match = _CITY_GAZETTEER.get(name.lower())
    if match:
        return {"lat": match[0], "lng": match[1], "demo": True}
    return None


_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG.sub("", text)


# Travel modes the app exposes -> (Google Directions mode, OSRM service path/profile).
# Public transport has no keyless routing source, so it is only offered when
# GOOGLE_MAPS_API_KEY is configured -- never faked.
TRAVEL_MODES = ("driving", "walking", "bicycling", "transit")
_GOOGLE_MODE = {"driving": "driving", "walking": "walking", "bicycling": "bicycling", "transit": "transit"}
_OSRM_PROFILE = {
    "driving": ("routed-car", "driving"),
    "walking": ("routed-foot", "foot"),
    "bicycling": ("routed-bike", "bike"),
}
# Straight-line fallback speeds per mode (km/h) -- an estimate, flagged demo.
_FALLBACK_SPEED_KMH = {"driving": _DEMO_SPEED_KMH, "walking": 5.0, "bicycling": 15.0, "transit": 20.0}


def available_modes() -> list[str]:
    """Modes that can return a real routed answer in this deployment."""
    modes = ["driving", "walking", "bicycling"]
    if settings.GOOGLE_MAPS_API_KEY:
        modes.append("transit")
    return modes


# Short-lived cache of routed answers: the same leg is asked repeatedly (editing
# an itinerary re-times the same stops, several tourists plan the same landmarks)
# and the free public OSRM servers ask clients to be gentle. Only successes are
# cached; coordinates are rounded to ~1 m so a GPS jitter doesn't defeat it.
_ROUTE_CACHE_TTL_S = 300
_ROUTE_CACHE_MAX = 500
_route_cache: dict[tuple, tuple[float, dict]] = {}
_route_cache_lock = threading.Lock()


def _osrm_route(olat, olng, dlat, dlng, mode: str) -> dict | None:
    key = (round(olat, 5), round(olng, 5), round(dlat, 5), round(dlng, 5), mode)
    now = time.monotonic()
    with _route_cache_lock:
        hit = _route_cache.get(key)
        if hit and hit[0] > now:
            return dict(hit[1])
    result = _osrm_route_uncached(olat, olng, dlat, dlng, mode)
    if result:
        with _route_cache_lock:
            if len(_route_cache) >= _ROUTE_CACHE_MAX:
                _route_cache.clear()
            _route_cache[key] = (now + _ROUTE_CACHE_TTL_S, result)
    return result


def clear_route_cache() -> None:
    with _route_cache_lock:
        _route_cache.clear()


def _osrm_route_uncached(olat, olng, dlat, dlng, mode: str) -> dict | None:
    """Real road/footpath routing via the free OSRM instances run by
    routing.openstreetmap.de (no key). Returns distance, duration, the full
    route geometry and turn-by-turn steps, or None on any failure."""
    profile = _OSRM_PROFILE.get(mode)
    if profile is None:
        return None
    service, name = profile
    try:
        resp = httpx.get(
            f"https://routing.openstreetmap.de/{service}/route/v1/{name}/{olng},{olat};{dlng},{dlat}",
            params={"overview": "full", "geometries": "geojson", "steps": "true"},
            headers={"User-Agent": "MUSAFIR/1.0 (tourist safety app, routing)"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route = data["routes"][0]
        leg = route["legs"][0]
        steps = []
        for st in leg.get("steps", []):
            m = st.get("maneuver", {})
            road = st.get("name") or ""
            kind = (m.get("type") or "").replace("_", " ")
            mod = m.get("modifier") or ""
            text = " ".join(x for x in [kind.capitalize() if kind != "depart" else "Head", mod, f"on {road}" if road else ""] if x).strip()
            if text:
                steps.append(text)
        return {
            "distance_km": round(route["distance"] / 1000, 2),
            "duration_min": round(route["duration"] / 60, 1),
            "demo": False, "steps": steps or None, "provider": "osrm",
            "geometry": [[c[1], c[0]] for c in route["geometry"]["coordinates"]],
        }
    except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as e:
        logger.warning("OSRM routing request failed, falling back: %s", e)
        return None


def directions(
    origin_lat: float, origin_lng: float, dest_lat: float, dest_lng: float, mode: str = "driving",
) -> dict:
    """Distance and ETA between two points for a travel `mode`. Tiers:
    Google Directions (when a key is set; real turn-by-turn, supports transit),
    then free keyless OSRM road routing (driving/walking/cycling), and only if
    both are unreachable a straight-line estimate at an assumed speed, marked
    `demo: True` since it is not a routed answer. `geometry` (when present) is
    the real route polyline as [[lat, lng], ...]; `steps` is a list of
    plain-text instructions suitable for a voice assistant."""
    if mode not in TRAVEL_MODES:
        mode = "driving"
    if settings.GOOGLE_MAPS_API_KEY:
        try:
            resp = httpx.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params={
                    "origin": f"{origin_lat},{origin_lng}",
                    "destination": f"{dest_lat},{dest_lng}",
                    "mode": _GOOGLE_MODE[mode],
                    "key": settings.GOOGLE_MAPS_API_KEY,
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("routes"):
                leg = data["routes"][0]["legs"][0]
                steps = [_strip_html(s["html_instructions"]) for s in leg.get("steps", []) if s.get("html_instructions")]
                return {
                    "distance_km": round(leg["distance"]["value"] / 1000, 2),
                    "duration_min": round(leg["duration"]["value"] / 60, 1),
                    "demo": False, "steps": steps, "provider": "google", "geometry": None,
                }
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            logger.warning("Directions API request failed, falling back: %s", e)

    # Tests stay hermetic/offline (see Settings.is_test), like geocode().
    if not settings.is_test:
        routed = _osrm_route(origin_lat, origin_lng, dest_lat, dest_lng, mode)
        if routed:
            return routed

    distance_km = haversine_m(origin_lat, origin_lng, dest_lat, dest_lng) / 1000
    duration_min = (distance_km / _FALLBACK_SPEED_KMH[mode]) * 60
    return {
        "distance_km": round(distance_km, 2), "duration_min": round(duration_min, 1),
        "demo": True, "steps": None, "provider": "estimate", "geometry": None,
    }


def trip_legs(points: list[tuple[float, float]], mode: str = "driving") -> dict:
    """Leg-by-leg distance/time through an ordered list of (lat, lng) stops,
    plus totals. `demo` is True if ANY leg fell back to an estimate."""
    pairs = list(zip(points, points[1:], strict=False))
    # Legs are independent -- fetch them concurrently (a 15-stop trip would
    # otherwise take 14 round-trips in sequence). map() preserves leg order.
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda ab: directions(ab[0][0], ab[0][1], ab[1][0], ab[1][1], mode), pairs))
    legs = [
        {"distance_km": d["distance_km"], "duration_min": d["duration_min"],
         "demo": d["demo"], "geometry": d.get("geometry")}
        for d in results
    ]
    return {
        "mode": mode, "legs": legs,
        "total_distance_km": round(sum(x["distance_km"] for x in legs), 2),
        "total_duration_min": round(sum(x["duration_min"] for x in legs), 1),
        "demo": any(x["demo"] for x in legs),
    }


def search_places(query: str, limit: int = 5) -> list[dict]:
    """Candidate matches for a place name -> [{name, lat, lng, demo}], best
    first, so the tourist can pick the right "Taj Mahal" rather than trust
    the top hit blindly. Google Geocoding when keyed, else Nominatim (the same
    free, rate-limited service geocode() uses); empty list if neither answers."""
    name = query.strip()
    if not name:
        return []
    if settings.GOOGLE_MAPS_API_KEY:
        try:
            resp = httpx.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"address": name, "key": settings.GOOGLE_MAPS_API_KEY}, timeout=5.0,
            )
            resp.raise_for_status()
            out = [
                {"name": r.get("formatted_address", name), "lat": r["geometry"]["location"]["lat"],
                 "lng": r["geometry"]["location"]["lng"], "demo": False}
                for r in resp.json().get("results", [])[:limit]
            ]
            if out:
                return out
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("Place search (Google) failed, falling back: %s", e)
    if settings.is_test:
        m = _CITY_GAZETTEER.get(name.lower())
        return [{"name": name, "lat": m[0], "lng": m[1], "demo": True}] if m else []

    global _last_nominatim_call
    with _nominatim_lock:
        wait = _NOMINATIM_MIN_INTERVAL_S - (time.monotonic() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim_call = time.monotonic()
    try:
        resp = httpx.get(
            settings.NOMINATIM_URL,
            params={"q": name, "format": "json", "limit": limit, "viewbox": "68,37,97,6", "bounded": 0},
            headers={"User-Agent": "MUSAFIR/1.0 (tourist safety app, place search)"}, timeout=5.0,
        )
        resp.raise_for_status()
        return [
            {"name": r.get("display_name", name), "lat": float(r["lat"]), "lng": float(r["lon"]), "demo": False}
            for r in resp.json()
        ]
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
        logger.warning("Place search (Nominatim) failed: %s", e)
        return []


# --------------------------------------------------------------- type-ahead
_SUGGEST_TTL_S = 600
_SUGGEST_MAX = 500
_suggest_cache: dict[tuple, tuple[float, list[dict]]] = {}
_suggest_lock = threading.Lock()


def clear_suggest_cache() -> None:
    with _suggest_lock:
        _suggest_cache.clear()


def _photon_label(p: dict) -> str:
    """"Leela Ambience Hotel, Sector 24, Gurugram, Haryana, India" from Photon's fields."""
    parts: list[str] = []
    for key in ("name", "street", "district", "locality", "city", "county", "state", "country"):
        v = p.get(key)
        if v and v not in parts:
            parts.append(str(v))
    if p.get("housenumber") and p.get("street") and parts:
        parts[0] = f"{parts[0]} {p['housenumber']}" if parts[0] == p.get("street") else parts[0]
    return ", ".join(parts)


def _photon(query: str, limit: int, near: tuple[float, float] | None) -> list[dict] | None:
    """Type-ahead matches from Photon, or None when the service did not answer
    (so the caller can fall back). An empty list means 'answered: nothing found'."""
    params: dict = {"q": query, "limit": limit, "lang": "en"}
    if near:
        params["lat"], params["lon"] = near  # a soft bias towards the user, not a filter
    try:
        resp = httpx.get(
            settings.PHOTON_URL, params=params, timeout=5.0,
            headers={"User-Agent": "MUSAFIR/1.0 (tourist safety app, place suggestions)"},
        )
        resp.raise_for_status()
        out: list[dict] = []
        for f in resp.json().get("features", []):
            lng, lat = f["geometry"]["coordinates"][:2]
            label = _photon_label(f.get("properties", {}))
            if label:
                out.append({"name": label, "lat": float(lat), "lng": float(lng), "demo": False})
        return out
    except (httpx.HTTPError, KeyError, ValueError, TypeError, IndexError) as e:
        logger.warning("Place suggestions (Photon) failed, falling back: %s", e)
        return None


def suggest_places(query: str, limit: int = 6, near: tuple[float, float] | None = None) -> list[dict]:
    """Live suggestions while the user types: [{name, lat, lng, demo}].

    Photon (built for type-ahead, tolerant of partial words and small typos)
    first; if it is unreachable, the normal place search. Cached for a few
    minutes so retyping or backspacing never repeats a request. Real data only:
    nothing is returned unless a service actually found it."""
    q = " ".join(query.split())
    if len(q) < 2:
        return []
    key = (q.lower(), limit, round(near[0], 1) if near else None, round(near[1], 1) if near else None)
    now = time.monotonic()
    with _suggest_lock:
        hit = _suggest_cache.get(key)
        if hit and now - hit[0] < _SUGGEST_TTL_S:
            return hit[1]
    out: list[dict] | None = None
    if settings.PHOTON_URL and not settings.is_test:
        out = _photon(q, limit, near)
    if out is None:  # Photon off/unreachable -> the regular search (Google or Nominatim)
        out = search_places(q, limit)
    else:
        # Photon answered but found nothing: one more try with the stricter search
        # (it tokenises addresses differently), so a miss here is a real miss.
        out = out or search_places(q, limit)
    with _suggest_lock:
        if len(_suggest_cache) >= _SUGGEST_MAX:
            _suggest_cache.clear()
        if out:  # never cache a miss: it may be a transient failure
            _suggest_cache[key] = (now, out)
    return out
