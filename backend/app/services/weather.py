"""Weather-risk adapter for the safety-score model.

`safety.py` needs one number: 0-100 weather risk. This has two
implementations behind that same interface:

  - real: OpenWeatherMap's free "current weather" endpoint, condition codes
    mapped to an explainable risk score.
  - real, keyless: Open-Meteo's free current-weather API (no signup, no
    key), used when no OpenWeatherMap key is configured.
  - mock: a deterministic function of lat/lng (the project's original
    behaviour), used ONLY when neither real source is reachable (offline, or
    the test suite) so the safety score still computes.

So out of the box the app uses genuine current conditions; the deterministic
mock is a last-resort offline fallback, never the normal path.
"""
from __future__ import annotations

import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_cache: dict[tuple[float, float], tuple[float, float]] = {}  # (lat,lng) -> (risk, expires_at)
_CACHE_PRECISION = 1  # round to ~11km grid cells so nearby pings share a cache entry


def _cache_key(lat: float, lng: float) -> tuple[float, float]:
    return (round(lat, _CACHE_PRECISION), round(lng, _CACHE_PRECISION))


def mock_weather_risk(lat: float, lng: float) -> float:
    """Deterministic mock weather risk 0-100. No network, always available."""
    return round((abs(lat * 100 + lng * 100) % 60), 1)


def _condition_risk(weather_id: int) -> float:
    group = weather_id // 100
    if group == 2:  # thunderstorm
        return 80.0
    if group == 3:  # drizzle
        return 20.0
    if group == 5:  # rain
        return 60.0 if weather_id >= 502 else 30.0  # 502+ = heavy/violent/extreme
    if group == 6:  # snow
        return 50.0
    if group == 7:  # fog/dust/haze/tornado etc
        return 45.0
    if weather_id == 800:  # clear sky
        return 5.0
    return 10.0  # clouds (801-804)


# WMO weather interpretation codes (Open-Meteo) -> the OpenWeatherMap-style
# condition ids the rest of this module (and services/disaster.py) already
# understand, so one risk mapping serves both providers.
_WMO_TO_OWM = {
    0: 800, 1: 801, 2: 802, 3: 804, 45: 741, 48: 741,
    51: 300, 53: 301, 55: 302, 56: 511, 57: 511,
    61: 500, 63: 501, 65: 502, 66: 511, 67: 511,
    71: 600, 73: 601, 75: 602, 77: 600,
    80: 520, 81: 521, 82: 522, 85: 620, 86: 621,
    95: 200, 96: 202, 99: 202,
}
_WMO_DESC = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle", 56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _fetch_open_meteo(lat: float, lng: float) -> dict | None:
    """Real current conditions from Open-Meteo (free, no key), normalised to the
    same dict shape as OpenWeatherMap's: weather_id / description / temp_c /
    wind_speed_ms / observed_at. None on any failure -- never a guess."""
    try:
        resp = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lng, "wind_speed_unit": "ms",
                "current": "weather_code,temperature_2m,wind_speed_10m",
            },
            timeout=settings.OPENWEATHER_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        cur = resp.json()["current"]
        code = int(cur["weather_code"])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logger.warning("Open-Meteo request failed: %s", e)
        return None
    return {
        "weather_id": _WMO_TO_OWM.get(code, 800),
        "description": _WMO_DESC.get(code, ""),
        "temp_c": cur.get("temperature_2m"),
        "wind_speed_ms": cur.get("wind_speed_10m") or 0.0,
        "observed_at": None,
    }


def _risk_from_conditions(c: dict) -> float:
    risk = _condition_risk(c.get("weather_id", 800))
    wind_speed = c.get("wind_speed_ms") or 0.0
    if wind_speed > 15:
        risk += 20
    elif wind_speed > 10:
        risk += 10
    temp = c.get("temp_c")
    if temp is not None and (temp > 42 or temp < 2):
        risk += 15
    return round(min(100.0, max(0.0, risk)), 1)


def _fetch_openweathermap(lat: float, lng: float) -> float | None:
    try:
        resp = httpx.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lng, "appid": settings.OPENWEATHER_API_KEY,
                   "units": "metric"},
            timeout=settings.OPENWEATHER_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("OpenWeatherMap request failed, falling back to mock: %s", e)
        return None

    weather_id = (data.get("weather") or [{}])[0].get("id", 800)
    risk = _condition_risk(weather_id)

    wind_speed = (data.get("wind") or {}).get("speed", 0.0)  # m/s
    if wind_speed > 15:
        risk += 20
    elif wind_speed > 10:
        risk += 10

    temp = (data.get("main") or {}).get("temp")
    if temp is not None and (temp > 42 or temp < 2):
        risk += 15

    return round(min(100.0, max(0.0, risk)), 1)


def get_weather_risk(lat: float, lng: float) -> float:
    """0-100 weather risk at a coordinate. Cached briefly to avoid a network
    call on every single GPS ping in the same neighbourhood. Real OpenWeatherMap
    when keyed, else real Open-Meteo, else (offline/tests) the deterministic mock."""
    if not settings.OPENWEATHER_API_KEY and settings.is_test:
        return mock_weather_risk(lat, lng)

    key = _cache_key(lat, lng)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]

    if settings.OPENWEATHER_API_KEY:
        risk = _fetch_openweathermap(lat, lng)
    else:
        conditions = _fetch_open_meteo(lat, lng)
        risk = _risk_from_conditions(conditions) if conditions else None
    if risk is None:
        return mock_weather_risk(lat, lng)

    _cache[key] = (risk, now + settings.WEATHER_CACHE_TTL_SECONDS)
    return risk


_conditions_cache: dict[tuple[float, float], tuple[dict, float]] = {}  # (lat,lng) -> (conditions, expires_at)


def fetch_current_conditions(lat: float, lng: float) -> dict | None:
    """Real current-weather conditions at a coordinate -- the raw fields
    (condition code, description, temperature, wind speed), not the
    collapsed 0-100 risk number `get_weather_risk` returns. Used by
    services/disaster.py to derive genuine weather-hazard advisories
    (extreme heat, dense fog, thunderstorm, ...) straight from what
    OpenWeatherMap actually reported for that spot.

    Uses OpenWeatherMap when keyed, else the free keyless Open-Meteo. None
    when neither is reachable (or in the offline test suite) -- callers must
    treat that as "no data", never synthesize a hazard from nothing. Cached
    briefly (same TTL as the risk score) so scanning every zone on a
    disaster-feed tick doesn't multiply into one call per zone per minute.
    """
    if not settings.OPENWEATHER_API_KEY and settings.is_test:
        return None

    key = _cache_key(lat, lng)
    cached = _conditions_cache.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]

    if not settings.OPENWEATHER_API_KEY:
        conditions = _fetch_open_meteo(lat, lng)
        if conditions:
            _conditions_cache[key] = (conditions, now + settings.WEATHER_CACHE_TTL_SECONDS)
        return conditions

    try:
        resp = httpx.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"lat": lat, "lon": lng, "appid": settings.OPENWEATHER_API_KEY,
                   "units": "metric"},
            timeout=settings.OPENWEATHER_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("OpenWeatherMap conditions request failed: %s", e)
        return None

    weather = (data.get("weather") or [{}])[0]
    conditions = {
        "weather_id": weather.get("id", 800),
        "description": weather.get("description", ""),
        "temp_c": (data.get("main") or {}).get("temp"),
        "wind_speed_ms": (data.get("wind") or {}).get("speed", 0.0),
        "observed_at": data.get("dt"),
    }
    _conditions_cache[key] = (conditions, now + settings.WEATHER_CACHE_TTL_SECONDS)
    return conditions


def clear_cache() -> None:
    _cache.clear()
    _conditions_cache.clear()
