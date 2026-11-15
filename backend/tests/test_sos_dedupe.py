"""Repeat SOS taps must not 500 or fan out again (services/monitoring.trigger_sos)."""
import threading
from datetime import timedelta

from app.core.time import utc_now
from app.models.incident import Incident
from app.models.tourist import Tourist
from app.services.monitoring import SOS_DEDUPE_SECONDS, trigger_sos


def _tourist(db, tourist_user):
    return db.get(Tourist, tourist_user.tourist_id)


def _sos_count(db, tid):
    return db.query(Incident).filter(Incident.tourist_id == tid, Incident.type == "sos").count()


def test_repeat_tap_returns_same_incident(db, tourist_user):
    t = _tourist(db, tourist_user)
    first = trigger_sos(db, t, 26.14, 91.73, "help")
    second = trigger_sos(db, t, 26.14, 91.73, "help")
    assert second["incident_id"] == first["incident_id"]
    assert second["status"] == first["status"]
    assert _sos_count(db, t.id) == 1


def test_silent_sos_is_not_merged_with_visible_one(db, tourist_user):
    t = _tourist(db, tourist_user)
    visible = trigger_sos(db, t, 26.14, 91.73, "help")
    silent = trigger_sos(db, t, 26.14, 91.73, "duress", silent=True)
    assert silent["incident_id"] != visible["incident_id"]


def test_resolved_or_old_sos_does_not_block_a_new_one(db, tourist_user):
    t = _tourist(db, tourist_user)
    first = trigger_sos(db, t, 26.14, 91.73, "help")
    inc = db.get(Incident, first["incident_id"])
    inc.status = "resolved"
    db.commit()
    assert trigger_sos(db, t, 26.14, 91.73, "again")["incident_id"] != first["incident_id"]

    older = db.get(Incident, trigger_sos(db, t, 26.14, 91.73, "x")["incident_id"])
    older.detected_at = utc_now() - timedelta(seconds=SOS_DEDUPE_SECONDS + 5)
    db.commit()
    assert trigger_sos(db, t, 26.14, 91.73, "later")["incident_id"] != older.id


def test_concurrent_taps_open_one_incident(client, tourist_user, tourist_headers, db):
    tid = tourist_user.tourist_id
    codes = []

    def tap():
        r = client.post(f"/api/tourists/{tid}/sos", headers=tourist_headers,
                        json={"lat": 26.14, "lng": 91.73, "message": "help"})
        codes.append(r.status_code)

    threads = [threading.Thread(target=tap) for _ in range(4)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    assert codes == [200] * 4
    db.expire_all()
    assert _sos_count(db, tid) == 1
