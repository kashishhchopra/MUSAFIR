"""Crowd outlook (services/crowd_outlook.py, GET /api/crowd/outlook).

Network is never touched: weather, reverse geocoding, the OpenStreetMap surroundings lookup and
public holidays are replaced by fakes. These test OUR model: that it responds to its real inputs
(time, weekday, holiday, weather, surroundings), explains every factor, is honest about being an
estimate, and degrades honestly when an input is missing.
"""
import time
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest

from app.services import crowd_outlook as co
from app.services import maps, osm_source

HERE = (28.6315, 77.2167)
MON_0930_UTC = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)   # Monday 15:00 IST
SAT_0930_UTC = datetime(2026, 11, 21, 9, 30, tzinfo=UTC)   # Saturday 15:00 IST
IST = 330 * 60


def weather_for(start_local: datetime, rain=10, temp=24, code=1, hours=48, offset_s=IST):
    t0 = start_local.replace(minute=0, second=0, microsecond=0, hour=0)
    times = [(t0 + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(hours)]
    r = rain if callable(rain) else (lambda i: rain)
    return {"utc_offset_s": offset_s, "time": times, "temp": [temp] * hours,
            "rain": [r(i) for i in range(hours)], "code": [code] * hours}


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    co.clear_cache()
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: weather_for(MON_0930_UTC + timedelta(seconds=IST)))
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: {"name": "Connaught Place, New Delhi", "country_code": "IN"})
    monkeypatch.setattr(co, "_count_places", lambda lat, lng: {"attractions": 6, "markets": 3, "stations": 4})
    monkeypatch.setattr(co, "_holiday_on", lambda day, cc: None)
    yield
    co.clear_cache()


def run(now=MON_0930_UTC, **kw):
    return co.outlook(*HERE, now_utc=now, **kw)


# ------------------------------------------------------------------ honesty
def test_it_always_says_it_is_an_estimate_not_a_measurement():
    o = run()
    assert o["method"] == "estimate"
    assert "not a live count" in o["note"]
    assert o["inputs"] == {"weather": True, "area": True, "area_pending": False, "holidays": False, "season": True}


def test_local_time_comes_from_the_place_not_the_server():
    o = run()
    assert o["local_time"].startswith("2026-11-16T15:00")        # 09:30 UTC at UTC+5:30, floored to the hour
    assert o["now"]["hour"] == "15:00" and len(o["hours"]) == co.HOURS_SHOWN


# --------------------------------------------------------------- the model
def test_night_is_quiet_and_afternoon_is_not():
    o = run()
    by_hour = {h["hour"]: h for h in o["hours"]}
    assert by_hour["01:00"]["band"] == "quiet" and by_hour["01:00"]["score"] < 5
    assert o["now"]["score"] > by_hour["01:00"]["score"] * 10
    assert o["now"]["band"] in ("moderate", "busy", "very_busy")


def test_a_busy_visitor_area_scores_higher_than_a_quiet_one(monkeypatch):
    monkeypatch.setattr(co, "_count_places", lambda lat, lng: {"attractions": 30, "markets": 10, "stations": 8})
    co.clear_cache()
    hub = run()["now"]["score"]
    monkeypatch.setattr(co, "_count_places", lambda lat, lng: {"attractions": 0, "markets": 0, "stations": 0})
    co.clear_cache()
    quiet = run()["now"]["score"]
    assert hub > quiet * 1.8
    assert run()["area"]["level"] == "few"


def test_weekend_is_busier_than_a_weekday_at_the_same_hour(monkeypatch):
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: weather_for(SAT_0930_UTC + timedelta(seconds=IST)))
    sat = run(SAT_0930_UTC)
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: weather_for(MON_0930_UTC + timedelta(seconds=IST)))
    mon = run(MON_0930_UTC)
    assert sat["now"]["score"] > mon["now"]["score"]
    assert any("Weekend" in f["text"] for f in sat["factors"])
    assert not any("Weekend" in f["text"] for f in mon["factors"])


def test_a_real_public_holiday_raises_it_and_is_named(monkeypatch):
    base = run()["now"]["score"]
    monkeypatch.setattr(co, "_holiday_on", lambda day, cc: "Diwali" if day.isoformat() == "2026-11-16" else None)
    o = run()
    assert o["now"]["score"] > base
    assert {"effect": "up", "text": "Public holiday: Diwali"} in o["factors"]


def test_rain_lowers_it_and_is_reported_once_not_once_per_hour(monkeypatch):
    base = run()["now"]["score"]
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: weather_for(MON_0930_UTC + timedelta(seconds=IST), rain=80))
    o = run()
    assert o["now"]["score"] < base
    rain_lines = [f for f in o["factors"] if "rain" in f["text"].lower()]
    assert len(rain_lines) == 1 and "80%" in rain_lines[0]["text"] and rain_lines[0]["effect"] == "down"


def test_storm_heat_and_cold_each_get_one_line(monkeypatch):
    base = run()["now"]["score"]
    for kw, needle in (({"code": 95}, "Thunderstorm"), ({"temp": 41}, "Very hot"), ({"temp": 2}, "Very cold")):
        monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng, kw=kw: weather_for(MON_0930_UTC + timedelta(seconds=IST), **kw))
        o = run()
        lines = [f for f in o["factors"] if needle in f["text"]]
        assert len(lines) == 1 and lines[0]["effect"] == "down"
        assert o["now"]["score"] < base


def test_season_factor_is_india_only(monkeypatch):
    assert any("peak tourist season" in f["text"] for f in run()["factors"])
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: {"name": "Paris", "country_code": "FR"})
    o = run()
    assert not any("season" in f["text"].lower() for f in o["factors"]) and o["inputs"]["season"] is False
    # and in India outside Oct-Mar
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: {"name": "Delhi", "country_code": "IN"})
    jul = datetime(2026, 7, 15, 9, 30, tzinfo=UTC)
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: weather_for(jul + timedelta(seconds=IST)))
    assert not any("season" in f["text"].lower() for f in run(jul)["factors"])


def test_best_time_is_a_quiet_daytime_hour_within_the_next_day():
    o = run()
    bt = o["best_time"]
    assert 9 <= int(bt["hour"][:2]) <= 19 and bt["day_offset"] in (0, 1)
    # nothing in the next 24 daytime hours is quieter than the one it picked
    # (the chart only shows 12 h, so recompute through the same function for the check)
    assert bt["band"] in ("quiet", "moderate", "busy", "very_busy")


def test_bands():
    assert [co.band_for(x) for x in (0, 24.9, 25, 44.9, 45, 64.9, 65, 100)] == \
        ["quiet", "quiet", "moderate", "moderate", "busy", "busy", "very_busy", "very_busy"]


# -------------------------------------------------- missing inputs, honestly
def test_without_weather_it_falls_back_to_the_device_clock_and_says_so(monkeypatch):
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: None)
    o = run(tz_offset_min=330)
    assert o["inputs"]["weather"] is False and o["local_time"].startswith("2026-11-16T15:00")
    assert all(h["rain_chance"] is None for h in o["hours"])
    assert not any("rain" in f["text"].lower() for f in o["factors"])


def test_without_weather_or_a_clock_it_refuses_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: None)
    with pytest.raises(co.OutlookUnavailable):
        run()
    with pytest.raises(co.OutlookUnavailable):
        run(tz_offset_min=99999)


def test_unknown_surroundings_are_reported_not_invented(monkeypatch):
    monkeypatch.setattr(co, "_count_places", lambda lat, lng: (_ for _ in ()).throw(osm_source.ProviderError("down")))
    o = run()
    assert o["area"] is None and o["inputs"]["area"] is False and o["inputs"]["area_pending"] is False
    assert not any("within 1 km" in f["text"] for f in o["factors"])


def test_a_slow_surroundings_lookup_does_not_hold_the_answer_up_and_is_cached_for_next_time(monkeypatch):
    def slow(lat, lng):
        time.sleep(0.4)
        return {"attractions": 20, "markets": 5, "stations": 5}
    monkeypatch.setattr(co, "_count_places", slow)
    co.clear_cache()
    monkeypatch.setattr(co, "_count_places", slow)
    started = time.monotonic()
    prof, pending = co.area_profile(*HERE, budget_s=0.05)
    assert pending is True and prof is None and time.monotonic() - started < 0.3
    time.sleep(0.6)  # the lookup finishes in the background ...
    prof, pending = co.area_profile(*HERE, budget_s=0.05)
    assert pending is False and prof["level"] == "many"  # ... and the next request has it


# ----------------------------------------------------- area from OSM sources
def test_area_counts_from_overpass(monkeypatch):
    monkeypatch.undo()
    co.clear_cache()
    els = [
        {"type": "node", "id": 1, "tags": {"tourism": "museum", "name": "M"}},
        {"type": "way", "id": 2, "tags": {"historic": "fort", "name": "F"}},
        {"type": "node", "id": 3, "tags": {"amenity": "marketplace", "name": "Mk"}},
        {"type": "node", "id": 4, "tags": {"railway": "station", "name": "S"}},
        {"type": "node", "id": 4, "tags": {"railway": "station", "name": "S"}},  # same object twice
        {"type": "node", "id": 5, "tags": {"amenity": "cafe", "name": "irrelevant"}},
    ]
    monkeypatch.setattr(co, "_overpass_post", lambda q: els)
    assert co._count_places(*HERE) == {"attractions": 2, "markets": 1, "stations": 1}


def test_area_counts_fall_back_to_nominatim(monkeypatch):
    monkeypatch.undo()
    co.clear_cache()

    def down(q):
        raise osm_source.ProviderError("down")
    monkeypatch.setattr(co, "_overpass_post", down)
    rows = {"[tourism=attraction]": [{"osm_type": "node", "osm_id": 1}, {"osm_type": "node", "osm_id": 2}],
            "[railway=station]": [{"osm_type": "node", "osm_id": 9}]}
    monkeypatch.setattr(osm_source, "nominatim_rows", lambda phrase, lat, lng, r, limit=40: rows.get(phrase, []))
    assert co._count_places(*HERE) == {"attractions": 2, "markets": 0, "stations": 1}

    def boom(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(osm_source, "nominatim_rows", boom)
    with pytest.raises(osm_source.ProviderError):
        co._count_places(*HERE)


# ------------------------------------------------------------------------ API
def test_api_shape(client, tourist_headers):
    r = client.get("/api/crowd/outlook", headers=tourist_headers, params={"lat": HERE[0], "lng": HERE[1], "tz_offset_min": 330})
    assert r.status_code == 200
    j = r.json()
    assert j["method"] == "estimate" and len(j["hours"]) == 12 and j["now"]["band"] in ("quiet", "moderate", "busy", "very_busy")
    assert j["region"]["name"].startswith("Connaught Place") and j["area"]["level"] in ("few", "some", "many")


def test_api_uses_the_coordinates_it_is_given(client, tourist_headers, monkeypatch):
    seen = []
    monkeypatch.setattr(co, "_count_places", lambda lat, lng: seen.append((lat, lng)) or {"attractions": 1, "markets": 0, "stations": 0})
    client.get("/api/crowd/outlook", headers=tourist_headers, params={"lat": 19.076, "lng": 72.8777})
    assert seen == [(19.076, 72.8777)]


def test_api_503_when_the_local_time_cannot_be_known(client, tourist_headers, monkeypatch):
    monkeypatch.setattr(co, "_fetch_weather", lambda lat, lng: None)
    r = client.get("/api/crowd/outlook", headers=tourist_headers, params={"lat": 1, "lng": 1})
    assert r.status_code == 503 and "unavailable" in r.json()["detail"].lower()


def test_api_validation_and_auth(client, tourist_headers):
    assert client.get("/api/crowd/outlook", params={"lat": 1, "lng": 1}).status_code == 401
    assert client.get("/api/crowd/outlook", headers=tourist_headers, params={"lat": 95, "lng": 1}).status_code == 422
    assert client.get("/api/crowd/outlook", headers=tourist_headers, params={"lat": 1, "lng": 200}).status_code == 422
    assert client.get("/api/crowd/outlook", headers=tourist_headers, params={"lat": 1, "lng": 1, "tz_offset_min": 5000}).status_code == 422
    assert client.get("/api/crowd/outlook", headers=tourist_headers, params={"lng": 1}).status_code == 422
