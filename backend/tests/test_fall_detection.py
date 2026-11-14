"""Fall & crash detection: countdown, cancellation, auto-incident, idempotency,
isolation, wearables, and that the existing SOS workflow is untouched."""
from datetime import timedelta
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.core.security import hash_password
from app.core.time import utc_now
from app.models.alert import Alert
from app.models.fall_event import FallEvent
from app.models.incident import Incident
from app.models.user import User
from app.services import fall_detection as svc
from tests.conftest import _token, make_tourist, make_unit


def _start(client, headers, tid, **over):
    body = {"event_type": "fall", "source": "simulation",
            "client_event_id": over.pop("client_event_id", "evt-0000-0001"),
            "lat": 26.14, "lng": 91.73}
    body.update(over)
    return client.post(f"/api/tourists/{tid}/fall-events", headers=headers, json=body)


def _expire(db, event_id, seconds_past=0):
    """Move the event's deadline into the past (the server owns the clock)."""
    ev = db.get(FallEvent, event_id)
    ev.countdown_deadline = utc_now() - timedelta(seconds=seconds_past)
    db.commit()


def _incidents(db, itype=None):
    q = db.query(Incident)
    if itype:
        q = q.filter(Incident.type == itype)
    return q.all()


@pytest.fixture
def tid(tourist_user):
    return tourist_user.tourist_id


@pytest.fixture
def second_tourist(db, client):
    t = make_tourist(db, name="Other Tourist", doc="XXXX-XXXX-0002")
    u = User(email="other@test.com", full_name="Other", hashed_password=hash_password("otherpass12"),
             role="tourist", tourist_id=t.id)
    db.add(u)
    db.commit()
    return t, _token(client, "other@test.com", "otherpass12")


# ------------------------------------------------------------------ config
def test_config_comes_from_settings_and_requires_auth(client, tourist_headers):
    assert client.get("/api/fall-detection/config").status_code == 401
    r = client.get("/api/fall-detection/config", headers=tourist_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["countdown_seconds"] == settings.FALL_COUNTDOWN_SECONDS
    assert body["thresholds"]["impact_g"] == settings.FALL_IMPACT_G
    assert body["thresholds"]["crash_min_speed_kmh"] == settings.FALL_CRASH_MIN_SPEED_KMH
    assert body["simulation_enabled"] is settings.FALL_SIMULATION_ENABLED


def test_disabled_feature_is_404(client, tourist_headers, tid, monkeypatch):
    monkeypatch.setattr(settings, "FALL_DETECTION_ENABLED", False)
    assert _start(client, tourist_headers, tid).status_code == 404
    assert client.get(f"/api/tourists/{tid}/fall-events/active", headers=tourist_headers).status_code == 404


# ------------------------------------------------------------------ start
@pytest.mark.parametrize("etype", ["fall", "crash"])
def test_detection_starts_countdown_without_creating_an_incident(
        client, tourist_headers, tid, db, etype):
    r = _start(client, tourist_headers, tid, event_type=etype)
    assert r.status_code == 201
    ev = r.json()
    assert ev["status"] == "pending" and ev["event_type"] == etype
    assert ev["simulated"] is True and ev["source"] == "simulation"
    assert 0 < ev["seconds_remaining"] <= settings.FALL_COUNTDOWN_SECONDS
    assert ev["countdown_deadline"].endswith(("Z", "+00:00"))  # unambiguous UTC for browsers
    assert ev["incident_id"] is None
    assert _incidents(db) == []  # nothing until the countdown expires


def test_active_endpoint_returns_pending_then_null(client, tourist_headers, tid):
    assert client.get(f"/api/tourists/{tid}/fall-events/active", headers=tourist_headers).json() is None
    ev = _start(client, tourist_headers, tid).json()
    act = client.get(f"/api/tourists/{tid}/fall-events/active", headers=tourist_headers).json()
    assert act["id"] == ev["id"]
    client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/cancel", headers=tourist_headers)
    assert client.get(f"/api/tourists/{tid}/fall-events/active", headers=tourist_headers).json() is None


def test_normal_movement_never_reaches_the_server(client, tourist_headers, tid, db):
    """Ordinary movement is filtered by the on-device detector; with no
    detection posted there is simply no event and no incident."""
    assert client.get(f"/api/tourists/{tid}/fall-events", headers=tourist_headers).json() == []
    assert _incidents(db) == []


# ------------------------------------------------------------------ cancel
def test_cancel_prevents_incident_even_after_the_deadline(client, tourist_headers, tid, db):
    ev = _start(client, tourist_headers, tid).json()
    r = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/cancel", headers=tourist_headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    _expire(db, ev["id"], seconds_past=60)
    assert svc.tick_fall_events(db) == []  # the sweep must not resurrect it
    assert _incidents(db) == []
    # Confirming a cancelled event is refused; cancelling twice is harmless.
    assert client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm",
                       headers=tourist_headers).status_code == 409
    assert client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/cancel",
                       headers=tourist_headers).status_code == 200
    assert _incidents(db) == []


def test_cancel_in_grace_window_beats_the_sweep(client, tourist_headers, tid, db):
    ev = _start(client, tourist_headers, tid).json()
    _expire(db, ev["id"], seconds_past=1)  # past deadline, inside the sweep grace
    assert svc.tick_fall_events(db) == []  # sweep waits out FALL_CONFIRM_GRACE_SECONDS
    r = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/cancel", headers=tourist_headers)
    assert r.json()["status"] == "cancelled"
    assert _incidents(db) == []


# ------------------------------------------------------------------ expiry -> incident
def test_confirm_before_deadline_is_refused(client, tourist_headers, tid, db):
    ev = _start(client, tourist_headers, tid).json()
    r = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers)
    assert r.status_code == 409 and "still running" in r.json()["detail"]
    assert _incidents(db) == []


@pytest.mark.parametrize("etype", ["fall", "crash"])
def test_expiry_creates_one_critical_incident_via_monitoring(
        client, tourist_headers, admin_headers, tid, db, etype):
    make_unit(db)
    ev = _start(client, tourist_headers, tid, event_type=etype, source="phone_sensor",
                client_event_id=f"evt-{etype}-0001",
                metadata={"peak_g": 6.1, "stillness_ms": 2400}).json()
    _expire(db, ev["id"])
    with patch("app.services.monitoring.broadcast_sync") as bc:
        r = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers)
    assert r.status_code == 200
    out = r.json()
    assert out["status"] == "confirmed" and out["incident_id"]

    inc = db.get(Incident, out["incident_id"])
    assert inc.type == f"{etype}_detected"
    assert inc.severity == "critical" and inc.status == "detected"
    assert inc.tourist_id == tid
    assert (inc.lat, inc.lng) == (26.14, 91.73)
    assert inc.escalation_deadline is not None  # existing escalation workflow takes over
    assert not inc.description.startswith("[SIMULATED]")  # a real-sensor event is never labelled simulated
    assert any(e.note.startswith("Raised from fall_event") for e in inc.events)
    assert db.query(Alert).filter(Alert.type == f"{etype}_detected").count() == 1
    # The existing control-room channel got the incident...
    assert any(c.args[0].get("event") == "incident" and c.args[0]["id"] == inc.id
               for c in bc.call_args_list)
    # ...and it shows on the existing admin Incidents list.
    listed = client.get("/api/incidents", headers=admin_headers).json()
    assert [i for i in listed if i["id"] == inc.id]


def test_confirm_is_idempotent_one_incident(client, tourist_headers, tid, db):
    ev = _start(client, tourist_headers, tid).json()
    _expire(db, ev["id"])
    a = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers).json()
    b = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers).json()
    assert a["incident_id"] == b["incident_id"]
    assert len(_incidents(db, "fall_detected")) == 1


def test_sweep_raises_incident_when_the_app_is_gone(client, tourist_headers, tid, db):
    """No client call at all (app closed / phone dead): the server still escalates."""
    ev = _start(client, tourist_headers, tid).json()
    _expire(db, ev["id"], seconds_past=settings.FALL_CONFIRM_GRACE_SECONDS + 1)
    assert svc.tick_fall_events(db) == [ev["id"]]
    assert svc.tick_fall_events(db) == []  # second tick: nothing left to do
    assert len(_incidents(db, "fall_detected")) == 1
    assert db.get(FallEvent, ev["id"]).resolution == "countdown_expired:sweep"


def test_cancel_and_expiry_cannot_both_win(client, tourist_headers, tid, db):
    """Stale in-memory copies of the same event race: only the first
    transition out of 'pending' takes effect."""
    ev = _start(client, tourist_headers, tid).json()
    stale_a = db.get(FallEvent, ev["id"])
    _expire(db, ev["id"])
    stale_b = db.get(FallEvent, ev["id"])
    svc.cancel_event(db, stale_a)
    db.expire_all()
    with pytest.raises(svc.FallDetectionError):
        svc.confirm_event(db, stale_b)
    assert _incidents(db) == []
    assert db.get(FallEvent, ev["id"]).status == "cancelled"


def test_incident_failure_rolls_the_claim_back_and_is_retried(client, tourist_headers, tid, db):
    ev = _start(client, tourist_headers, tid).json()
    _expire(db, ev["id"], seconds_past=settings.FALL_CONFIRM_GRACE_SECONDS + 1)
    with patch("app.services.monitoring.raise_motion_incident", side_effect=RuntimeError("boom")):
        assert svc.tick_fall_events(db) == []  # swallowed per-event
    row = db.get(FallEvent, ev["id"])
    assert row.status == "pending" and row.incident_id is None  # not marked confirmed without an incident
    assert svc.tick_fall_events(db) == [ev["id"]]  # next sweep succeeds
    assert len(_incidents(db, "fall_detected")) == 1


# ------------------------------------------------------------------ immediate SOS
def test_immediate_sos_runs_the_existing_sos_workflow(client, tourist_headers, tid, db):
    unit = make_unit(db)
    ev = _start(client, tourist_headers, tid).json()
    r = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/sos", headers=tourist_headers,
                    json={"lat": 26.14, "lng": 91.73})
    assert r.status_code == 200
    body = r.json()
    assert body["event"]["status"] == "sos_triggered"
    assert body["sos"]["nearest_unit"]["name"] == unit.name  # real dispatch ran
    inc = db.get(Incident, body["sos"]["incident_id"])
    assert inc.type == "sos" and inc.severity == "critical" and inc.live_tracking_active
    # Expiry afterwards must not create a second incident.
    _expire(db, ev["id"], seconds_past=60)
    assert svc.tick_fall_events(db) == []
    assert len(_incidents(db)) == 1


def test_immediate_sos_without_any_location_still_opens_an_incident(
        client, tourist_headers, tourist_user, tid, db):
    from app.models.tourist import Tourist
    tour = db.get(Tourist, tid)
    tour.last_lat = tour.last_lng = None
    db.commit()
    ev = client.post(f"/api/tourists/{tid}/fall-events", headers=tourist_headers, json={
        "event_type": "fall", "source": "simulation", "client_event_id": "evt-nogps-0001"}).json()
    assert ev["has_location"] is False
    r = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/sos", headers=tourist_headers, json={})
    assert r.status_code == 200 and r.json()["sos"] is None
    inc = db.get(Incident, r.json()["event"]["incident_id"])
    assert inc.lat is None and "Location unavailable" in inc.description


# ------------------------------------------------------------------ missing GPS
def test_missing_gps_falls_back_to_last_known_location(client, tourist_headers, tid, db):
    ev = client.post(f"/api/tourists/{tid}/fall-events", headers=tourist_headers, json={
        "event_type": "crash", "source": "simulation", "client_event_id": "evt-lastknown-1"}).json()
    _expire(db, ev["id"])
    out = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers).json()
    inc = db.get(Incident, out["incident_id"])
    assert (inc.lat, inc.lng) == (26.1445, 91.7362)  # make_tourist's last known fix
    assert "last known location" in inc.description


def test_no_location_anywhere_does_not_crash(client, tourist_headers, tid, db):
    from app.models.tourist import Tourist
    tour = db.get(Tourist, tid)
    tour.last_lat = tour.last_lng = None
    db.commit()
    ev = client.post(f"/api/tourists/{tid}/fall-events", headers=tourist_headers, json={
        "event_type": "fall", "source": "simulation", "client_event_id": "evt-nogps-0002"}).json()
    _expire(db, ev["id"])
    out = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers)
    assert out.status_code == 200
    inc = db.get(Incident, out.json()["incident_id"])
    assert inc.lat is None and inc.station_id is None and "Location unavailable" in inc.description


def test_lat_without_lng_is_rejected(client, tourist_headers, tid):
    r = client.post(f"/api/tourists/{tid}/fall-events", headers=tourist_headers, json={
        "event_type": "fall", "source": "simulation", "client_event_id": "evt-badloc-0001", "lat": 12.0})
    assert r.status_code == 422


# ------------------------------------------------------------------ duplicates
def test_same_client_event_id_is_idempotent(client, tourist_headers, tid, db):
    a = _start(client, tourist_headers, tid)
    b = _start(client, tourist_headers, tid)
    assert (a.status_code, b.status_code) == (201, 200)
    assert a.json()["id"] == b.json()["id"] and b.json()["deduplicated"] is True
    assert db.query(FallEvent).count() == 1


def test_second_detection_while_pending_is_folded_in(client, tourist_headers, tid, db):
    a = _start(client, tourist_headers, tid, client_event_id="evt-dupe-0001").json()
    b = _start(client, tourist_headers, tid, client_event_id="evt-dupe-0002", event_type="crash")
    assert b.status_code == 200 and b.json()["id"] == a["id"] and b.json()["deduplicated"]
    assert db.query(FallEvent).count() == 1


def test_no_second_incident_right_after_one_was_raised(client, tourist_headers, tid, db):
    a = _start(client, tourist_headers, tid, client_event_id="evt-once-00001").json()
    _expire(db, a["id"])
    client.post(f"/api/tourists/{tid}/fall-events/{a['id']}/confirm", headers=tourist_headers)
    b = _start(client, tourist_headers, tid, client_event_id="evt-once-00002")
    assert b.json()["id"] == a["id"] and b.json()["deduplicated"]
    assert len(_incidents(db, "fall_detected")) == 1


def test_a_new_fall_after_a_cancel_is_still_taken_seriously(client, tourist_headers, tid, db):
    a = _start(client, tourist_headers, tid, client_event_id="evt-redo-00001").json()
    client.post(f"/api/tourists/{tid}/fall-events/{a['id']}/cancel", headers=tourist_headers)
    b = _start(client, tourist_headers, tid, client_event_id="evt-redo-00002")
    assert b.status_code == 201 and b.json()["id"] != a["id"]


# ------------------------------------------------------------------ isolation
def test_users_have_isolated_sessions(client, tourist_headers, tid, second_tourist, db):
    other, other_headers = second_tourist
    a = _start(client, tourist_headers, tid, client_event_id="evt-iso-a-0001").json()
    b = _start(client, other_headers, other.id, client_event_id="evt-iso-b-0001").json()
    assert a["id"] != b["id"] and a["status"] == b["status"] == "pending"  # both countdowns live at once

    # B cannot touch A's event, by either path.
    assert client.post(f"/api/tourists/{tid}/fall-events/{a['id']}/cancel",
                       headers=other_headers).status_code == 403
    assert client.post(f"/api/tourists/{other.id}/fall-events/{a['id']}/cancel",
                       headers=other_headers).status_code == 404
    assert client.get(f"/api/tourists/{tid}/fall-events", headers=other_headers).status_code == 403

    # Cancelling A leaves B's countdown running, and B's expiry is B's alone.
    client.post(f"/api/tourists/{tid}/fall-events/{a['id']}/cancel", headers=tourist_headers)
    assert db.get(FallEvent, b["id"]).status == "pending"
    _expire(db, b["id"], seconds_past=settings.FALL_CONFIRM_GRACE_SECONDS + 1)
    svc.tick_fall_events(db)
    incs = _incidents(db, "fall_detected")
    assert len(incs) == 1 and incs[0].tourist_id == other.id


# ------------------------------------------------------------------ simulation honesty
def test_simulated_events_are_labelled_and_never_text_family(client, tourist_headers, tid, db):
    ev = _start(client, tourist_headers, tid).json()
    _expire(db, ev["id"])
    with patch("app.services.notifications._enqueue_sms") as sms:
        out = client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers).json()
    inc = db.get(Incident, out["incident_id"])
    assert inc.description.startswith("[SIMULATED]")
    assert db.query(Alert).filter(Alert.type == "fall_detected").one().message.startswith("[SIMULATED]")
    sms.assert_not_called()


def test_real_detection_texts_emergency_contacts(client, tourist_headers, tid, db):
    ev = _start(client, tourist_headers, tid, source="phone_sensor").json()
    assert ev["simulated"] is False
    _expire(db, ev["id"])
    with patch("app.services.notifications._enqueue_sms") as sms:
        client.post(f"/api/tourists/{tid}/fall-events/{ev['id']}/confirm", headers=tourist_headers)
    assert sms.call_count >= 1
    assert sms.call_args.kwargs["rtype"] == "emergency_contact"


def test_simulation_can_be_disabled(client, tourist_headers, tid, monkeypatch):
    monkeypatch.setattr(settings, "FALL_SIMULATION_ENABLED", False)
    assert _start(client, tourist_headers, tid).status_code == 403
    assert _start(client, tourist_headers, tid, source="phone_sensor",
                  client_event_id="evt-real-00001").status_code == 201


# ------------------------------------------------------------------ validation
@pytest.mark.parametrize("patch_body", [
    {"event_type": "meteor"},
    {"source": "wearable"},                      # wearables use their own authenticated endpoint
    {"client_event_id": "short"},
    {"client_event_id": "has space in it"},
    {"metadata": {"raw_samples": [1, 2, 3]}},     # no raw sensor streams
    {"lat": 123.0, "lng": 0.0},
])
def test_invalid_events_are_rejected(client, tourist_headers, tid, patch_body):
    assert _start(client, tourist_headers, tid, **patch_body).status_code == 422


def test_detection_age_is_relative_and_clamped(client, tourist_headers, tid):
    """A delayed (offline-retried) request starts with the time already used up,
    but a wrong age can neither insta-escalate beyond the cap nor extend the window."""
    a = _start(client, tourist_headers, tid, seconds_since_detection=10).json()
    assert settings.FALL_COUNTDOWN_SECONDS - 11 <= a["seconds_remaining"] <= settings.FALL_COUNTDOWN_SECONDS - 9
    client.post(f"/api/tourists/{tid}/fall-events/{a['id']}/cancel", headers=tourist_headers)
    stale = _start(client, tourist_headers, tid, client_event_id="evt-stale-0001",
                   seconds_since_detection=3600).json()
    expected = max(0, settings.FALL_COUNTDOWN_SECONDS - settings.FALL_MAX_EVENT_AGE_SECONDS)
    assert stale["seconds_remaining"] == expected  # age capped, not trusted past the cap
    assert _start(client, tourist_headers, tid, client_event_id="evt-neg-00001",
                  seconds_since_detection=-5).status_code == 422


# ------------------------------------------------------------------ wearables
def _register_band(client, admin_headers, tid, device_id="BAND-FALL-1"):
    r = client.post("/api/devices/register", headers=admin_headers,
                    json={"tourist_id": tid, "device_id": device_id})
    assert r.status_code == 201
    return device_id, {"X-Device-Key": r.json()["api_key"]}


def test_wearable_event_uses_the_same_countdown_and_pipeline(
        client, admin_headers, tourist_headers, tid, db):
    dev, key = _register_band(client, admin_headers, tid)
    r = client.post(f"/api/devices/{dev}/fall-event", headers=key,
                    json={"event_type": "fall", "lat": 26.15, "lng": 91.74})
    assert r.status_code == 201
    ev = r.json()
    assert ev["source"] == "wearable" and ev["simulated"] is False and ev["device_id"] == dev
    assert ev["status"] == "pending" and _incidents(db) == []
    # the tourist's phone can see and cancel it...
    act = client.get(f"/api/tourists/{tid}/fall-events/active", headers=tourist_headers).json()
    assert act["id"] == ev["id"]
    # ...and if nobody does, the sweep raises the same critical incident.
    _expire(db, ev["id"], seconds_past=settings.FALL_CONFIRM_GRACE_SECONDS + 1)
    svc.tick_fall_events(db)
    inc = _incidents(db, "fall_detected")[0]
    assert inc.severity == "critical" and "wearable" in inc.description


def test_wearable_requires_valid_device_key_and_can_cancel(client, admin_headers, tid, db):
    dev, key = _register_band(client, admin_headers, tid)
    assert client.post(f"/api/devices/{dev}/fall-event", headers={"X-Device-Key": "wrong"},
                       json={"event_type": "fall"}).status_code == 401
    assert client.post(f"/api/devices/{dev}/fall-event", json={"event_type": "fall"}).status_code == 422
    ev = client.post(f"/api/devices/{dev}/fall-event", headers=key, json={"event_type": "crash"}).json()
    r = client.post(f"/api/devices/{dev}/fall-event/{ev['id']}/cancel", headers=key)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _incidents(db) == []


def test_wearable_duplicate_reports_do_not_double_count(client, admin_headers, tid, db):
    dev, key = _register_band(client, admin_headers, tid)
    a = client.post(f"/api/devices/{dev}/fall-event", headers=key, json={"event_type": "fall"})
    b = client.post(f"/api/devices/{dev}/fall-event", headers=key, json={"event_type": "fall"})
    assert (a.status_code, b.status_code) == (201, 200)
    assert db.query(FallEvent).count() == 1


# ------------------------------------------------------------------ admin view + regressions
def test_admin_can_list_events_but_tourists_cannot(client, admin_headers, tourist_headers, tid):
    _start(client, tourist_headers, tid)
    assert len(client.get("/api/fall-events", headers=admin_headers).json()) == 1
    assert client.get("/api/fall-events", headers=tourist_headers).status_code == 403


def test_existing_sos_endpoint_is_unaffected(client, tourist_headers, tid, db):
    make_unit(db)
    r = client.post(f"/api/tourists/{tid}/sos", headers=tourist_headers,
                    json={"lat": 26.14, "lng": 91.73, "message": "help"})
    assert r.status_code == 200 and r.json()["nearest_unit"]
    assert db.query(FallEvent).count() == 0
    assert db.get(Incident, r.json()["incident_id"]).type == "sos"


def test_existing_wearable_telemetry_fall_flag_is_unaffected(client, admin_headers, tid, db):
    dev, key = _register_band(client, admin_headers, tid)
    r = client.post(f"/api/devices/{dev}/telemetry", headers=key,
                    json={"lat": 26.14, "lng": 91.73, "fall_detected": True})
    assert r.status_code == 200
    assert len(_incidents(db, "fall_detected")) == 1  # legacy immediate behaviour preserved
    assert db.query(FallEvent).count() == 0


# ------------------------------------------------------------------ scheduler wiring
def test_sweep_job_lock_expires_before_the_next_tick():
    """Job locks are held until their TTL (never released), so the sweep's lock
    must be shorter than its tick or the 'incident right after the deadline'
    guarantee silently degrades to the shared 120 s TTL."""
    from app import main

    with patch("app.main.run_locked") as locked:
        main._fall_event_tick_job()
    _, job_id, ttl, fn = locked.call_args.args
    assert job_id == "fall_event_tick"
    assert fn is svc.tick_fall_events
    assert 1 <= ttl < settings.FALL_SWEEP_TICK_SECONDS
    assert ttl < settings.JOB_LOCK_TTL_SECONDS


def test_sweep_is_registered_on_the_scheduler(client):
    from app.core import scheduler as job_scheduler

    if not settings.SCHEDULER_ENABLED:
        pytest.skip("scheduler disabled")
    job = job_scheduler.scheduler.get_job("fall_event_tick")
    assert job is not None
    assert job.trigger.interval.total_seconds() == settings.FALL_SWEEP_TICK_SECONDS


# ------------------------------------------------------------------ the atomic claim, against a genuinely stale view
def test_atomic_claim_holds_when_the_loser_still_believes_the_event_is_pending(
        client, tourist_headers, tid, db):
    """Two independent sessions (as two workers/requests would be). The second
    loaded the event while it was pending and never saw it change, so only the
    conditional UPDATE -- not an in-memory status check -- can stop it."""
    from sqlalchemy.orm import sessionmaker

    other = sessionmaker(bind=db.get_bind())()
    try:
        # (a) cancel wins, a stale expiry must not create an incident
        ev = _start(client, tourist_headers, tid, client_event_id="evt-race-a-001").json()
        _expire(db, ev["id"], seconds_past=60)
        stale = other.get(FallEvent, ev["id"])
        assert stale.status == "pending"
        svc.cancel_event(db, db.get(FallEvent, ev["id"]))
        with pytest.raises(svc.FallDetectionError):
            svc.confirm_event(other, stale, now=utc_now() + timedelta(minutes=5))
        assert _incidents(db) == []
        other.rollback()

        # (b) expiry wins, a stale cancel must not un-raise or double-handle it
        ev2 = _start(client, tourist_headers, tid, client_event_id="evt-race-b-001",
                     seconds_since_detection=0).json()
        _expire(db, ev2["id"], seconds_past=60)
        stale2 = other.get(FallEvent, ev2["id"])
        assert stale2.status == "pending"
        svc.confirm_event(db, db.get(FallEvent, ev2["id"]))
        with pytest.raises(svc.FallDetectionError):
            svc.cancel_event(other, stale2)
        assert db.get(FallEvent, ev2["id"]).status == "confirmed"
        assert len(_incidents(db, "fall_detected")) == 1
    finally:
        other.close()
