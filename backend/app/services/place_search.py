"""Place search for pickers (Walk-Me-Home destination, ...): several ranked
matches for what the tourist typed, near where they are -- not one silent
guess.

Providers, in order:
  1. Google Places API (New) Text Search, when GOOGLE_MAPS_API_KEY is set --
     the most complete source: practically every shop, hotel, street and
     address, with typo tolerance.
  2. OpenStreetMap, free and keyless, when Google is not configured or fails:
       * Photon (komoot) -- typo-tolerant, ranks results near the tourist;
       * Nominatim -- OSM's official geocoder, biased to the tourist's area.
     Both are merged, de-duplicated and ranked nearest-area first.
  3. The built-in gazetteer of major cities/landmarks (offline last resort,
     and the only source inside the test suite).

"Near the tourist" = the lat/lng the caller passes, else the tourist's last
known position. Results that are far away still appear, after closer ones,
so a tourist can search another city on purpose.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time

import httpx

from app.core.config import settings
from app.services import maps
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)

_USER_AGENT = "MUSAFIR/1.0 (tourist safety app; place search)"
_CACHE_TTL_S = 600
_CACHE_MAX = 500
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _address(*parts: str | None) -> str:
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return ", ".join(out)


def _google(query: str, lat: float | None, lng: float | None, limit: int) -> list[dict] | None:
    body: dict = {"textQuery": query, "maxResultCount": limit, "languageCode": "en", "regionCode": "IN"}
    if lat is not None and lng is not None:
        body["locationBias"] = {"circle": {"center": {"latitude": lat, "longitude": lng},
                                           "radius": float(settings.PLACE_SEARCH_BIAS_RADIUS_M)}}
    try:
        resp = httpx.post(
            "https://places.googleapis.com/v1/places:searchText",
            json=body,
            headers={"X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
                     "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location"},
            timeout=settings.PLACE_SEARCH_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        places = resp.json().get("places") or []
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Google Places search failed, falling back to OpenStreetMap: %s", e)
        return None
    out = []
    for p in places:
        loc = p.get("location") or {}
        if "latitude" not in loc or "longitude" not in loc:
            continue
        out.append({"name": (p.get("displayName") or {}).get("text") or query,
                    "address": p.get("formattedAddress") or "",
                    "lat": float(loc["latitude"]), "lng": float(loc["longitude"]), "source": "google"})
    return out


def _photon(query: str, lat: float | None, lng: float | None, limit: int) -> list[dict]:
    params: dict = {"q": query, "limit": limit, "lang": "en"}
    if lat is not None and lng is not None:
        params.update(lat=lat, lon=lng)
    try:
        resp = httpx.get(settings.PHOTON_URL, params=params, headers={"User-Agent": _USER_AGENT},
                         timeout=settings.PLACE_SEARCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        features = resp.json().get("features") or []
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Photon search failed: %s", e)
        return []
    out = []
    for f in features:
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        street = " ".join(x for x in (p.get("housenumber"), p.get("street")) if x)
        name = p.get("name") or street or p.get("city") or query
        out.append({
            "name": name,
            "address": _address(street if street != name else None, p.get("district") or p.get("locality"),
                                p.get("city"), p.get("state"), p.get("country")),
            "lat": float(coords[1]), "lng": float(coords[0]), "source": "openstreetmap",
        })
    return out


def _nominatim(query: str, lat: float | None, lng: float | None, limit: int) -> list[dict]:
    params: dict = {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 0}
    if lat is not None and lng is not None:
        d = 0.6  # ~65 km box: a preference, not a filter (bounded=0)
        params.update(viewbox=f"{lng - d},{lat + d},{lng + d},{lat - d}", bounded=0)
    else:
        params.update(viewbox="68,37,97,6", bounded=0)  # South Asia preference
    # Shares the 1 request/second floor with maps.geocode (Nominatim policy).
    with maps._nominatim_lock:
        wait = maps._NOMINATIM_MIN_INTERVAL_S - (time.monotonic() - maps._last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        maps._last_nominatim_call = time.monotonic()
    try:
        resp = httpx.get("https://nominatim.openstreetmap.org/search", params=params,
                         headers={"User-Agent": _USER_AGENT}, timeout=settings.PLACE_SEARCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        rows = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Nominatim search failed: %s", e)
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            display = r.get("display_name") or ""
            name = r.get("name") or display.split(",")[0] or query
            rest = display[len(name):].lstrip(", ") if display.startswith(name) else display
            out.append({"name": name, "address": rest, "lat": float(r["lat"]), "lng": float(r["lon"]),
                        "source": "openstreetmap"})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _gazetteer(query: str, limit: int) -> list[dict]:
    """Built-in landmarks: substring hits plus close spellings ("kamakya
    temple" -> Kamakhya Temple), so common typos of famous places resolve
    even when the live services miss them."""
    q = _norm(query)
    names = list(maps._CITY_GAZETTEER)
    hits = [n for n in names if q in n or n in q]
    hits += [n for n in difflib.get_close_matches(q, names, n=limit, cutoff=0.8) if n not in hits]
    hits.sort(key=lambda n: (n != q, -difflib.SequenceMatcher(None, q, n).ratio()))
    return [{"name": n.title(), "address": "", "lat": maps._CITY_GAZETTEER[n][0],
             "lng": maps._CITY_GAZETTEER[n][1], "source": "builtin"} for n in hits[:limit]]


_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> list[str]:
    # "G.S. Road" and "GS Road" are the same road: drop dots before splitting.
    return _WORD.findall((text or "").lower().replace(".", ""))


def _norm(text: str) -> str:
    return " ".join(_words(text))


def _relevance(query: str, r: dict) -> float:
    """0-1: how well a result matches what was typed. Every query word found
    (allowing small typos) in the name+address, and the name itself close to
    the query, score high -- so the exact place beats a bus stop that merely
    shares one word, and "Christ Church Shillong" matches by name + city."""
    q_words = _words(query)
    if not q_words:
        return 0.0
    hay = _words(f"{r['name']} {r['address']}")
    found = 0
    for w in q_words:
        if w in hay or any(difflib.SequenceMatcher(None, w, h).ratio() >= 0.8 for h in hay):
            found += 1
    coverage = found / len(q_words)
    name, q = _norm(r["name"]), _norm(query)
    similarity = difflib.SequenceMatcher(None, q, name).ratio()
    if name and q and (q in name or name in q):
        # Containment counts, scaled by how much of the longer string it is,
        # so a 100-character office name that mentions the place doesn't tie
        # with the place itself.
        similarity = max(similarity, 0.6 + 0.4 * min(len(q), len(name)) / max(len(q), len(name)))
    return round(0.6 * coverage + 0.4 * similarity, 3)


def _interleave(*lists: list[dict]) -> list[dict]:
    out = []
    for i in range(max((len(x) for x in lists), default=0)):
        out += [x[i] for x in lists if i < len(x)]
    return out


def _merge(results: list[dict], lat: float | None, lng: float | None, limit: int,
           query: str = "") -> list[dict]:
    """De-duplicate (same-ish name within 150 m, or anything within 25 m) and
    rank by match quality first (strong / partial / weak), then distance
    (nearby area <= 60 km, region <= 400 km, rest), then provider order."""
    merged: list[dict] = []
    for r in results:
        dup = False
        for m in merged:
            d = haversine_m(r["lat"], r["lng"], m["lat"], m["lng"])
            same_name = r["name"].strip().lower() == m["name"].strip().lower()
            if d < 25 or (same_name and d < 150):
                dup = True
                break
        if not dup:
            merged.append(dict(r))
    for i, r in enumerate(merged):
        r["_rank"] = i
        if lat is not None and lng is not None:
            r["distance_m"] = round(haversine_m(lat, lng, r["lat"], r["lng"]))
        else:
            r["distance_m"] = None

    def band(r):
        d = r["distance_m"]
        if d is None:
            return 0
        return 0 if d <= 60_000 else 1 if d <= 400_000 else 2

    for r in merged:
        r["_score"] = _relevance(query, r) if query else 1.0

    def tier(r):
        return 0 if r["_score"] >= 0.8 else 1 if r["_score"] >= 0.55 else 2

    merged.sort(key=lambda r: (tier(r), band(r), -r["_score"], r["_rank"]))
    for r in merged:
        r.pop("_rank", None)
        r.pop("_score", None)
    return merged[:limit]


def search(query: str, lat: float | None = None, lng: float | None = None, limit: int = 6) -> dict:
    """{query, provider, results: [{name, address, lat, lng, distance_m, source}]}."""
    q = (query or "").strip()
    limit = max(1, min(int(limit), 10))
    if len(q) < 2:
        return {"query": q, "provider": "none", "results": []}

    key = (q.lower(), None if lat is None else round(lat, 2), None if lng is None else round(lng, 2),
           limit, bool(settings.GOOGLE_MAPS_API_KEY))
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    provider, found = "offline", []
    live = settings.PLACE_SEARCH_LIVE_ENABLED and not settings.is_test
    if live and settings.GOOGLE_MAPS_API_KEY:
        g = _google(q, lat, lng, limit)
        if g:
            provider, found = "google", g
    if not found and live:
        # Ask each source for more than we show, then rank the union.
        osm = _interleave(_photon(q, lat, lng, limit * 2), _nominatim(q, lat, lng, limit))
        if osm:
            provider, found = "openstreetmap", osm
    builtin = _gazetteer(q, limit)
    if not found and builtin:
        provider = "offline"
    found = found + builtin

    out = {"query": q, "provider": provider if found else "none",
           "results": _merge(found, lat, lng, limit, query=q)}
    if found and provider != "offline":
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[key] = (now + _CACHE_TTL_S, out)
    return out


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
