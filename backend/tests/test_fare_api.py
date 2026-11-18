"""Fair Price API: uses the real coordinates the client sends and says honestly
whether the distance was routed or a straight-line estimate."""
from tests.conftest import make_tourist


def _check(client, headers, tid, **over):
    body = {
        "transport_type": "auto_rickshaw", "pickup_name": "Current location",
        "pickup_lat": 27.18, "pickup_lng": 78.01,
        "destination_name": "Taj Mahal", "destination_lat": 27.1751, "destination_lng": 78.0421,
        "quoted_fare": 150, **over,
    }
    return client.post(f"/api/tourists/{tid}/fare-checks", json=body, headers=headers)


def test_fare_check_uses_sent_coordinates_and_flags_estimate(client, tourist_headers, tourist_user):
    r = _check(client, tourist_headers, tourist_user.tourist_id)
    assert r.status_code == 201
    b = r.json()
    assert (b["pickup_lat"], b["destination_lng"]) == (27.18, 78.0421)
    assert b["distance_km"] > 2  # haversine of those two real points, not a fixed number
    assert b["route_estimated"] is True  # offline test mode -> straight-line, and it says so
    assert b["verdict"] in {"fair", "slightly_high", "overpriced", "suspicious"}


def test_fare_distance_changes_with_destination(client, tourist_headers, tourist_user):
    near = _check(client, tourist_headers, tourist_user.tourist_id, destination_lat=27.181, destination_lng=78.011).json()
    far = _check(client, tourist_headers, tourist_user.tourist_id, destination_lat=28.6, destination_lng=77.2).json()
    assert far["distance_km"] > near["distance_km"] * 50
    assert far["estimated_max"] > near["estimated_max"]


def test_overpriced_quote_is_flagged_and_reportable(client, tourist_headers, tourist_user):
    b = _check(client, tourist_headers, tourist_user.tourist_id, quoted_fare=99999).json()
    assert b["verdict"] == "suspicious"
    rep = client.post(f"/api/tourists/{tourist_user.tourist_id}/fare-checks/{b['id']}/report",
                      json={"note": "too much"}, headers=tourist_headers)
    assert rep.status_code == 200 and rep.json()["reported"] is True


def test_fare_check_validation_and_access(client, tourist_headers, tourist_user, db):
    assert _check(client, tourist_headers, tourist_user.tourist_id, transport_type="rocket").status_code == 400
    assert _check(client, tourist_headers, tourist_user.tourist_id, quoted_fare=0).status_code == 422
    assert _check(client, tourist_headers, tourist_user.tourist_id, pickup_lat=500).status_code == 422
    other = make_tourist(db, name="Someone Else")
    assert _check(client, tourist_headers, other.id).status_code == 403
