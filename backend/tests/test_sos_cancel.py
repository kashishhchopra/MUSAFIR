"""A tourist closing their own SOS after three confirmations (POST /tourists/{id}/sos/cancel)."""
from app.models.incident import Incident, IncidentEvent
from app.models.tourist import Tourist
from app.services.monitoring import trigger_sos
from tests.conftest import make_tourist


def _tourist(db, tourist_user):
    return db.get(Tourist, tourist_user.tourist_id)


def _cancel(client, headers, tid, confirmations=3):
    return client.post(f"/api/tourists/{tid}/sos/cancel", json={"confirmations": confirmations}, headers=headers)


def test_tourist_can_close_their_own_sos(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    sent = trigger_sos(db, t, 26.14, 91.73, "help")
    r = _cancel(client, tourist_headers, t.id)
    assert r.status_code == 200, r.text
    assert r.json() == {"incident_id": sent["incident_id"], "status": "resolved"}

    inc = db.get(Incident, sent["incident_id"])
    db.refresh(inc)
    assert inc.status == "resolved" and inc.resolved_at is not None
    assert inc.live_tracking_active is False
    db.refresh(t)
    assert t.status == "active"
    notes = [e.note for e in db.query(IncidentEvent).filter(IncidentEvent.incident_id == inc.id)]
    assert any("Closed by the tourist" in (n or "") for n in notes)


def test_it_needs_all_three_confirmations(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    sent = trigger_sos(db, t, 26.14, 91.73, "help")
    for bad in (0, 1, 2, 4):
        assert _cancel(client, tourist_headers, t.id, bad).status_code == 422
    assert client.post(f"/api/tourists/{t.id}/sos/cancel", json={}, headers=tourist_headers).status_code == 422
    assert db.get(Incident, sent["incident_id"]).status != "resolved"


def test_nothing_to_close_is_a_404(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    assert _cancel(client, tourist_headers, t.id).status_code == 404


def test_closing_twice_is_harmless_and_reports_already_closed(client, db, tourist_user, tourist_headers):
    """A lost response or a double-send must not show the tourist "still active"."""
    t = _tourist(db, tourist_user)
    sent = trigger_sos(db, t, 26.14, 91.73, "help")
    assert _cancel(client, tourist_headers, t.id).status_code == 200
    again = _cancel(client, tourist_headers, t.id)
    assert again.status_code == 200
    assert again.json() == {"incident_id": sent["incident_id"], "status": "resolved", "already_closed": True}


def test_an_old_closed_sos_does_not_count_as_just_closed(client, db, tourist_user, tourist_headers):
    from datetime import timedelta

    from app.core.time import utc_now

    t = _tourist(db, tourist_user)
    sent = trigger_sos(db, t, 26.14, 91.73, "help")
    assert _cancel(client, tourist_headers, t.id).status_code == 200
    inc = db.get(Incident, sent["incident_id"])
    inc.resolved_at = utc_now() - timedelta(minutes=30)
    db.commit()
    assert _cancel(client, tourist_headers, t.id).status_code == 404


def test_self_close_is_not_counted_as_a_human_acknowledgement(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    sent = trigger_sos(db, t, 26.14, 91.73, "help")
    assert _cancel(client, tourist_headers, t.id).status_code == 200
    inc = db.get(Incident, sent["incident_id"])
    db.refresh(inc)
    assert inc.acknowledged_at is None


def test_closing_leaves_the_tourist_flagged_while_a_silent_sos_is_open(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    trigger_sos(db, t, 26.14, 91.73, "duress", silent=True)
    trigger_sos(db, t, 26.14, 91.73, "help")
    assert _cancel(client, tourist_headers, t.id).status_code == 200
    db.refresh(t)
    assert t.status == "sos"


def test_all_open_visible_sos_cases_are_closed_together(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    a = trigger_sos(db, t, 26.14, 91.73, "help")
    inc_a = db.get(Incident, a["incident_id"])
    inc_a.detected_at = inc_a.detected_at.replace(year=2020)  # outside the merge window -> a 2nd case
    db.commit()
    b = trigger_sos(db, t, 26.14, 91.73, "help again")
    assert a["incident_id"] != b["incident_id"]
    assert _cancel(client, tourist_headers, t.id).status_code == 200
    assert {db.get(Incident, i["incident_id"]).status for i in (a, b)} == {"resolved"}


def test_the_control_room_is_told_when_a_tourist_closes_their_sos(client, db, tourist_user, tourist_headers, monkeypatch):
    from app.services import notifications

    t = _tourist(db, tourist_user)
    trigger_sos(db, t, 26.14, 91.73, "help")
    calls = []
    monkeypatch.setattr(notifications, "_push_fanout", lambda *a, **k: calls.append((k["rtype"], k["event_name"])))
    assert _cancel(client, tourist_headers, t.id).status_code == 200
    assert ("admin", "sos_closed_by_tourist") in calls


def test_a_silent_duress_sos_cannot_be_closed_by_the_tourist(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    sent = trigger_sos(db, t, 26.14, 91.73, "duress", silent=True)
    r = _cancel(client, tourist_headers, t.id)
    assert r.status_code == 409
    assert db.get(Incident, sent["incident_id"]).status != "resolved"


def test_closing_the_visible_sos_leaves_a_silent_one_open(client, db, tourist_user, tourist_headers):
    t = _tourist(db, tourist_user)
    silent = trigger_sos(db, t, 26.14, 91.73, "duress", silent=True)
    visible = trigger_sos(db, t, 26.14, 91.73, "help")
    assert _cancel(client, tourist_headers, t.id).status_code == 200
    assert db.get(Incident, visible["incident_id"]).status == "resolved"
    assert db.get(Incident, silent["incident_id"]).status != "resolved"


def test_one_tourist_cannot_close_anothers_sos(client, db, tourist_user, tourist_headers):
    other = make_tourist(db, name="Other", doc="XXXX-XXXX-0042")
    trigger_sos(db, other, 26.14, 91.73, "help")
    assert _cancel(client, tourist_headers, other.id).status_code == 403
    assert db.query(Incident).filter(Incident.tourist_id == other.id, Incident.status == "resolved").count() == 0


def test_login_is_required(client, db, tourist_user):
    t = _tourist(db, tourist_user)
    assert client.post(f"/api/tourists/{t.id}/sos/cancel", json={"confirmations": 3}).status_code == 401
