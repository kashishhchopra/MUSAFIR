"""Crowd outlook for the tourist's own area: how busy is it likely to be over the next
hours, and when is the quiet time?

HONESTY FIRST: there is no free source of live crowd counts anywhere (Google's "popular
times" is paid and OpenStreetMap has no footfall). So this is an ESTIMATE, always labelled
as one, built only from real inputs about the user's actual position:

  * the real local time at that spot (from the weather service's time-zone for the
    coordinates, or the device's own offset),
  * real hourly weather (Open-Meteo, free, keyless): rain chance, heat, storms,
  * real public holidays (python-holidays if installed, else the Calendarific-key service),
  * what is really mapped within ~1 km in OpenStreetMap (attractions, markets, stations),
  * weekday/weekend, and India's Oct-Mar tourist season (India only -- a heuristic, said so).

Every factor that moves the number is returned by name, so the result is explainable. The
daily activity curve below is the one fixed assumption (people are out in the daytime and
early evening); it is not data about this place. Nothing here is a measurement.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, date, datetime, timedelta

import httpx

from app.core.config import settings
from app.services import holidays as holidays_service
from app.services import maps, osm_source
from app.services.osm_source import ProviderError

logger = logging.getLogger(__name__)

AREA_RADIUS_KM = 1.0
HOURS_SHOWN = 12        # the chart
HOURS_COMPUTED = 24     # "best time" looks a full day ahead
AREA_BUDGET_S = 9.0     # how long a request waits for the surroundings lookup before answering without it
_WEATHER_TTL_S = 1800
_AREA_TTL_S = 6 * 3600
_lock = threading.Lock()
_weather_cache: dict[tuple, tuple[float, dict]] = {}
_area_cache: dict[tuple, tuple[float, dict]] = {}
_area_inflight: dict[tuple, Future] = {}
_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="crowd-outlook")


class OutlookUnavailable(Exception):
    """Not enough real information to say anything (e.g. the local time is unknown)."""


def clear_cache() -> None:
    with _lock:
        _weather_cache.clear()
        _area_cache.clear()
        _area_inflight.clear()
    osm_source.reset()


# Share of "people are out" by local hour -- the single fixed assumption in this module.
_DAY_CURVE = [0.04, 0.03, 0.03, 0.03, 0.04, 0.07, 0.14, 0.24, 0.38, 0.54, 0.70, 0.82,
              0.90, 0.88, 0.85, 0.85, 0.90, 0.95, 0.95, 0.85, 0.68, 0.48, 0.28, 0.13]
_MAX_MULT = 1.8  # the busiest plausible combination maps to 100
_BANDS = [(25, "quiet"), (45, "moderate"), (65, "busy"), (101, "very_busy")]


def band_for(score: float) -> str:
    return next(name for limit, name in _BANDS if score < limit)


# ---------------------------------------------------------------------- weather
def _fetch_weather(lat: float, lng: float) -> dict | None:
    """Hourly weather + the coordinates' UTC offset from Open-Meteo, or None. Never a guess."""
    if settings.is_test:
        return None
    key = (round(lat, 1), round(lng, 1))
    now = time.monotonic()
    with _lock:
        hit = _weather_cache.get(key)
    if hit and now - hit[0] < _WEATHER_TTL_S:
        return hit[1]
    try:
        r = httpx.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lng, "timezone": "auto", "forecast_days": 2,
            "hourly": "temperature_2m,precipitation_probability,weather_code",
        }, timeout=settings.OPENWEATHER_TIMEOUT_SECONDS)
        r.raise_for_status()
        j = r.json()
        h = j["hourly"]
        data = {"utc_offset_s": int(j["utc_offset_seconds"]), "time": h["time"], "temp": h["temperature_2m"],
                "rain": h["precipitation_probability"], "code": h["weather_code"]}
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        logger.warning("Open-Meteo hourly request failed: %s", e)
        return hit[1] if hit else None
    with _lock:
        if len(_weather_cache) > 200:
            _weather_cache.clear()
        _weather_cache[key] = (now, data)
    return data


# ------------------------------------------------------------------------ area
_AREA_SELECTORS = {
    "attractions": ['["tourism"~"^(attraction|museum|gallery|viewpoint|zoo|theme_park)$"]["name"]',
                    '["historic"~"^(monument|fort|castle|palace|temple|archaeological_site)$"]["name"]'],
    "markets": ['["amenity"="marketplace"]["name"]', '["shop"~"^(mall|department_store)$"]["name"]'],
    "stations": ['["railway"="station"]["name"]', '["amenity"="bus_station"]["name"]', '["aeroway"="terminal"]["name"]'],
}
_AREA_NOMINATIM = {
    "attractions": ["[tourism=attraction]", "[tourism=museum]"],
    "markets": ["[amenity=marketplace]", "[shop=mall]"],
    "stations": ["[railway=station]"],
}


def _group_of(tags: dict) -> str | None:
    if tags.get("tourism") in ("attraction", "museum", "gallery", "viewpoint", "zoo", "theme_park") or tags.get("historic"):
        return "attractions"
    if tags.get("amenity") == "marketplace" or tags.get("shop") in ("mall", "department_store"):
        return "markets"
    if tags.get("railway") == "station" or tags.get("amenity") == "bus_station" or tags.get("aeroway") == "terminal":
        return "stations"
    return None


def _overpass_post(query: str) -> list[dict]:
    return osm_source.overpass_post(query)


def _count_places(lat: float, lng: float) -> dict[str, int]:
    """Named attractions / markets / stations mapped within ~1 km: Overpass, else Nominatim."""
    try:
        seen: dict[str, set] = {g: set() for g in _AREA_SELECTORS}
        sels = [s for group in _AREA_SELECTORS.values() for s in group]
        for el in _overpass_post(osm_source.overpass_query(sels, lat, lng, AREA_RADIUS_KM)):
            g = _group_of(el.get("tags") or {})
            if g:
                seen[g].add((el.get("type"), el.get("id")))
        return {g: len(v) for g, v in seen.items()}
    except ProviderError:
        pass
    seen = {g: set() for g in _AREA_SELECTORS}
    answered = 0
    for g, phrases in _AREA_NOMINATIM.items():
        for ph in phrases:
            try:
                rows = osm_source.nominatim_rows(ph, lat, lng, AREA_RADIUS_KM)
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("Area profile search %s failed: %s", ph, e)
                continue
            answered += 1
            for r in rows:
                seen[g].add((r.get("osm_type"), r.get("osm_id")))
    if not answered:
        raise ProviderError("area profile unavailable")
    return {g: len(v) for g, v in seen.items()}


def _compute_area(lat: float, lng: float) -> dict | None:
    """Ask OpenStreetMap what is around, store it, return it (None if it could not be asked)."""
    key = (round(lat, 3), round(lng, 3))
    try:
        counts = _count_places(lat, lng)
    except ProviderError:
        return None
    weighted = counts["attractions"] * 2 + counts["markets"] * 2 + counts["stations"] * 3
    level = "few" if weighted <= 3 else "some" if weighted <= 14 else "many"
    label = {
        "few": "Few attractions, markets or stations mapped within 1 km",
        "some": "A moderate number of attractions, markets or stations within 1 km",
        "many": "Many attractions, markets or stations within 1 km — a busy visitor area",
    }[level]
    prof = {"counts": counts, "level": level, "label": label}
    with _lock:
        if len(_area_cache) > 300:
            _area_cache.clear()
        _area_cache[key] = (time.monotonic(), prof)
    return prof


def area_profile(lat: float, lng: float, budget_s: float = AREA_BUDGET_S) -> tuple[dict | None, bool]:
    """-> (profile | None, pending). The lookup can be slow when the free map services are busy,
    so a request waits at most `budget_s` for it; if it is not done the answer goes out without
    it (pending=True) while the lookup finishes in the background and is cached for the next call."""
    key = (round(lat, 3), round(lng, 3))
    now = time.monotonic()
    with _lock:
        hit = _area_cache.get(key)
        if hit and now - hit[0] < _AREA_TTL_S:
            return hit[1], False
        fut = _area_inflight.get(key)
        if fut is None or fut.done():
            fut = _pool.submit(_compute_area, lat, lng)
            _area_inflight[key] = fut
    try:
        return fut.result(timeout=budget_s), False
    except FutureTimeout:
        return (hit[1] if hit else None), True


_AREA_SCALE = {"few": 0.55, "some": 0.85, "many": 1.15}


# --------------------------------------------------------------------- holidays
def _holiday_on(day: date, country_code: str | None) -> str | None:
    """The real public holiday on `day` (its name), or None. python-holidays when installed
    (covers India and ~200 countries offline); else the Calendarific-key service; else nothing."""
    if not country_code:
        return None
    try:
        import holidays as pyholidays  # optional dependency
        return pyholidays.country_holidays(country_code, years=[day.year]).get(day)
    except ImportError:
        pass
    except Exception as e:  # unknown country code etc.
        logger.info("python-holidays has no data for %s: %s", country_code, e)
        return None
    h = holidays_service.holiday_on(day, country_code)
    return h["name"] if h else None


def holidays_available(country_code: str | None) -> bool:
    if not country_code:
        return False
    try:
        import holidays  # noqa: F401
        return True
    except ImportError:
        return bool(settings.CALENDARIFIC_API_KEY)


# ---------------------------------------------------------------------- outlook
def _weather_factor(rain: float | None, temp: float | None, code: int | None) -> tuple[float, str | None]:
    if code is not None and code >= 95:
        return 0.5, "Thunderstorm forecast"
    if rain is not None and rain >= 60:
        return 0.7, f"Rain likely ({int(rain)}%)"
    if temp is not None and temp >= 38:
        return 0.75, f"Very hot ({temp:.0f}°C)"
    if temp is not None and temp <= 5:
        return 0.8, f"Very cold ({temp:.0f}°C)"
    if rain is not None and rain >= 30:
        return 0.9, f"Some chance of rain ({int(rain)}%)"
    return 1.0, None


def outlook(lat: float, lng: float, tz_offset_min: int | None = None, now_utc: datetime | None = None) -> dict:
    """The next hours at (lat, lng). Raises OutlookUnavailable when the local time at that spot
    cannot be established."""
    now_utc = now_utc or datetime.now(UTC)
    # The three real inputs are independent and the free services slow: ask them at once.
    f_weather = _pool.submit(_fetch_weather, lat, lng)
    f_region = _pool.submit(maps.reverse_geocode, lat, lng)
    area, area_pending = area_profile(lat, lng)
    weather = f_weather.result()
    region = f_region.result()

    if weather:
        offset = timedelta(seconds=weather["utc_offset_s"])
    elif tz_offset_min is not None and -840 <= tz_offset_min <= 840:
        offset = timedelta(minutes=tz_offset_min)
    else:
        raise OutlookUnavailable("The local time at this location could not be determined")
    local_now = (now_utc + offset).replace(tzinfo=None, minute=0, second=0, microsecond=0)

    cc = (region or {}).get("country_code")
    area_scale = _AREA_SCALE[area["level"]] if area else 1.0
    weather_by_time: dict[str, tuple] = {}
    if weather:
        for t, tmp, rain, code in zip(weather["time"], weather["temp"], weather["rain"], weather["code"], strict=False):
            weather_by_time[t[:13]] = (tmp, rain, code)

    hours, notes = [], {}
    weather_seen = {"rain": 0.0, "hot": None, "cold": None, "storm": False}
    for i in range(HOURS_COMPUTED):
        t = local_now + timedelta(hours=i)
        mult = area_scale
        if t.weekday() >= 5:
            mult *= 1.2
            notes.setdefault("weekend", ("up", "Weekend: more people out"))
        hol = _holiday_on(t.date(), cc)
        if hol:
            mult *= 1.25
            notes.setdefault("holiday", ("up", f"Public holiday: {hol}"))
        if cc == "IN" and t.month in (10, 11, 12, 1, 2, 3):
            mult *= 1.1
            notes.setdefault("season", ("up", "India's peak tourist season (Oct–Mar) — a general seasonal assumption"))
        w = weather_by_time.get(t.strftime("%Y-%m-%dT%H"))
        tmp = rain = None
        if w:
            tmp, rain, code = w
            wf, _ = _weather_factor(rain, tmp, code)
            mult *= wf
            if i < HOURS_SHOWN:  # only what is relevant to the hours on screen
                if code is not None and code >= 95:
                    weather_seen["storm"] = True
                weather_seen["rain"] = max(weather_seen["rain"], rain or 0)
                if tmp is not None and tmp >= 38:
                    weather_seen["hot"] = max(weather_seen["hot"] or tmp, tmp)
                if tmp is not None and tmp <= 5:
                    weather_seen["cold"] = min(weather_seen["cold"] if weather_seen["cold"] is not None else tmp, tmp)
        score = min(100.0, round(100 * _DAY_CURVE[t.hour] * mult / _MAX_MULT, 1))
        hours.append({"hour": t.strftime("%H:00"), "day_offset": (t.date() - local_now.date()).days,
                      "score": score, "band": band_for(score), "rain_chance": rain, "temp_c": tmp})

    # one line per kind of weather effect, not one per hour
    if weather_seen["storm"]:
        notes["storm"] = ("down", "Thunderstorm forecast: fewer people out")
    if weather_seen["rain"] >= 60:
        notes["rain"] = ("down", f"Rain likely at times (up to {int(weather_seen['rain'])}%): fewer people out")
    elif weather_seen["rain"] >= 30:
        notes["rain"] = ("down", f"Some chance of rain (up to {int(weather_seen['rain'])}%): slightly fewer people out")
    if weather_seen["hot"] is not None:
        notes["hot"] = ("down", f"Very hot (up to {weather_seen['hot']:.0f}°C): fewer people out")
    if weather_seen["cold"] is not None:
        notes["cold"] = ("down", f"Very cold (down to {weather_seen['cold']:.0f}°C): fewer people out")

    daytime = [h for h in hours if 9 <= int(h["hour"][:2]) <= 19]
    best = min(daytime, key=lambda h: (h["score"], h["day_offset"])) if daytime else None
    factors = [{"effect": e, "text": txt} for e, txt in notes.values()]
    if area:
        factors.insert(0, {"effect": "up" if area["level"] == "many" else "down" if area["level"] == "few" else "neutral",
                           "text": area["label"]})
    shown = hours[:HOURS_SHOWN]
    return {
        "lat": lat, "lng": lng, "method": "estimate",
        "note": "An estimate from the time of day, weekday, holidays, weather and what is mapped nearby — "
                "not a live count of people. No free source of live crowd data exists.",
        "local_time": local_now.isoformat(), "now": shown[0], "hours": shown,
        "best_time": {"hour": best["hour"], "band": best["band"], "day_offset": best["day_offset"]} if best else None,
        "area": area, "factors": factors,
        "inputs": {"weather": bool(weather), "area": area is not None, "area_pending": area_pending,
                   "holidays": holidays_available(cc), "season": cc == "IN"},
        "region": {"name": region.get("name"), "country_code": cc} if region else None,
    }
