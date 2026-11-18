"""An SOS must go out even when the phone has no GPS fix: the server falls back to
the last known position, or raises the incident with the location marked unknown."""
from app.models.incident import Incident
from app.models.tourist import Tourist
from app.services.monitoring import trigger_sos


def _tourist(db, tourist_user):
    return db.get(Tourist, tourist_user.tourist_id)


def test_sos_without_coordinates_uses_last_known_position(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    t.last_lat, t.last_lng = 26.15, 91.74
    db.commit()
    r = client.post(f"/api/tourists/{t.id}/sos", json={"message": "help"}, headers=tourist_headers)
    assert r.status_code == 200, r.text
    assert r.json()["location_known"] is True
    inc = db.get(Incident, r.json()["incident_id"])
    assert (inc.lat, inc.lng) == (26.15, 91.74)


def test_sos_with_no_position_anywhere_is_still_raised(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    t.last_lat = t.last_lng = None
    db.commit()
    r = client.post(f"/api/tourists/{t.id}/sos", json={"message": "help"}, headers=tourist_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["location_known"] is False
    inc = db.get(Incident, body["incident_id"])
    assert inc.severity == "critical" and inc.lat is None and inc.lng is None
    assert "location unknown" in inc.description
    db.refresh(t)
    assert t.last_lat is None  # nothing invented


def test_sos_rejects_out_of_range_or_half_given_coordinates(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    for body in ({"lat": 91, "lng": 10}, {"lat": 10, "lng": 181}):
        assert client.post(f"/api/tourists/{t.id}/sos", json=body, headers=tourist_headers).status_code == 422


def test_trigger_sos_with_unknown_position_does_not_move_the_tourist(db, tourist_user):
    t = _tourist(db, tourist_user)
    t.last_lat, t.last_lng = 26.2, 91.8
    db.commit()
    out = trigger_sos(db, t, None, None, "help")
    assert out["location_known"] is False and out["nearest_unit"] is None
    db.refresh(t)
    assert (t.last_lat, t.last_lng) == (26.2, 91.8)


def test_sos_message_over_500_chars_is_rejected_cleanly(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    r = client.post(f"/api/tourists/{t.id}/sos", json={"lat": 26.1, "lng": 91.7, "message": "x" * 501},
                    headers=tourist_headers)
    assert r.status_code == 422


def test_duress_sos_without_coordinates_is_raised_silently(client, db, tourist_user, tourist_headers):
    from app.core.security import hash_password

    t = _tourist(db, tourist_user)
    t.duress_pin_hash = hash_password("4321")
    t.last_lat = t.last_lng = None
    db.commit()
    r = client.post(f"/api/tourists/{t.id}/sos/duress", json={"pin": "4321"}, headers=tourist_headers)
    assert r.status_code == 200, r.text
    assert r.json()["silent"] is True and r.json()["location_known"] is False


def test_retry_with_a_gps_fix_gives_the_merged_incident_its_location(db, tourist_user):
    t = _tourist(db, tourist_user)
    t.last_lat = t.last_lng = None
    db.commit()
    first = trigger_sos(db, t, None, None, "help")
    assert db.get(Incident, first["incident_id"]).lat is None
    second = trigger_sos(db, t, 26.15, 91.74, "help")  # GPS locked, user taps again
    assert second["incident_id"] == first["incident_id"]
    inc = db.get(Incident, first["incident_id"])
    assert (inc.lat, inc.lng) == (26.15, 91.74)


def test_a_half_given_position_falls_back_to_the_last_known_one(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    t.last_lat, t.last_lng = 26.15, 91.74
    db.commit()
    r = client.post(f"/api/tourists/{t.id}/sos", json={"lat": 10.0}, headers=tourist_headers)
    assert r.status_code == 200, r.text
    assert (db.get(Incident, r.json()["incident_id"]).lat) == 26.15
