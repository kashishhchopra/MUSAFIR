"""Weather-risk adapter for the safety-score model.

`safety.py` needs one number: 0-100 weather risk. Sources, tried in order:

  1. OpenWeatherMap's "current weather" endpoint, when OPENWEATHER_API_KEY is
     set -- condition codes mapped to an explainable risk score.
  2. Open-Meteo (free, no key, CC-BY 4.0), when OPEN_METEO_ENABLED -- the
     same risk mapping applied to its WMO weather codes. Real data with zero
     setup, so a fresh deployment no longer needs a key to use real weather.
  3. mock: a deterministic function of lat/lng (the project's original
     behaviour), only when neither real source answers (offline demo, tests).

`weather_risk_with_source` also says which source produced the number, so
the safety-score breakdown never passes a mock value off as real weather.
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


def _wmo_code_risk(code: int) -> float:
    """WMO weather interpretation code (Open-Meteo `weather_code`) -> the same
    risk scale `_condition_risk` uses for OpenWeatherMap, so both sources
    score the same weather the same way."""
    if code in (95, 96, 99):  # thunderstorm (96/99: with hail)
        return 80.0
    if code in (51, 53, 55, 56, 57):  # drizzle (56/57 freezing)
        return 20.0
    if code in (61, 63, 80, 81):  # slight/moderate rain or showers
        return 30.0
    if code in (65, 66, 67, 82):  # heavy / freezing rain, violent showers
        return 60.0
    if code in (71, 73, 75, 77, 85, 86):  # snow
        return 50.0
    if code in (45, 48):  # fog
        return 45.0
    if code == 0:  # clear sky
        return 5.0
    return 10.0  # 1-3: partly cloudy / overcast


def _fetch_open_meteo(lat: float, lng: float) -> float | None:
    try:
        resp = httpx.get(
            settings.OPEN_METEO_URL,
            params={"latitude": lat, "longitude": lng,
                    "current": "temperature_2m,weather_code,wind_speed_10m",
                    "wind_speed_unit": "ms"},
            timeout=settings.OPEN_METEO_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        current = resp.json().get("current") or {}
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Open-Meteo request failed, falling back to mock: %s", e)
        return None
    code = current.get("weather_code")
    if code is None:
        return None

    risk = _wmo_code_risk(int(code))
    wind_speed = current.get("wind_speed_10m") or 0.0  # m/s
    if wind_speed > 15:
        risk += 20
    elif wind_speed > 10:
        risk += 10
    temp = current.get("temperature_2m")
    if temp is not None and (temp > 42 or temp < 2):
        risk += 15
    return round(min(100.0, max(0.0, risk)), 1)


def weather_risk_with_source(lat: float, lng: float) -> tuple[float, str]:
    """(0-100 weather risk, source) where source is "openweathermap",
    "open-meteo" or "mock". Real results are cached briefly so every GPS ping
    in the same neighbourhood doesn't cost a network call."""
    key = _cache_key(lat, lng)
    cached = _cache.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]

    result: tuple[float, str] | None = None
    if settings.OPENWEATHER_API_KEY:
        risk = _fetch_openweathermap(lat, lng)
        if risk is not None:
            result = (risk, "openweathermap")
    if result is None and settings.OPEN_METEO_ENABLED:
        risk = _fetch_open_meteo(lat, lng)
        if risk is not None:
            result = (risk, "open-meteo")
    if result is None:
        return mock_weather_risk(lat, lng), "mock"  # not cached: retry real next time

    _cache[key] = (result, now + settings.WEATHER_CACHE_TTL_SECONDS)
    return result


def get_weather_risk(lat: float, lng: float) -> float:
    """0-100 weather risk at a coordinate (see weather_risk_with_source)."""
    return weather_risk_with_source(lat, lng)[0]


_conditions_cache: dict[tuple[float, float], tuple[dict, float]] = {}  # (lat,lng) -> (conditions, expires_at)


def fetch_current_conditions(lat: float, lng: float) -> dict | None:
    """Real current-weather conditions at a coordinate -- the raw fields
    (condition code, description, temperature, wind speed), not the
    collapsed 0-100 risk number `get_weather_risk` returns. Used by
    services/disaster.py to derive genuine weather-hazard advisories
    (extreme heat, dense fog, thunderstorm, ...) straight from what
    OpenWeatherMap actually reported for that spot.

    None when no API key is configured or the request fails -- callers must
    treat that as "no data", never synthesize a hazard from nothing. Cached
    briefly (same TTL as the risk score) so scanning every zone on a
    disaster-feed tick doesn't multiply into one call per zone per minute.
    """
    if not settings.OPENWEATHER_API_KEY:
        return None

    key = _cache_key(lat, lng)
    cached = _conditions_cache.get(key)
    now = time.monotonic()
    if cached and cached[1] > now:
        return cached[0]

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
