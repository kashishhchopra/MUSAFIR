"""Shared access to the free OpenStreetMap data services used by Discovery and Nearby.

  * Overpass API -- "what is within R km of here". Several endpoints can be configured
    (OVERPASS_API_URL + OVERPASS_API_URLS); an endpoint that errors or times out is
    skipped for a couple of minutes so one overloaded public server cannot slow every
    request.
  * Nominatim -- category searches inside a bounding box, the automatic fallback when
    Overpass is unavailable (rate-limited to 1 request/second by services/maps.py).

Both public services are fair-use. Nothing here invents data: callers get real OSM
records or a ProviderError.
"""
from __future__ import annotations

import logging
import math
import time

import httpx

from app.core.config import settings
from app.services import maps

logger = logging.getLogger(__name__)

ENDPOINT_DOWN_S = 120
RAW_CAP = 1500  # Overpass elements read per query (it does not sort by distance)


class ProviderError(Exception):
    """One data source could not answer."""


_down_until: dict[str, float] = {}  # overpass endpoint -> monotonic time it may be retried


def reset() -> None:
    _down_until.clear()


def endpoints() -> list[str]:
    urls = [settings.OVERPASS_API_URL] + [u.strip() for u in (settings.OVERPASS_API_URLS or "").split(",")]
    seen: list[str] = []
    for u in urls:
        if u and u not in seen:
            seen.append(u)
    return seen


def overpass_post(query: str) -> list[dict]:
    """Run a query on the first healthy endpoint. The first one tried gets the full
    timeout; a second is only a short extra try, so a dead pair cannot hold a request
    up for long. Raises ProviderError when none answers."""
    now = time.monotonic()
    errors: list[str] = []
    tried = 0
    for url in endpoints():
        if _down_until.get(url, 0) > now:
            errors.append(f"{url}: skipped (recently failed)")
            continue
        read = settings.OVERPASS_TIMEOUT_SECONDS if tried == 0 else 5.0
        tried += 1
        try:
            resp = httpx.post(
                url, data={"data": query}, timeout=httpx.Timeout(read, connect=4.0),
                headers={"User-Agent": "MUSAFIR/1.0 (tourist safety app, place lookup)"},
            )
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except (httpx.HTTPError, ValueError) as e:
            _down_until[url] = time.monotonic() + ENDPOINT_DOWN_S
            errors.append(f"{url}: {type(e).__name__}")
            logger.warning("Overpass endpoint %s failed: %s", url, e)
    raise ProviderError("Overpass unavailable (" + "; ".join(errors) + ")")


def overpass_query(selectors: list[str], lat: float, lng: float, radius_km: float) -> str:
    around = f"(around:{int(radius_km * 1000)},{lat:.6f},{lng:.6f})"
    body = "\n".join(f"  nwr{sel}{around};" for sel in selectors)
    return f"[out:json][timeout:{int(settings.OVERPASS_TIMEOUT_SECONDS)}];\n(\n{body}\n);\nout center tags {RAW_CAP};"


def bbox(lat: float, lng: float, radius_km: float) -> str:
    """Nominatim viewbox: left,top,right,bottom."""
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(0.05, math.cos(math.radians(lat))))
    return f"{lng - dlng:.6f},{lat + dlat:.6f},{lng + dlng:.6f},{lat - dlat:.6f}"


def nominatim_rows(phrase: str, lat: float, lng: float, radius_km: float, limit: int = 40) -> list[dict]:
    """One bounded category search ("[amenity=pharmacy]"). Raises httpx.HTTPError/ValueError."""
    params = {
        "q": phrase, "format": "jsonv2", "viewbox": bbox(lat, lng, radius_km), "bounded": 1, "limit": limit,
        "addressdetails": 0, "extratags": 1, "accept-language": "en",
    }
    # The public service now and then times out or answers "busy" for a single request while
    # being fine a moment later; one patient retry keeps an emergency lookup from failing on that.
    for attempt in (0, 1):
        try:
            return maps.nominatim_request(settings.NOMINATIM_URL, params, timeout=10.0)
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            transient = isinstance(e, httpx.TimeoutException) or e.response.status_code in (429, 500, 502, 503, 504)
            if attempt == 1 or not transient:
                raise
            logger.info("Nominatim request retrying after: %s", type(e).__name__)
            time.sleep(1.5)
    raise AssertionError("unreachable")


def row_to_tags(r: dict) -> dict:
    """A Nominatim result re-expressed as OSM tags, so one classifier serves both sources."""
    tags = dict(r.get("extratags") or {})
    if r.get("category") and r.get("type"):
        tags.setdefault(r["category"], r["type"])
    tags["name"] = r.get("name") or tags.get("name") or ""
    return tags


def row_area_hint(r: dict) -> str | None:
    """The area part of OSM's own display name (after the place's name)."""
    return ", ".join((r.get("display_name") or "").split(", ")[1:4]) or None


def cache_precision(radius_km: float) -> int:
    """Decimal places for a cache cell: small searches need a fine grid (a 1 km search
    must not be served from a cell ~1 km away), big searches can share a coarser one."""
    return 3 if radius_km <= 5 else 2
