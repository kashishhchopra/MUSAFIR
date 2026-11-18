"""Routing/search endpoints (app/api/maps.py) and services/maps.py fallbacks."""
from app.services import maps


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
    r = client.get("/api/maps/route", params=dict(olat=26.14, olng=91.73, dlat=26.2, dlng=91.8, mode="walking"),
                   headers=tourist_headers)
    assert r.status_code == 200 and r.json()["distance_km"] > 0
    bad = client.get("/api/maps/route", params=dict(olat=99, olng=91.73, dlat=26.2, dlng=91.8), headers=tourist_headers)
    assert bad.status_code == 422
    transit = client.get("/api/maps/route", params=dict(olat=26.14, olng=91.73, dlat=26.2, dlng=91.8, mode="transit"),
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
