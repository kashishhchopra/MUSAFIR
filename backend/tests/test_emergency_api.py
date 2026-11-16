"""SOS live-location sharing API (app/api/emergency.py)."""
import pytest

from app.models.incident import Incident
from app.models.tourist import Tourist
from app.services.monitoring import trigger_sos
from tests.conftest import make_tourist, make_unit


@pytest.fixture
def sos_incident(db, tourist_user):
    """An active SOS for the same tourist `tourist_headers` authenticates
    as, so the fixture and the auth fixture always agree on who "self" is."""
    make_unit(db)
    t = db.get(Tourist, tourist_user.tourist_id)
    result = trigger_sos(db, t, 26.1445, 91.7362, "help")
    return db.get(Incident, result["incident_id"])


def _loc(lat=26.15, lng=91.74, **kw):
    return {"lat": lat, "lng": lng, **kw}


# ---------------------------------------------------------------- posting
def test_tourist_can_post_their_own_emergency_location(client, tourist_headers, sos_incident):
    r = client.post(f"/api/incidents/{sos_incident.id}/location",
                    json=_loc(accuracy_m=8, speed_kmh=3.2, heading_deg=92), headers=tourist_headers)
    assert r.status_code == 201


def test_other_tourist_cannot_post_to_someone_elses_incident(client, db, sos_incident):
    from app.core.security import hash_password
    from app.models.user import User

    other_tourist = make_tourist(db, name="Someone Else")
    u = User(email="other@test.com", full_name="Someone Else",
             hashed_password=hash_password("otherpass1"), role="tourist", tourist_id=other_tourist.id)
    db.add(u)
    db.commit()
    r = client.post("/api/auth/login", data={"username": "other@test.com", "password": "otherpass1"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    r = client.post(f"/api/incidents/{sos_incident.id}/location", json=_loc(), headers=headers)
    assert r.status_code == 403


def test_admin_can_post_on_behalf_of_an_incident(client, admin_headers, sos_incident):
    r = client.post(f"/api/incidents/{sos_incident.id}/location", json=_loc(), headers=admin_headers)
    assert r.status_code == 201


def test_posting_to_an_unknown_incident_404s(client, tourist_headers):
    r = client.post("/api/incidents/999999/location", json=_loc(), headers=tourist_headers)
    assert r.status_code == 404


def test_rejects_invalid_latitude(client, tourist_headers, sos_incident):
    r = client.post(f"/api/incidents/{sos_incident.id}/location",
                    json=_loc(lat=999), headers=tourist_headers)
    assert r.status_code == 422


def test_rejects_missing_incident_id_route(client, tourist_headers):
    # No incident id at all -- FastAPI's own routing 404s/405s, never a crash.
    r = client.post("/api/incidents//location", json=_loc(), headers=tourist_headers)
    assert r.status_code in (404, 405)


def test_posting_after_control_room_stopped_tracking_is_rejected(client, admin_headers, tourist_headers, sos_incident):
    client.post(f"/api/incidents/{sos_incident.id}/stop-tracking", headers=admin_headers)
    r = client.post(f"/api/incidents/{sos_incident.id}/location", json=_loc(), headers=tourist_headers)
    assert r.status_code == 409


def test_posting_to_a_resolved_incident_is_rejected(client, admin_headers, tourist_headers, sos_incident):
    client.patch(f"/api/incidents/{sos_incident.id}", json={"status": "resolved"}, headers=admin_headers)
    r = client.post(f"/api/incidents/{sos_incident.id}/location", json=_loc(), headers=tourist_headers)
    assert r.status_code == 409


def test_resolving_an_incident_stops_live_tracking(client, admin_headers, sos_incident, db):
    client.patch(f"/api/incidents/{sos_incident.id}", json={"status": "resolved"}, headers=admin_headers)
    db.refresh(sos_incident)
    assert sos_incident.live_tracking_active is False


# ---------------------------------------------------------------- reading
def test_admin_can_read_the_live_track(client, admin_headers, tourist_headers, sos_incident):
    client.post(f"/api/incidents/{sos_incident.id}/location",
               json=_loc(accuracy_m=8, speed_kmh=3.2, heading_deg=92), headers=tourist_headers)
    r = client.get(f"/api/incidents/{sos_incident.id}/location", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["latest"]["lat"] == 26.15
    assert body["location_status"] == "live"
    assert body["tourist_id"] == sos_incident.tourist_id


def test_responder_can_read_the_live_track(client, responder_headers, sos_incident):
    r = client.get(f"/api/incidents/{sos_incident.id}/location", headers=responder_headers)
    assert r.status_code == 200


def test_tourist_cannot_read_the_police_live_track_endpoint(client, tourist_headers, sos_incident):
    """This endpoint is police/responder-only -- a tourist reads their own
    status through their own SOS confirmation UI, never this route."""
    r = client.get(f"/api/incidents/{sos_incident.id}/location", headers=tourist_headers)
    assert r.status_code == 403


def test_unauthenticated_request_is_rejected(client, sos_incident):
    r = client.get(f"/api/incidents/{sos_incident.id}/location")
    assert r.status_code == 401


# ---------------------------------------------------------------- live list
def test_live_emergencies_list_is_police_only(client, tourist_headers):
    r = client.get("/api/incidents/live", headers=tourist_headers)
    assert r.status_code == 403


def test_live_emergencies_list_shows_active_incidents(client, admin_headers, sos_incident):
    r = client.get("/api/incidents/live", headers=admin_headers)
    assert r.status_code == 200
    ids = [e["incident_id"] for e in r.json()]
    assert sos_incident.id in ids


def test_multiple_simultaneous_emergencies_all_appear(client, admin_headers, db):
    make_unit(db)
    t1 = make_tourist(db, name="Tourist One")
    t2 = make_tourist(db, name="Tourist Two")
    r1 = trigger_sos(db, t1, 26.14, 91.73, "help")
    r2 = trigger_sos(db, t2, 26.20, 91.80, "help")

    r = client.get("/api/incidents/live", headers=admin_headers)
    ids = {e["incident_id"] for e in r.json()}
    assert {r1["incident_id"], r2["incident_id"]}.issubset(ids)


# ---------------------------------------------------------------- stop-tracking
def test_tourist_cannot_end_their_own_emergency(client, tourist_headers, sos_incident, db):
    """Ending an emergency is the control room's decision, not the app's."""
    r = client.post(f"/api/incidents/{sos_incident.id}/stop-tracking", headers=tourist_headers)
    assert r.status_code == 403
    assert "control room" in r.json()["detail"]
    db.refresh(sos_incident)
    assert sos_incident.live_tracking_active is True
    assert sos_incident.status != "resolved"


def test_responder_cannot_stop_tracking(client, responder_headers, sos_incident, db):
    r = client.post(f"/api/incidents/{sos_incident.id}/stop-tracking", headers=responder_headers)
    assert r.status_code == 403
    db.refresh(sos_incident)
    assert sos_incident.live_tracking_active is True


def test_admin_stopping_tracking_keeps_the_case_open(client, admin_headers, sos_incident, db):
    client.post(f"/api/incidents/{sos_incident.id}/stop-tracking", headers=admin_headers)
    db.refresh(sos_incident)
    assert sos_incident.live_tracking_active is False
    assert sos_incident.status != "resolved"      # stopping sharing is not closing the case


def test_admin_can_stop_tracking_too(client, admin_headers, sos_incident):
    r = client.post(f"/api/incidents/{sos_incident.id}/stop-tracking", headers=admin_headers)
    assert r.status_code == 200


# ---------------------------------------------------------------- repeat SOS taps
def test_repeat_sos_press_merges_into_the_open_case(client, tourist_headers, tourist_user, sos_incident, db):
    r = client.post(f"/api/tourists/{tourist_user.tourist_id}/sos", headers=tourist_headers,
                    json={"lat": 26.1445, "lng": 91.7362, "message": "again"})
    assert r.status_code == 200
    assert r.json()["incident_id"] == sos_incident.id      # no duplicate case, no second dispatch
    assert db.query(Incident).filter(Incident.type == "sos").count() == 1


def test_repeat_sos_press_resumes_sharing_that_was_switched_off(client, admin_headers, tourist_headers,
                                                                tourist_user, sos_incident, db):
    """The bug: sharing switched off, SOS pressed again, the app's location post was
    refused and the tourist saw "Emergency Resolved" on an open case."""
    from app.models.incident import IncidentEvent
    client.post(f"/api/incidents/{sos_incident.id}/stop-tracking", headers=admin_headers)
    r = client.post(f"/api/tourists/{tourist_user.tourist_id}/sos", headers=tourist_headers,
                    json={"lat": 26.1445, "lng": 91.7362, "message": "still need help"})
    assert r.status_code == 200
    assert r.json()["incident_id"] == sos_incident.id and r.json()["live_tracking_active"] is True
    db.refresh(sos_incident)
    assert sos_incident.live_tracking_active is True
    notes = [e.note for e in db.query(IncidentEvent).filter_by(incident_id=sos_incident.id).all()]
    assert any("pressed SOS again" in n for n in notes)
    loc = client.post(f"/api/incidents/{sos_incident.id}/location", json=_loc(), headers=tourist_headers)
    assert loc.status_code == 201                            # the app can share its location again


def test_repeat_press_on_a_live_case_writes_nothing_extra(client, tourist_headers, tourist_user, sos_incident, db):
    from app.models.incident import IncidentEvent
    before = db.query(IncidentEvent).filter_by(incident_id=sos_incident.id).count()
    for _ in range(3):
        client.post(f"/api/tourists/{tourist_user.tourist_id}/sos", headers=tourist_headers,
                    json={"lat": 26.1445, "lng": 91.7362, "message": "tap"})
    assert db.query(IncidentEvent).filter_by(incident_id=sos_incident.id).count() == before


def test_sos_after_the_case_was_resolved_opens_a_new_case(client, admin_headers, tourist_headers,
                                                         tourist_user, sos_incident):
    client.patch(f"/api/incidents/{sos_incident.id}", json={"status": "resolved"}, headers=admin_headers)
    r = client.post(f"/api/tourists/{tourist_user.tourist_id}/sos", headers=tourist_headers,
                    json={"lat": 26.1445, "lng": 91.7362, "message": "new emergency"})
    assert r.status_code == 200
    assert r.json()["incident_id"] != sos_incident.id and r.json()["live_tracking_active"] is True
