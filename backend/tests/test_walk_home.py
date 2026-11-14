"""Walk-Me-Home: a monitored journey on top of the existing check-in engine.

Covers creation/validation, authorization, guardian scoping, check-ins, the
missed -> grace -> incident escalation (through services/checkin.py), duplicate
prevention, races, location handling, end/cancel, and that outings, SOS and the
incident pipeline are untouched."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.core.time import utc_now
from app.models.alert import Alert
from app.models.checkin import CheckIn
from app.models.incident import Incident
from app.models.notification import NotificationLog
from app.models.user import User
from app.services import walk_home as svc
from app.services.checkin import tick_checkins
from tests.conftest import _token, make_tourist, make_unit


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


def _guardian(client, headers, tid, name="Mum", contact="+919000000001"):
    r = client.post(f"/api/tourists/{tid}/guardians", headers=headers,
                    json={"guardian_name": name, "guardian_contact": contact})
    assert r.status_code == 201, r.text
    return r.json()


def _start(client, headers, tid, **over):
    body = {"destination_name": "Hostel Aurora", "dest_lat": 26.15, "dest_lng": 91.74,
            "duration_minutes": 30, "check_interval_minutes": 10, "guardian_ids": []}
    body.update(over)
    return client.post(f"/api/tourists/{tid}/walk-home", headers=headers, json=body)


def _journey(client, headers, tid, **over):
    r = _start(client, headers, tid, **over)
    assert r.status_code == 201, r.text
    return r.json()


def _row(db, sid):
    db.expire_all()
    return db.get(CheckIn, sid)


def _overdue(db, sid, minutes_past=0.0):
    """Move the journey's check-in deadline into the past (the server owns the clock)."""
    c = db.get(CheckIn, sid)
    c.expected_return_at = utc_now() - timedelta(minutes=minutes_past, seconds=1)
    db.commit()


def _walk_incidents(db):
    return db.query(Incident).filter(Incident.type == "missed_checkin").all()


def _tz(v):
    """tz-aware ISO string (FastAPI renders UTC as +00:00; both are unambiguous)."""
    return v.endswith(("Z", "+00:00"))


def _url(tid, sid, tail):
    return f"/api/tourists/{tid}/walk-home/{sid}/{tail}"


# ------------------------------------------------------------------ config / create
def test_config_comes_from_settings_and_requires_auth(client, tourist_headers):
    assert client.get("/api/walk-home/config").status_code == 401
    cfg = client.get("/api/walk-home/config", headers=tourist_headers).json()
    assert cfg["duration"]["default"] == settings.WALKHOME_DEFAULT_DURATION_MINUTES
    assert cfg["interval"]["max"] == settings.WALKHOME_MAX_INTERVAL_MINUTES
    assert cfg["grace"]["default"] == settings.WALKHOME_DEFAULT_GRACE_MINUTES
    assert cfg["location_interval_seconds"] == settings.WALKHOME_LOCATION_INTERVAL_SECONDS
    assert all(isinstance(p, int) for p in cfg["duration"]["presets"])


def test_create_journey_stores_the_session(client, tourist_headers, tid, db):
    g = _guardian(client, tourist_headers, tid)
    before = utc_now()
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]], start_lat=26.14, start_lng=91.73)
    row = _row(db, j["id"])
    assert row.kind == "walk_home" and row.status == "planned" and row.tourist_id == tid
    assert row.destination_name == "Hostel Aurora" and (row.dest_lat, row.dest_lng) == (26.15, 91.74)
    assert (row.start_lat, row.start_lng) == (26.14, 91.73)
    assert row.arrive_by - row.started_at == timedelta(minutes=30)
    assert row.expected_return_at - row.started_at == timedelta(minutes=10)  # first check-in = 1 interval
    assert row.grace_minutes == settings.WALKHOME_DEFAULT_GRACE_MINUTES
    assert row.check_interval_minutes == 10 and row.checkin_count == 0 and row.last_checkin_at is None
    assert before <= row.started_at <= utc_now()
    assert j["guardians"] == [{"id": g["id"], "name": "Mum", "has_access": True}]
    assert _tz(j["next_checkin_deadline"]) and j["phase"] == "on_track"


def test_first_deadline_never_beyond_arrival(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, duration_minutes=5, check_interval_minutes=30)
    row = _row(db, j["id"])
    assert row.expected_return_at == row.arrive_by


def test_defaults_come_from_settings_when_omitted(client, tourist_headers, tid, db):
    r = client.post(f"/api/tourists/{tid}/walk-home", headers=tourist_headers,
                    json={"destination_name": "Home", "duration_minutes": 60})
    row = _row(db, r.json()["id"])
    assert row.check_interval_minutes == settings.WALKHOME_DEFAULT_INTERVAL_MINUTES
    assert row.grace_minutes == settings.WALKHOME_DEFAULT_GRACE_MINUTES


@pytest.mark.parametrize("over", [
    {"duration_minutes": settings.WALKHOME_MAX_DURATION_MINUTES + 1},
    {"duration_minutes": settings.WALKHOME_MIN_DURATION_MINUTES - 1},
    {"check_interval_minutes": settings.WALKHOME_MAX_INTERVAL_MINUTES + 1},
    {"grace_minutes": settings.WALKHOME_MAX_GRACE_MINUTES + 1},
    {"destination_name": "   "},
    {"destination_name": ""},
    {"dest_lat": 91.0, "dest_lng": 10.0},
    {"dest_lat": 10.0, "dest_lng": 181.0},
    {"dest_lat": 10.0, "dest_lng": None},
    {"start_lat": 10.0, "start_lng": None},
    {"guardian_ids": list(range(1, 60))},
    {"surprise": "field"},
])
def test_create_validation(client, tourist_headers, tid, over):
    assert _start(client, tourist_headers, tid, **over).status_code in (400, 422)


def test_only_one_active_journey(client, tourist_headers, tid):
    first = _journey(client, tourist_headers, tid)
    again = _start(client, tourist_headers, tid)
    assert again.status_code == 409 and "already" in again.json()["detail"]
    assert client.post(_url(tid, first["id"], "cancel"), headers=tourist_headers).status_code == 200
    assert _start(client, tourist_headers, tid).status_code == 201  # free again


def test_database_itself_rejects_a_second_active_journey(client, tourist_headers, tid, db):
    """Two simultaneous 'start' requests: the partial unique index is the backstop."""
    _journey(client, tourist_headers, tid)
    db.add(CheckIn(tourist_id=tid, kind="walk_home", destination_name="X", status="planned",
                   expected_return_at=utc_now() + timedelta(minutes=5)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_guardian_must_be_the_tourists_own_and_not_revoked(client, tourist_headers, tid, second_tourist):
    other, other_headers = second_tourist
    theirs = _guardian(client, other_headers, other.id, name="Stranger")
    assert _start(client, tourist_headers, tid, guardian_ids=[theirs["id"]]).status_code == 422
    assert _start(client, tourist_headers, tid, guardian_ids=[99999]).status_code == 422
    mine = _guardian(client, tourist_headers, tid)
    client.post(f"/api/tourists/{tid}/guardians/{mine['id']}/revoke", headers=tourist_headers)
    assert _start(client, tourist_headers, tid, guardian_ids=[mine["id"]]).status_code == 422


# ------------------------------------------------------------------ authorization
def test_all_tourist_endpoints_require_auth(client, tourist_headers, tid):
    j = _journey(client, tourist_headers, tid)
    base = f"/api/tourists/{tid}/walk-home"
    assert client.post(base, json={}).status_code == 401
    assert client.get(base + "/active").status_code == 401
    assert client.get(base).status_code == 401
    for tail in ("checkin", "location", "extend", "end", "cancel"):
        assert client.post(f"{base}/{j['id']}/{tail}", json={}).status_code == 401


def test_a_tourist_cannot_touch_anothers_journey(client, tourist_headers, tid, second_tourist):
    other, other_headers = second_tourist
    j = _journey(client, tourist_headers, tid)
    base = f"/api/tourists/{tid}/walk-home"
    assert client.get(base + "/active", headers=other_headers).status_code == 403
    assert client.post(f"{base}/{j['id']}/checkin", headers=other_headers).status_code == 403
    # Through their OWN path with someone else's session id: not found, not leaked.
    for tail, body in (("checkin", None), ("cancel", None), ("end", None),
                       ("location", {"lat": 1, "lng": 1}), ("extend", {"minutes": 5})):
        r = client.post(_url(other.id, j["id"], tail), headers=other_headers, json=body)
        assert r.status_code == 404, tail
    assert client.get(base + "/active", headers=tourist_headers).json()["status"] == "planned"


def test_admin_can_view_a_tourists_journey(client, tourist_headers, admin_headers, tid):
    _journey(client, tourist_headers, tid)
    assert client.get(f"/api/tourists/{tid}/walk-home/active", headers=admin_headers).status_code == 200


def test_active_and_history(client, tourist_headers, tid):
    base = f"/api/tourists/{tid}/walk-home"
    assert client.get(base + "/active", headers=tourist_headers).json() is None
    j = _journey(client, tourist_headers, tid)
    # A refresh / reopened app: same journey, same SERVER deadline.
    again = client.get(base + "/active", headers=tourist_headers).json()
    assert again["id"] == j["id"] and again["next_checkin_deadline"] == j["next_checkin_deadline"]
    client.post(f"{base}/{j['id']}/end", headers=tourist_headers)
    assert client.get(base + "/active", headers=tourist_headers).json() is None
    hist = client.get(base, headers=tourist_headers).json()
    assert [h["status"] for h in hist] == ["completed"] and hist[0]["ended_at"]


# ------------------------------------------------------------------ check-in
def test_checkin_before_the_deadline_moves_it_out(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    r = client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers)
    assert r.status_code == 200
    row = _row(db, j["id"])
    assert row.status == "planned" and row.checkin_count == 1 and row.last_checkin_at is not None
    assert row.expected_return_at > utc_now() + timedelta(minutes=9)
    assert tick_checkins(db)["missed"] == []
    assert _tz(r.json()["last_checkin_at"])


def test_checkin_deadline_is_capped_at_arrival_then_continues(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, duration_minutes=12, check_interval_minutes=10)
    row = _row(db, j["id"])
    row.arrive_by = utc_now() + timedelta(minutes=5)  # arrival is now closer than one interval
    db.commit()
    client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers)
    row = _row(db, j["id"])
    assert row.expected_return_at == row.arrive_by  # 10 min away would overshoot arrival
    # Once arrival time has passed, a check-in simply earns another interval.
    row.arrive_by = utc_now() - timedelta(minutes=1)
    db.commit()
    client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers)
    assert _row(db, j["id"]).expected_return_at > utc_now() + timedelta(minutes=9)


def test_simultaneous_checkins_are_both_safe(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    a = client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers)
    b = client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers)
    assert a.status_code == b.status_code == 200
    row = _row(db, j["id"])
    assert row.status == "planned" and row.checkin_count == 2


# ------------------------------------------------------------------ missed -> grace -> incident
def test_missed_checkin_raises_the_existing_alert_and_warns_the_guardian(client, tourist_headers, tid, db):
    g = _guardian(client, tourist_headers, tid)
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]], start_lat=26.14, start_lng=91.73)
    _overdue(db, j["id"])
    with patch("app.services.monitoring.notify_tourist_sync") as tourist_push:
        assert tick_checkins(db)["missed"] == [j["id"]]
    row = _row(db, j["id"])
    assert row.status == "missed" and row.missed_at is not None
    alert = db.query(Alert).filter(Alert.type == "missed_checkin").one()
    assert alert.severity == "high" and "Hostel Aurora" in alert.message
    assert tourist_push.called  # the tourist is told on their own channel
    assert _walk_incidents(db) == []  # grace period: no incident yet
    logs = db.query(NotificationLog).filter(NotificationLog.event_type == "walk_home").all()
    assert any(lg.recipient_type == "guardian" and lg.channel == "sms" for lg in logs)
    assert client.get(f"/api/tourists/{tid}/walk-home/active", headers=tourist_headers).json()["phase"] == "missed"


def test_missed_state_reports_the_grace_countdown(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, grace_minutes=5)
    _overdue(db, j["id"])
    tick_checkins(db)
    a = client.get(f"/api/tourists/{tid}/walk-home/active", headers=tourist_headers).json()
    assert a["phase"] == "missed" and 0 < a["seconds_to_escalation"] <= 5 * 60


def test_late_checkin_during_grace_resumes_the_journey(client, tourist_headers, tid, db):
    g = _guardian(client, tourist_headers, tid)
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]])
    _overdue(db, j["id"])
    tick_checkins(db)
    r = client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers)
    assert r.status_code == 200 and r.json()["phase"] == "on_track"
    assert _row(db, j["id"]).missed_at is None
    # The old miss was cleared. A NEW miss starts a fresh grace period (and the
    # guardian is told again: the de-dup key includes the check-in count).
    _overdue(db, j["id"])
    tick_checkins(db)
    assert _walk_incidents(db) == [] and _row(db, j["id"]).status == "missed"
    logs = db.query(NotificationLog).filter(NotificationLog.event_type == "walk_home").all()
    assert any("recovered" in lg.payload for lg in logs)
    missed_sms = [lg for lg in logs if '"walk_home:missed' in lg.payload and lg.channel == "sms"
                  and lg.status != "suppressed"]
    assert len(missed_sms) == 2


def test_grace_expiry_opens_exactly_one_critical_incident(client, tourist_headers, tid, db):
    make_unit(db)
    g = _guardian(client, tourist_headers, tid)
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]], grace_minutes=2,
                 start_lat=26.14, start_lng=91.73)
    client.post(_url(tid, j["id"], "location"), headers=tourist_headers,
                json={"lat": 26.141, "lng": 91.731, "accuracy_m": 8})
    _overdue(db, j["id"])
    tick_checkins(db)
    _overdue(db, j["id"], minutes_past=3)
    result = tick_checkins(db)
    assert result["escalated"] == [j["id"]]
    row = _row(db, j["id"])
    assert row.status == "escalated"
    incs = _walk_incidents(db)
    assert len(incs) == 1 and row.incident_id == incs[0].id
    inc = incs[0]
    assert inc.severity == settings.WALKHOME_ESCALATION_SEVERITY == "critical"
    assert inc.tourist_id == tid and inc.status == "detected"
    assert "Hostel Aurora" in inc.description and f"Journey #{j['id']}" in inc.description
    assert "26.14100" in inc.description and "updated" in inc.description  # last known GPS + its age
    assert (inc.lat, inc.lng) == (26.141, 91.731)
    assert inc.escalation_deadline is not None  # the existing unattended-incident clock is running
    # Visible on the existing admin incidents page, through the existing API.
    # (the admin_headers fixture is requested lazily below)


def test_escalation_is_idempotent_and_visible_to_admin(client, tourist_headers, admin_headers, tid, db):
    j = _journey(client, tourist_headers, tid, grace_minutes=1)
    _overdue(db, j["id"])
    tick_checkins(db)
    _overdue(db, j["id"], minutes_past=2)
    tick_checkins(db)
    for _ in range(3):
        assert tick_checkins(db)["escalated"] == []
    assert len(_walk_incidents(db)) == 1
    listed = client.get("/api/incidents", headers=admin_headers).json()
    items = listed if isinstance(listed, list) else listed.get("items", listed)
    assert any(i["type"] == "missed_checkin" and i["severity"] == "critical" for i in items)


def test_incident_is_opened_without_any_location(client, tourist_headers, tid, db):
    t = db.get(__import__("app.models.tourist", fromlist=["Tourist"]).Tourist, tid)
    t.last_lat = t.last_lng = None
    db.commit()
    j = _journey(client, tourist_headers, tid, dest_lat=None, dest_lng=None, grace_minutes=1)
    _overdue(db, j["id"])
    tick_checkins(db)
    _overdue(db, j["id"], minutes_past=2)
    tick_checkins(db)
    inc = _walk_incidents(db)[0]
    assert inc.lat is None and "Location unavailable" in inc.description


def test_escalated_journey_cannot_be_checked_in_but_can_be_ended(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, grace_minutes=1)
    _overdue(db, j["id"])
    tick_checkins(db)
    _overdue(db, j["id"], minutes_past=2)
    tick_checkins(db)
    r = client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers)
    assert r.status_code == 409 and "End the journey" in r.json()["detail"]
    ended = client.post(_url(tid, j["id"], "end"), headers=tourist_headers)
    assert ended.status_code == 200 and ended.json()["status"] == "completed"
    inc = db.get(Incident, _row(db, j["id"]).incident_id)
    db.refresh(inc)
    assert inc.status == "detected"  # responders, not the tourist, close an incident
    assert any("reports being safe" in e.note for e in inc.events)


def test_terminal_journeys_are_ignored_by_the_tick(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    client.post(_url(tid, j["id"], "cancel"), headers=tourist_headers)
    _overdue(db, j["id"], minutes_past=999)
    assert tick_checkins(db)["missed"] == [] and _walk_incidents(db) == []


# ------------------------------------------------------------------ races
def _race(db, hook_sid, competing, *, when):
    """Run `competing(other_session)` the instant before the tick's claim UPDATE
    -- i.e. while the tick holds a stale view. The in-memory test engine shares
    one connection, so this is a faithful interleaving."""
    other = sessionmaker(bind=db.get_bind())()
    fired = []

    @event.listens_for(db, "do_orm_execute")
    def _hook(state):
        if state.is_update and not fired and "checkins" in str(state.statement).lower() \
                and when in str(state.statement).lower():
            fired.append(1)
            competing(other)

    return other, fired, _hook


def test_checkin_racing_the_missed_claim_wins(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    _overdue(db, j["id"])
    other, fired, hook = _race(db, j["id"], lambda s: svc.check_in(s, tid, j["id"]), when="status")
    try:
        result = tick_checkins(db)
    finally:
        event.remove(db, "do_orm_execute", hook)
        other.close()
    assert fired
    row = _row(db, j["id"])
    assert result["missed"] == [] and row.status == "planned" and row.checkin_count == 1
    assert db.query(Alert).filter(Alert.type == "missed_checkin").count() == 0


def test_late_checkin_racing_the_escalation_claim_wins(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, grace_minutes=1)
    _overdue(db, j["id"])
    tick_checkins(db)
    _overdue(db, j["id"], minutes_past=2)
    other, fired, hook = _race(db, j["id"], lambda s: svc.check_in(s, tid, j["id"]), when="status")
    try:
        result = tick_checkins(db)
    finally:
        event.remove(db, "do_orm_execute", hook)
        other.close()
    assert fired and result["escalated"] == []
    assert _walk_incidents(db) == [] and _row(db, j["id"]).status == "planned"


def test_end_racing_the_escalation_leaves_no_incident(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, grace_minutes=1)
    _overdue(db, j["id"])
    tick_checkins(db)
    _overdue(db, j["id"], minutes_past=2)
    other, fired, hook = _race(db, j["id"], lambda s: svc.end(s, tid, j["id"], svc.COMPLETED), when="status")
    try:
        result = tick_checkins(db)
    finally:
        event.remove(db, "do_orm_execute", hook)
        other.close()
    assert fired and result["escalated"] == []
    assert _walk_incidents(db) == [] and _row(db, j["id"]).status == "completed"


def test_failed_incident_rolls_the_claim_back_and_the_next_tick_retries(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, grace_minutes=1)
    _overdue(db, j["id"])
    tick_checkins(db)
    _overdue(db, j["id"], minutes_past=2)
    with patch("app.services.monitoring._open_incident", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            tick_checkins(db)
    db.rollback()
    assert _row(db, j["id"]).status == "missed" and _walk_incidents(db) == []
    assert tick_checkins(db)["escalated"] == [j["id"]]
    assert len(_walk_incidents(db)) == 1


# ------------------------------------------------------------------ end / cancel
def test_end_completes_stops_sharing_and_tells_guardians(client, tourist_headers, tid, db):
    g = _guardian(client, tourist_headers, tid)
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]])
    r = client.post(_url(tid, j["id"], "end"), headers=tourist_headers)
    assert r.status_code == 200 and r.json()["status"] == "completed" and r.json()["active"] is False
    assert _row(db, j["id"]).ended_at is not None
    # Location sharing is over: the endpoint refuses, which stops the app's timer.
    loc = client.post(_url(tid, j["id"], "location"), headers=tourist_headers, json={"lat": 1, "lng": 1})
    assert loc.status_code == 409
    logs = db.query(NotificationLog).filter(NotificationLog.event_type == "walk_home").all()
    assert any("completed" in lg.payload and lg.recipient_type == "guardian" for lg in logs)
    # idempotent for the same outcome, conflicting for the other one
    assert client.post(_url(tid, j["id"], "end"), headers=tourist_headers).status_code == 200
    assert client.post(_url(tid, j["id"], "cancel"), headers=tourist_headers).status_code == 409


def test_cancel_revokes_guardian_access_and_records_it(client, tourist_headers, tid, db):
    g = _guardian(client, tourist_headers, tid)
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]])
    assert client.get(f"/api/guardian/{g['token']}/journey").json()["journey"]["id"] == j["id"]
    r = client.post(_url(tid, j["id"], "cancel"), headers=tourist_headers)
    assert r.json()["status"] == "cancelled"
    assert _row(db, j["id"]).ended_at is not None
    assert client.get(f"/api/guardian/{g['token']}/journey").json()["journey"] is None
    assert client.post(_url(tid, j["id"], "checkin"), headers=tourist_headers).status_code == 409


def test_a_new_journey_notifies_again(client, tourist_headers, tid, db):
    """The de-duplication key must not swallow the next journey's messages."""
    g = _guardian(client, tourist_headers, tid)
    for _ in range(2):
        j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]])
        client.post(_url(tid, j["id"], "end"), headers=tourist_headers)
    started = [lg for lg in db.query(NotificationLog).filter(NotificationLog.event_type == "walk_home")
               if '"walk_home:started' in lg.payload and lg.channel == "sms" and lg.status != "suppressed"]
    assert len(started) == 2


# ------------------------------------------------------------------ location
def _loc(client, headers, tid, sid, **over):
    body = {"lat": 26.141, "lng": 91.731, "accuracy_m": 10}
    body.update(over)
    return client.post(_url(tid, sid, "location"), headers=headers, json=body)


def test_location_is_stored_and_returned_with_its_age(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    r = _loc(client, tourist_headers, tid, j["id"], fix_age_seconds=5)
    assert r.status_code == 200 and r.json()["accepted"] is True
    jr = r.json()["journey"]
    assert jr["has_location"] and (jr["last_lat"], jr["last_lng"]) == (26.141, 91.731)
    assert 4 <= jr["location_age_seconds"] < 30 and jr["location_stale"] is False
    assert _tz(jr["last_location_at"])


def test_no_location_means_none_not_a_made_up_one(client, tourist_headers, tid):
    j = _journey(client, tourist_headers, tid)  # no start fix, no updates
    assert j["has_location"] is False and j["last_lat"] is None and j["location_age_seconds"] is None


def test_duplicate_and_out_of_order_fixes_are_ignored(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    assert _loc(client, tourist_headers, tid, j["id"], lat=10.0, lng=10.0).json()["accepted"] is True
    # an OLDER fix delivered late (retry / reorder) must not overwrite the newer one
    late = _loc(client, tourist_headers, tid, j["id"], lat=20.0, lng=20.0, fix_age_seconds=60)
    assert late.status_code == 200 and late.json() == {**late.json(), "accepted": False, "reason": "duplicate"}
    row = _row(db, j["id"])
    assert (row.last_lat, row.last_lng) == (10.0, 10.0)


def test_too_old_fix_is_rejected(client, tourist_headers, tid):
    j = _journey(client, tourist_headers, tid)
    r = _loc(client, tourist_headers, tid, j["id"], fix_age_seconds=settings.WALKHOME_MAX_FIX_AGE_SECONDS + 1)
    assert r.json()["accepted"] is False and r.json()["reason"] == "too_old"


def test_a_stale_fix_is_flagged_not_shown_as_live(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    _loc(client, tourist_headers, tid, j["id"])
    c = db.get(CheckIn, j["id"])
    c.last_loc_at = utc_now() - timedelta(seconds=settings.WALKHOME_LOCATION_STALE_SECONDS + 30)
    db.commit()
    a = client.get(f"/api/tourists/{tid}/walk-home/active", headers=tourist_headers).json()
    assert a["has_location"] and a["location_stale"] is True


@pytest.mark.parametrize("bad", [{"lat": 91, "lng": 0}, {"lat": 0, "lng": 181}, {"lat": "x", "lng": 0},
                                 {"lat": 1}, {"lat": 1, "lng": 1, "accuracy_m": -1},
                                 {"lat": 1, "lng": 1, "fix_age_seconds": -5}, {"lat": 1, "lng": 1, "extra": 1}])
def test_location_validation(client, tourist_headers, tid, bad):
    j = _journey(client, tourist_headers, tid)
    assert client.post(_url(tid, j["id"], "location"), headers=tourist_headers, json=bad).status_code == 422


# ------------------------------------------------------------------ guardian scoping
def test_guardian_sees_only_what_was_shared_with_them(client, tourist_headers, tid, db):
    chosen = _guardian(client, tourist_headers, tid, name="Chosen")
    left_out = _guardian(client, tourist_headers, tid, name="LeftOut", contact="+919000000002")
    j = _journey(client, tourist_headers, tid, guardian_ids=[chosen["id"]], start_lat=26.14, start_lng=91.73)

    seen = client.get(f"/api/guardian/{chosen['token']}/journey").json()
    jr = seen["journey"]
    assert jr["id"] == j["id"] and jr["destination_name"] == "Hostel Aurora"
    assert jr["tourist_name"] == "Owner Tourist" and jr["has_location"] and jr["phase"] == "on_track"
    # Nothing beyond the journey: no incident id, tourist id, other guardians, phone/document.
    flat = str(seen)
    for secret in ("incident_id", "tourist_id", "guardians", "guardian_ids", "document", "phone", "LeftOut"):
        assert secret not in flat

    other = client.get(f"/api/guardian/{left_out['token']}/journey").json()
    assert other["journey"] is None  # a real guardian of this tourist, but not for this journey

    assert client.get("/api/guardian/not-a-real-token/journey").status_code == 404


def test_revoking_a_guardian_ends_their_access_immediately(client, tourist_headers, tid):
    g = _guardian(client, tourist_headers, tid)
    _journey(client, tourist_headers, tid, guardian_ids=[g["id"]])
    assert client.get(f"/api/guardian/{g['token']}/journey").json()["journey"]
    client.post(f"/api/tourists/{tid}/guardians/{g['id']}/revoke", headers=tourist_headers)
    assert client.get(f"/api/guardian/{g['token']}/journey").status_code == 404


def test_guardian_view_shows_alert_state_and_stale_location(client, tourist_headers, tid, db):
    g = _guardian(client, tourist_headers, tid)
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]], start_lat=26.14, start_lng=91.73)
    _overdue(db, j["id"])
    tick_checkins(db)
    jr = client.get(f"/api/guardian/{g['token']}/journey").json()["journey"]
    assert jr["phase"] == "missed" and jr["alert"] == "missed" and jr["seconds_to_escalation"] > 0


def test_tourist_sees_which_guardians_have_access(client, tourist_headers, tid):
    a = _guardian(client, tourist_headers, tid, name="A")
    b = _guardian(client, tourist_headers, tid, name="B", contact="+919000000003")
    j = _journey(client, tourist_headers, tid, guardian_ids=[a["id"], b["id"]])
    client.post(f"/api/tourists/{tid}/guardians/{b['id']}/revoke", headers=tourist_headers)
    now = client.get(f"/api/tourists/{tid}/walk-home/active", headers=tourist_headers).json()
    assert {g["name"]: g["has_access"] for g in now["guardians"]} == {"A": True, "B": False}
    assert j["id"] == now["id"]


# ------------------------------------------------------------------ extend
def test_extend_adds_time_within_the_limits(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid, duration_minutes=10, check_interval_minutes=10)
    before = _row(db, j["id"])
    arrive, deadline = before.arrive_by, before.expected_return_at
    r = client.post(_url(tid, j["id"], "extend"), headers=tourist_headers, json={"minutes": 15})
    assert r.status_code == 200 and r.json()["extensions_left"] == settings.WALKHOME_MAX_EXTENSIONS - 1
    after = _row(db, j["id"])
    assert after.arrive_by - arrive == timedelta(minutes=15)
    assert after.expected_return_at - deadline == timedelta(minutes=15)  # the final check-in moves with arrival


def test_extend_limits(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    url = _url(tid, j["id"], "extend")
    assert client.post(url, headers=tourist_headers,
                       json={"minutes": settings.WALKHOME_MAX_EXTENSION_MINUTES + 1}).status_code == 422
    assert client.post(url, headers=tourist_headers, json={"minutes": 0}).status_code == 422
    for _ in range(settings.WALKHOME_MAX_EXTENSIONS):
        assert client.post(url, headers=tourist_headers, json={"minutes": 1}).status_code == 200
    assert client.post(url, headers=tourist_headers, json={"minutes": 1}).status_code == 409


def test_extend_cannot_exceed_the_maximum_duration(client, tourist_headers, tid):
    j = _journey(client, tourist_headers, tid, duration_minutes=settings.WALKHOME_MAX_DURATION_MINUTES)
    assert client.post(_url(tid, j["id"], "extend"), headers=tourist_headers,
                       json={"minutes": 5}).status_code == 422


def test_extend_requires_a_check_in_first_when_missed(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    _overdue(db, j["id"])
    tick_checkins(db)
    r = client.post(_url(tid, j["id"], "extend"), headers=tourist_headers, json={"minutes": 5})
    assert r.status_code == 409 and "Check in first" in r.json()["detail"]


# ------------------------------------------------------------------ existing features untouched
def test_outings_still_work_and_do_not_mix_with_journeys(client, tourist_headers, tid, db):
    j = _journey(client, tourist_headers, tid)
    r = client.post(f"/api/tourists/{tid}/checkins", headers=tourist_headers, json={
        "destination_name": "Riverside trek",
        "expected_return_at": (utc_now() + timedelta(hours=3)).isoformat()})
    assert r.status_code == 201 and r.json()["status"] == "planned"
    listed = client.get(f"/api/tourists/{tid}/checkins", headers=tourist_headers).json()
    assert [c["destination_name"] for c in listed] == ["Riverside trek"]  # the walk is not in the outing list
    # the outing endpoint must not be usable to "check in" a journey and skip its rules
    assert client.post(f"/api/tourists/{tid}/checkins/{j['id']}/checkin",
                       headers=tourist_headers).status_code == 404
    assert client.post(f"/api/tourists/{tid}/checkins/{r.json()['id']}/checkin",
                       headers=tourist_headers).json()["status"] == "checked_in"


def test_outing_escalation_keeps_its_own_grace_and_severity(client, db):
    t = make_tourist(db)
    c = CheckIn(tourist_id=t.id, destination_name="Hillside", status="missed",
                expected_return_at=utc_now() - timedelta(minutes=settings.CHECKIN_GRACE_MINUTES - 1))
    db.add(c)
    db.commit()
    assert tick_checkins(db)["escalated"] == []  # still inside the 30 min outing grace
    c.expected_return_at = utc_now() - timedelta(minutes=settings.CHECKIN_GRACE_MINUTES + 1)
    db.commit()
    assert tick_checkins(db)["escalated"] == [c.id]
    inc = _walk_incidents(db)[0]
    assert inc.severity == "high" and c.incident_id is None


def test_sos_still_works_during_a_journey(client, tourist_headers, tid, db):
    make_unit(db)
    j = _journey(client, tourist_headers, tid)
    r = client.post(f"/api/tourists/{tid}/sos", headers=tourist_headers,
                    json={"lat": 26.14, "lng": 91.73, "message": "help"})
    assert r.status_code == 200
    assert db.query(Incident).filter(Incident.type == "sos").count() == 1
    assert client.get(f"/api/tourists/{tid}/walk-home/active", headers=tourist_headers).json()["id"] == j["id"]


def test_feature_can_be_switched_off(client, tourist_headers, tid):
    with patch.object(settings, "WALKHOME_ENABLED", False):
        assert _start(client, tourist_headers, tid).status_code == 404
        assert client.get(f"/api/tourists/{tid}/walk-home/active", headers=tourist_headers).status_code == 404


# ------------------------------------------------------------------ scheduler wiring
def test_checkin_job_lock_expires_before_the_next_tick():
    """Job locks live until their TTL; the shared 120 s TTL would throttle the
    30 s check-in tick to ~2 min and delay every missed-check-in alert."""
    from app import main
    from app.services.checkin import tick_checkins as fn

    with patch("app.main.run_locked") as locked:
        main._checkin_tick_job()
    _, job_id, ttl, called = locked.call_args.args
    assert job_id == "checkin_tick" and called is fn
    assert 1 <= ttl < settings.CHECKIN_TICK_SECONDS and ttl < settings.JOB_LOCK_TTL_SECONDS


# ------------------------------------------------------------------ live push to guardians
def test_live_pushes_reach_only_the_selected_guardian_and_end_without_location(client, tourist_headers, tid, db):
    chosen = _guardian(client, tourist_headers, tid, name="Chosen")
    other = _guardian(client, tourist_headers, tid, name="Other", contact="+919000000009")
    with patch("app.services.walk_home.notify_guardian_sync") as push:
        j = _journey(client, tourist_headers, tid, guardian_ids=[chosen["id"]])
        push.reset_mock()
        _loc(client, tourist_headers, tid, j["id"])
        targets = {c.args[0] for c in push.call_args_list}
        assert targets == {chosen["id"]} and other["id"] not in targets  # never the unselected guardian
        sent = push.call_args_list[-1].args[1]
        assert sent["kind"] == "location" and sent["journey"]["has_location"] is True
        assert "incident_id" not in sent["journey"] and "tourist_id" not in sent["journey"]

        push.reset_mock()
        client.post(_url(tid, j["id"], "end"), headers=tourist_headers)
        final = [c.args for c in push.call_args_list]
        assert final and all(gid == chosen["id"] for gid, _ in final)
        assert all(m["kind"] == "ended" and m["journey"]["active"] is False for _, m in final)
        assert all("last_lat" not in m["journey"] for _, m in final)  # no position after the end


def test_no_guardian_is_considered_to_have_access_once_the_journey_ends(client, tourist_headers, tid, db):
    g = _guardian(client, tourist_headers, tid)
    j = _journey(client, tourist_headers, tid, guardian_ids=[g["id"]])
    assert [x.id for x in svc.shared_guardians(db, _row(db, j["id"]))] == [g["id"]]
    client.post(_url(tid, j["id"], "cancel"), headers=tourist_headers)
    assert svc.shared_guardians(db, _row(db, j["id"])) == []
