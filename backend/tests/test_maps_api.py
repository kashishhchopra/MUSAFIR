"""Maps API (app/api/maps.py): geocoding, place search, routing, multi-stop trips,
and the services/maps.py routing fallbacks."""
from app.services import maps


def test_geocode_known_place(client, tourist_headers):
    r = client.get("/api/maps/geocode", params={"place": "Kamakhya Temple"}, headers=tourist_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["lat"] is not None and body["lng"] is not None


def test_geocode_unknown_place_returns_null_not_a_guess(client, tourist_headers):
    r = client.get("/api/maps/geocode", params={"place": "Nowhereville Xyzzy"}, headers=tourist_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["lat"] is None and body["lng"] is None


def test_geocode_requires_auth(client):
    r = client.get("/api/maps/geocode", params={"place": "Delhi"})
    assert r.status_code == 401


def test_directions_falls_back_to_honest_estimate_per_mode():
    drive = maps.directions(26.14, 91.73, 26.20, 91.80, "driving")
    walk = maps.directions(26.14, 91.73, 26.20, 91.80, "walking")
    assert drive["demo"] is True and drive["provider"] == "estimate"  # offline in tests
    assert walk["duration_min"] > drive["duration_min"]  # walking is slower, same distance
    assert drive["distance_km"] == walk["distance_km"] > 0


def test_unknown_mode_defaults_to_driving():
    assert maps.directions(26.14, 91.73, 26.2, 91.8, "teleport")["duration_min"] == \
        maps.directions(26.14, 91.73, 26.2, 91.8, "driving")["duration_min"]


def test_trip_legs_totals():
    pts = [(26.14, 91.73), (26.16, 91.75), (26.20, 91.80)]
    trip = maps.trip_legs(pts, "driving")
    assert len(trip["legs"]) == 2
    assert trip["total_distance_km"] == round(sum(leg["distance_km"] for leg in trip["legs"]), 2)
    assert trip["demo"] is True


def test_route_endpoint(client, tourist_headers):
    r = client.get("/api/maps/route", params={"olat": 26.14, "olng": 91.73, "dlat": 26.2, "dlng": 91.8, "mode": "walking"},
                   headers=tourist_headers)
    assert r.status_code == 200 and r.json()["distance_km"] > 0
    bad = client.get("/api/maps/route", params={"olat": 99, "olng": 91.73, "dlat": 26.2, "dlng": 91.8}, headers=tourist_headers)
    assert bad.status_code == 422
    transit = client.get("/api/maps/route", params={"olat": 26.14, "olng": 91.73, "dlat": 26.2, "dlng": 91.8, "mode": "transit"},
                         headers=tourist_headers)
    assert transit.status_code == 422  # not offered without a Google key -- never faked


def test_modes_hide_transit_without_key(client, tourist_headers):
    assert "transit" not in client.get("/api/maps/modes", headers=tourist_headers).json()["modes"]


def test_trip_endpoint_and_validation(client, tourist_headers):
    body = {"points": [{"lat": 26.14, "lng": 91.73}, {"lat": 26.2, "lng": 91.8}], "mode": "driving"}
    r = client.post("/api/maps/trip", json=body, headers=tourist_headers)
    assert r.status_code == 200 and r.json()["total_duration_min"] > 0
    assert client.post("/api/maps/trip", json={"points": [{"lat": 1, "lng": 1}]}, headers=tourist_headers).status_code == 422


def test_search_endpoint(client, tourist_headers):
    r = client.get("/api/maps/search", params={"q": "taj mahal"}, headers=tourist_headers)
    assert r.status_code == 200 and r.json()["results"][0]["lat"] == 27.1751
    assert client.get("/api/maps/search", params={"q": "  "}, headers=tourist_headers).status_code == 422


def test_trip_legs_keep_order_when_fetched_concurrently(monkeypatch):
    calls = []

    def fake(a, b, c, d, mode="driving"):
        calls.append((a, b))
        return {"distance_km": float(c), "duration_min": float(d), "demo": False, "geometry": None}

    monkeypatch.setattr(maps, "directions", fake)
    pts = [(0.0, 0.0), (1.0, 10.0), (2.0, 20.0), (3.0, 30.0), (4.0, 40.0), (5.0, 50.0)]
    trip = maps.trip_legs(pts, "walking")
    assert [leg["distance_km"] for leg in trip["legs"]] == [1.0, 2.0, 3.0, 4.0, 5.0]  # order preserved
    assert trip["total_distance_km"] == 15.0
    assert sorted(calls) == sorted(pts[:-1])


def test_osrm_results_are_cached_and_failures_are_not(monkeypatch):
    maps.clear_route_cache()
    n = {"calls": 0}
    good = {"distance_km": 1.0, "duration_min": 2.0, "demo": False, "steps": None, "provider": "osrm", "geometry": [[0, 0]]}

    def uncached(*a, **k):
        n["calls"] += 1
        return good if n["calls"] != 1 else None  # first call fails

    monkeypatch.setattr(maps, "_osrm_route_uncached", uncached)
    assert maps._osrm_route(1, 2, 3, 4, "driving") is None     # failure
    assert maps._osrm_route(1, 2, 3, 4, "driving") == good     # retried (not cached), now succeeds
    assert maps._osrm_route(1, 2, 3, 4, "driving") == good     # served from cache
    assert n["calls"] == 2
    assert maps._osrm_route(1, 2, 3, 4, "walking") == good     # different mode = different key
    assert n["calls"] == 3
    maps.clear_route_cache()
