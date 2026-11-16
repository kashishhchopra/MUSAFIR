"""DPDP-oriented privacy management: consent, retention, location privacy,
export, account deletion and grievances (services/privacy_*.py, api/privacy.py)."""
import io
import json
import zipfile
from datetime import timedelta

import pytest

from app.core.config import settings
from app.core.security import hash_password
from app.core.time import utc_now
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.efir import EFIR
from app.models.emergency_location import EmergencyLocationPing
from app.models.guardian import TripGuardian
from app.models.incident import Incident
from app.models.medical_profile import MedicalProfile
from app.models.notification import NotificationLog, PushSubscription
from app.models.privacy import (
    AccountDeletionRequest,
    ConsentRecord,
    DataExportRequest,
    DeletionJob,
    RetentionPolicy,
)
from app.models.tourist import LocationPing, Tourist
from app.models.tourist_id import TouristIdScan, TouristIdToken
from app.models.user import User
from app.services import privacy_consent, privacy_deletion, privacy_export, privacy_retention
from tests.conftest import _token, make_tourist

PW = "touristpass1"


@pytest.fixture(autouse=True)
def _export_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PRIVACY_EXPORT_DIR", str(tmp_path / "exports"))


def make_login(db, client, name="Second Tourist", email="second@test.com", doc="XXXX-XXXX-0002"):
    t = make_tourist(db, name=name, doc=doc)
    db.add(User(email=email, full_name=name, hashed_password=hash_password(PW),
                role="tourist", tourist_id=t.id))
    db.commit()
    return t, _token(client, email, PW)


def ping(db, tid, ts=None, lat=26.1, lng=91.7):
    p = LocationPing(tourist_id=tid, lat=lat, lng=lng, speed_kmh=1.0, timestamp=ts or utc_now())
    db.add(p)
    db.commit()
    return p


def sos(db, tid, status="detected", resolved=False):
    inc = Incident(tourist_id=tid, type="sos", severity="critical", status=status,
                   description="private description", lat=26.1234, lng=91.7345,
                   resolved_at=utc_now() if resolved else None)
    db.add(inc)
    db.commit()
    return inc


# ============================================================== consent ====
def test_notice_is_public_and_purposes_are_configured(client):
    r = client.get("/api/privacy/notice")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == settings.PRIVACY_NOTICE_VERSION
    keys = {p["key"] for p in body["purposes"]}
    assert {"location_tracking", "guardian_sharing", "notifications"} <= keys
    assert "not a certificate" in body["legal_note"].lower()


def test_nothing_is_preselected(client, tourist_headers, tourist_user):
    r = client.get(f"/api/tourists/{tourist_user.tourist_id}/consents", headers=tourist_headers)
    consent_rows = [c for c in r.json() if c["kind"] == "consent"]
    assert consent_rows and all(c["status"] == "not_recorded" for c in consent_rows)


def test_grant_records_purpose_version_and_timestamp(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    r = client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers,
                    json={"purpose": "guardian_sharing"})
    assert r.status_code == 201
    body = r.json()
    assert body["purpose"] == "guardian_sharing" and body["status"] == "granted"
    assert body["notice_version"] == settings.PRIVACY_NOTICE_VERSION
    assert body["consent_version"] == 1 and body["consent_id"].startswith("CNS-")
    assert body["consented_at"] and body["withdrawn_at"] is None
    assert "live_location" in body["data_categories"]


def test_grant_is_idempotent(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    for _ in range(2):
        client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers,
                    json={"purpose": "notifications"})
    assert db.query(ConsentRecord).filter_by(purpose="notifications").count() == 1


def test_withdraw_stamps_record_and_keeps_history(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers, json={"purpose": "guardian_sharing"})
    w = client.post(f"/api/tourists/{tid}/consents/guardian_sharing/withdraw", headers=tourist_headers)
    assert w.status_code == 200 and w.json()["status"] == "withdrawn" and w.json()["withdrawn_at"]
    client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers, json={"purpose": "guardian_sharing"})
    hist = client.get(f"/api/tourists/{tid}/consents/history", headers=tourist_headers).json()
    mine = [h for h in hist if h["purpose"] == "guardian_sharing"]
    assert [h["status"] for h in mine] == ["granted", "withdrawn"]       # newest first
    assert [h["consent_version"] for h in mine] == [2, 1]


def test_withdrawing_location_consent_stops_collection(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers, json={"purpose": "location_tracking"})
    ok = client.post(f"/api/tourists/{tid}/location", headers=tourist_headers,
                     json={"lat": 26.14, "lng": 91.73, "speed_kmh": 3})
    assert ok.status_code == 200
    w = client.post(f"/api/tourists/{tid}/consents/location_tracking/withdraw", headers=tourist_headers)
    assert "location_collection_stopped" in w.json()["effects"]
    db.expire_all()
    assert db.get(Tourist, tid).tracking_enabled is False
    before = db.query(LocationPing).filter_by(tourist_id=tid).count()
    blocked = client.post(f"/api/tourists/{tid}/location", headers=tourist_headers,
                          json={"lat": 26.15, "lng": 91.74, "speed_kmh": 3})
    assert blocked.status_code == 409
    assert db.query(LocationPing).filter_by(tourist_id=tid).count() == before
    # withdrawal is not erasure: the history is still there
    assert before >= 1
    client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers, json={"purpose": "location_tracking"})
    assert client.post(f"/api/tourists/{tid}/location", headers=tourist_headers,
                       json={"lat": 26.15, "lng": 91.74, "speed_kmh": 3}).status_code == 200


def test_withdrawing_location_consent_does_not_disable_sos(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    client.post(f"/api/tourists/{tid}/consents/location_tracking/withdraw", headers=tourist_headers)
    r = client.post(f"/api/tourists/{tid}/sos", headers=tourist_headers,
                    json={"lat": 26.14, "lng": 91.73, "message": "help"})
    assert r.status_code in (200, 201)


def test_existing_tracking_switch_keeps_ledger_truthful(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    client.post(f"/api/tourists/{tid}/tracking?enabled=false", headers=tourist_headers)
    hist = client.get(f"/api/tourists/{tid}/consents/history", headers=tourist_headers).json()
    assert hist[0]["purpose"] == "location_tracking" and hist[0]["status"] == "withdrawn"
    assert hist[0]["source"] == "tracking_toggle"


def test_withdrawing_guardian_sharing_revokes_links_and_blocks_new_ones(
        client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    g = client.post(f"/api/tourists/{tid}/guardians", headers=tourist_headers,
                    json={"guardian_name": "Mum", "guardian_contact": "mum@x.com"})
    assert g.status_code == 201
    w = client.post(f"/api/tourists/{tid}/consents/guardian_sharing/withdraw", headers=tourist_headers)
    assert any(e.startswith("guardian_links_revoked") for e in w.json()["effects"])
    assert client.get(f"/api/guardian/{g.json()['token']}").status_code in (404, 410)
    again = client.post(f"/api/tourists/{tid}/guardians", headers=tourist_headers,
                        json={"guardian_name": "Dad", "guardian_contact": "d@x.com"})
    assert again.status_code == 403


def test_withdrawing_notifications_deactivates_push(client, tourist_headers, tourist_user, db):
    db.add(PushSubscription(endpoint="https://push.example/1", p256dh="k", auth="a",
                            user_id=tourist_user.id, role="tourist"))
    db.commit()
    w = client.post(f"/api/tourists/{tourist_user.tourist_id}/consents/notifications/withdraw",
                    headers=tourist_headers)
    assert any(e.startswith("push_subscriptions_deactivated") for e in w.json()["effects"])
    db.expire_all()
    assert db.query(PushSubscription).filter_by(user_id=tourist_user.id).one().active is False


def test_non_consent_purposes_cannot_be_withdrawn_here(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    assert client.post(f"/api/tourists/{tid}/consents/emergency_sos/withdraw",
                       headers=tourist_headers).status_code == 400
    assert client.post(f"/api/tourists/{tid}/consents/nope/withdraw",
                       headers=tourist_headers).status_code == 400
    assert client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers,
                       json={"purpose": "made_up"}).status_code == 400


def test_consent_cannot_be_modified_by_others(client, tourist_headers, tourist_user, admin_headers, db):
    other, other_h = make_login(db, client)
    tid = tourist_user.tourist_id
    for h in (other_h, admin_headers):
        assert client.post(f"/api/tourists/{tid}/consents", headers=h,
                           json={"purpose": "notifications"}).status_code == 403
        assert client.get(f"/api/tourists/{tid}/consents/history", headers=h).status_code == 403
    assert client.get(f"/api/tourists/{tid}/consents").status_code == 401


def test_registration_records_only_ticked_boxes(client, db):
    body = {
        "full_name": "Priya Sharma", "nationality": "Indian", "document_type": "aadhaar",
        "document_number": "123456789999", "phone": "9876543210",
        "photo": "data:image/png;base64,iVBORw0KGgo=",
        "trip_start": (utc_now() - timedelta(days=1)).isoformat(),
        "trip_end": (utc_now() + timedelta(days=5)).isoformat(),
        "email": "priya@test.com", "password": "Str0ng!Passw0rd",
        "consents": [{"purpose": "privacy_notice_ack", "granted": True},
                     {"purpose": "location_tracking", "granted": False},
                     {"purpose": "guardian_sharing", "granted": False}],
    }
    r = client.post("/api/tourists", json=body)
    assert r.status_code == 201, r.text
    t = db.get(Tourist, r.json()["id"])
    recs = db.query(ConsentRecord).filter_by(tourist_id=t.id).all()
    assert [(c.purpose, c.source) for c in recs] == [("privacy_notice_ack", "registration")]
    assert t.tracking_enabled is False       # saw the choice, did not tick it


def test_registration_can_require_acknowledgements(client, monkeypatch):
    monkeypatch.setattr(settings, "PRIVACY_REGISTRATION_CONSENT_REQUIRED", True)
    body = {
        "full_name": "Priya Sharma", "nationality": "Indian", "document_type": "aadhaar",
        "document_number": "123456789999", "phone": "9876543210",
        "photo": "data:image/png;base64,iVBORw0KGgo=",
        "trip_start": (utc_now() - timedelta(days=1)).isoformat(),
        "trip_end": (utc_now() + timedelta(days=5)).isoformat(), "consents": [],
        "email": "priya.ack@example.com", "password": "Strongpass1!",  # a login is required at registration
    }
    assert client.post("/api/tourists", json=body).status_code == 400
    body["consents"] = [{"purpose": "privacy_notice_ack", "granted": True},
                        {"purpose": "adult_declaration", "granted": True}]
    assert client.post("/api/tourists", json=body).status_code == 201


def test_registration_without_consents_still_works(client):
    body = {
        "full_name": "Priya Sharma", "nationality": "Indian", "document_type": "aadhaar",
        "document_number": "123456789999", "phone": "9876543210",
        "photo": "data:image/png;base64,iVBORw0KGgo=",
        "trip_start": (utc_now() - timedelta(days=1)).isoformat(),
        "trip_end": (utc_now() + timedelta(days=5)).isoformat(),
        "email": "priya.noconsent@example.com", "password": "Strongpass1!",  # a login is required at registration
    }
    assert client.post("/api/tourists", json=body).status_code == 201


def test_consent_choice_requires_explicit_granted(client):
    body = {"full_name": "A B", "nationality": "Indian", "document_type": "aadhaar",
            "document_number": "123456789999", "phone": "9876543210",
        "photo": "data:image/png;base64,iVBORw0KGgo=",
            "trip_start": utc_now().isoformat(),
            "trip_end": (utc_now() + timedelta(days=2)).isoformat(),
            "consents": [{"purpose": "location_tracking"}]}
    assert client.post("/api/tourists", json=body).status_code == 422


def test_reconsent_when_notice_changes(client, tourist_headers, tourist_user, monkeypatch):
    tid = tourist_user.tourist_id
    client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers, json={"purpose": "notifications"})
    monkeypatch.setattr(settings, "PRIVACY_NOTICE_VERSION", "2.0")
    rows = client.get(f"/api/tourists/{tid}/consents", headers=tourist_headers).json()
    assert next(r for r in rows if r["key"] == "notifications")["needs_review"] is True
    client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers, json={"purpose": "notifications"})
    hist = client.get(f"/api/tourists/{tid}/consents/history", headers=tourist_headers).json()
    assert [h["notice_version"] for h in hist if h["purpose"] == "notifications"] == ["2.0", "1.0"]


# ============================================================ retention ====
def test_policies_are_seeded_and_do_not_invent_periods(client, admin_headers):
    r = client.get("/api/admin/privacy/retention/policies", headers=admin_headers)
    assert r.status_code == 200
    by = {p["category"]: p for p in r.json()}
    assert by["location_history"]["retention_days"] == 90 and by["location_history"]["is_active"]
    for cat in ("tourist_profile", "sos_incident_records", "notification_logs", "security_audit_logs"):
        assert by[cat]["retention_days"] is None and by[cat]["requires_review"] is True
        assert by[cat]["is_active"] is False
        assert "No period established" in by[cat]["state"]
    # not one universal number
    assert len({p["retention_days"] for p in by.values() if p["retention_days"] is not None}) <= 2


def test_only_admin_can_edit_policies(client, tourist_headers, admin_headers):
    assert client.patch("/api/admin/privacy/retention/policies/notification_logs",
                        headers=tourist_headers, json={"retention_days": 5}).status_code == 403
    assert client.get("/api/admin/privacy/retention/policies", headers=tourist_headers).status_code == 403
    ok = client.patch("/api/admin/privacy/retention/policies/notification_logs",
                      headers=admin_headers, json={"retention_days": 30, "is_active": True})
    assert ok.status_code == 200 and ok.json()["retention_days"] == 30


def test_cannot_activate_policy_without_a_period(client, admin_headers):
    r = client.patch("/api/admin/privacy/retention/policies/notification_logs",
                     headers=admin_headers, json={"is_active": True})
    assert r.status_code == 400
    assert client.patch("/api/admin/privacy/retention/policies/nope", headers=admin_headers,
                        json={"note": "x"}).status_code == 404
    assert client.patch("/api/admin/privacy/retention/policies/notification_logs", headers=admin_headers,
                        json={"deletion_action": "shred"}).status_code == 400


def test_engine_deletes_expired_location_and_is_idempotent(db):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=200)
    t.data_retention_days = 90
    ping(db, t.id)
    fresh = make_tourist(db, name="Fresh", doc="XXXX-XXXX-0009")
    ping(db, fresh.id)
    first = privacy_retention.run(db)
    loc = next(j for j in first if j["category"] == "location_history")
    assert loc["status"] == "completed" and loc["records_deleted"] == 1
    assert db.query(LocationPing).filter_by(tourist_id=t.id).count() == 0
    assert db.query(LocationPing).filter_by(tourist_id=fresh.id).count() == 1
    second = privacy_retention.run(db)
    assert next(j for j in second if j["category"] == "location_history")["records_deleted"] == 0


def test_job_audit_holds_counts_only(db):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=200)
    ping(db, t.id, lat=11.111111, lng=22.222222)
    privacy_retention.run(db)
    job = db.query(DeletionJob).filter_by(category="location_history").one()
    dump = json.dumps({c.name: str(getattr(job, c.name)) for c in job.__table__.columns})
    assert "11.111111" not in dump and job.job_id.startswith("DEL-")
    assert job.records_processed == 1 and job.finished_at is not None


def test_category_without_period_is_never_touched(db):
    old = NotificationLog(event_type="sos", channel="push", recipient_type="tourist",
                          created_at=utc_now() - timedelta(days=4000), status="sent")
    db.add(old)
    db.commit()
    privacy_retention.run(db)
    assert db.query(NotificationLog).count() == 1


def test_notification_logs_deleted_once_policy_has_period(db):
    db.add_all([
        NotificationLog(event_type="sos", channel="push", recipient_type="tourist",
                        created_at=utc_now() - timedelta(days=100), status="sent"),
        NotificationLog(event_type="sos", channel="push", recipient_type="tourist",
                        created_at=utc_now() - timedelta(days=100), status="retrying"),
        NotificationLog(event_type="sos", channel="push", recipient_type="tourist",
                        created_at=utc_now() - timedelta(days=1), status="sent"),
    ])
    db.commit()
    privacy_retention.update_policy(db, "notification_logs", {"retention_days": 30, "is_active": True}, "admin")
    privacy_retention.run(db)
    left = sorted(n.status for n in db.query(NotificationLog).all())
    assert left == ["retrying", "sent"]          # old+sent gone; in-flight and recent kept


def test_exception_blocks_deletion_then_release_allows_it(db):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=200)
    ping(db, t.id)
    e = privacy_retention.add_exception(db, category="location_history", reason="Court hold",
                                        basis="Order ref 12", review_days=30, actor="admin",
                                        tourist_id=t.id)
    jobs = privacy_retention.run(db)
    loc = next(j for j in jobs if j["category"] == "location_history")
    assert loc["records_deleted"] == 0 and loc["records_retained"] == 1
    assert db.query(LocationPing).filter_by(tourist_id=t.id).count() == 1
    privacy_retention.release_exception(db, e.id, "admin")
    privacy_retention.run(db)
    assert db.query(LocationPing).filter_by(tourist_id=t.id).count() == 0


def test_category_wide_exception_freezes_category(db):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=200)
    ping(db, t.id)
    privacy_retention.add_exception(db, category="location_history", reason="Audit", basis="Policy",
                                    review_days=10, actor="admin")
    privacy_retention.run(db)
    assert db.query(LocationPing).filter_by(tourist_id=t.id).count() == 1


def test_exception_needs_reason_basis_and_review_date(client, admin_headers, tourist_headers):
    bad = client.post("/api/admin/privacy/retention/exceptions", headers=admin_headers,
                      json={"category": "location_history", "reason": "xx", "basis": "yyy"})
    assert bad.status_code == 422        # reason too short
    ok = client.post("/api/admin/privacy/retention/exceptions", headers=admin_headers,
                     json={"category": "location_history", "reason": "Legal hold",
                           "basis": "Order 7", "review_days": 14})
    assert ok.status_code == 201 and ok.json()["review_by"]
    assert client.post("/api/admin/privacy/retention/exceptions", headers=tourist_headers,
                       json={"category": "location_history", "reason": "Legal hold",
                             "basis": "Order 7"}).status_code == 403
    assert client.post("/api/admin/privacy/retention/exceptions", headers=admin_headers,
                       json={"category": "ghost", "reason": "Legal hold",
                             "basis": "Order 7"}).status_code == 404


def test_overdue_exception_review_is_flagged(db):
    e = privacy_retention.add_exception(db, category="location_history", reason="Hold", basis="Order",
                                        review_days=1, actor="admin")
    e.review_by = utc_now() - timedelta(days=1)
    db.commit()
    assert privacy_retention.exception_out(e)["review_overdue"] is True
    assert privacy_retention.summary(db)["exceptions_overdue_for_review"] == 1


def test_open_emergency_protects_location_from_scheduled_purge(db):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=200)
    old_id = ping(db, t.id, ts=utc_now() - timedelta(days=300)).id
    inc = sos(db, t.id)
    inc.detected_at = utc_now() - timedelta(days=250)
    db.commit()
    during_id = ping(db, t.id, ts=utc_now() - timedelta(days=240)).id
    privacy_retention.run(db)
    ids = {p.id for p in db.query(LocationPing).filter_by(tourist_id=t.id).all()}
    assert ids == {during_id} and old_id not in ids


def test_failed_job_is_recorded_and_retried_safely(db, monkeypatch):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=200)
    ping(db, t.id)
    real = privacy_retention.HANDLERS["location_history"]
    calls = {"n": 0}

    def flaky(d, p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk hiccup")
        return real(d, p)

    monkeypatch.setitem(privacy_retention.HANDLERS, "location_history", flaky)
    first = privacy_retention.run(db)
    f = next(j for j in first if j["category"] == "location_history")
    assert f["status"] == "failed" and "disk hiccup" in f["failure_reason"]
    assert db.query(LocationPing).filter_by(tourist_id=t.id).count() == 1
    second = privacy_retention.run(db)
    s = next(j for j in second if j["category"] == "location_history")
    assert s["status"] == "completed" and s["records_deleted"] == 1
    assert privacy_retention.summary(db)["failed_jobs"] == 1


def test_manual_run_and_summary_via_api(client, admin_headers, tourist_headers, db):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=500)
    ping(db, t.id)
    r = client.post("/api/admin/privacy/retention/run", headers=admin_headers, json={"category": "location_history"})
    assert r.status_code == 200 and r.json()["jobs"][0]["records_deleted"] == 1
    s = client.get("/api/admin/privacy/retention/jobs", headers=admin_headers).json()
    assert s["recent_jobs"][0]["job_type"] == "manual"
    assert client.post("/api/admin/privacy/retention/run", headers=tourist_headers, json={}).status_code == 403


def test_expired_session_tokens_are_purged(db):
    from app.models.revoked_token import RevokedToken

    db.add_all([RevokedToken(jti="old", expires_at=utc_now() - timedelta(hours=1)),
                RevokedToken(jti="live", expires_at=utc_now() + timedelta(hours=1))])
    db.commit()
    privacy_retention.run(db)
    assert [r.jti for r in db.query(RevokedToken).all()] == ["live"]


def test_scheduler_tick_runs_engine(db):
    t = make_tourist(db)
    t.trip_end = utc_now() - timedelta(days=500)
    ping(db, t.id)
    privacy_retention.tick(db)
    assert db.query(LocationPing).filter_by(tourist_id=t.id).count() == 0


def test_retention_view_for_tourist_hides_admin_internals(client, tourist_headers, tourist_user):
    r = client.get(f"/api/tourists/{tourist_user.tourist_id}/privacy/overview", headers=tourist_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["retention"] and body["data_inventory"] and body["consents"]
    assert all("updated_by" not in p for p in body["retention"])
    assert "legal_note" in body["notice"]


# ================================================ location privacy ====
def test_view_own_location_history_only(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    other = make_tourist(db, name="Other", doc="XXXX-XXXX-0003")
    ping(db, tid, lat=10.0)
    ping(db, other.id, lat=77.7)
    r = client.get(f"/api/tourists/{tid}/location-history", headers=tourist_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["lat"] == 10.0
    assert body["available_from"] and body["available_to"]
    assert client.get(f"/api/tourists/{other.id}/location-history", headers=tourist_headers).status_code == 403


def test_location_history_pagination_and_range(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    now = utc_now()
    for i in range(5):
        ping(db, tid, ts=now - timedelta(days=i))
    page = client.get(f"/api/tourists/{tid}/location-history?limit=2&offset=1", headers=tourist_headers).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    rng = client.get(f"/api/tourists/{tid}/location-history",
                     params={"date_from": (now - timedelta(days=1, hours=1)).isoformat()},
                     headers=tourist_headers).json()
    assert rng["total"] == 2


def test_delete_selected_records_only_touches_owner(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    other = make_tourist(db, name="Other", doc="XXXX-XXXX-0003")
    a, b = ping(db, tid), ping(db, tid)
    theirs = ping(db, other.id)
    r = client.post(f"/api/tourists/{tid}/location-history/delete", headers=tourist_headers,
                    json={"ids": [a.id, theirs.id]})
    assert r.status_code == 200 and r.json()["deleted"] == 1
    left = {p.id for p in db.query(LocationPing).all()}
    assert left == {b.id, theirs.id}


def test_delete_by_date_range(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    now = utc_now()
    old, new = ping(db, tid, ts=now - timedelta(days=10)), ping(db, tid, ts=now)
    r = client.post(f"/api/tourists/{tid}/location-history/delete", headers=tourist_headers,
                    json={"date_to": (now - timedelta(days=5)).isoformat()})
    assert r.json()["deleted"] == 1
    assert [p.id for p in db.query(LocationPing).filter_by(tourist_id=tid)] == [new.id]


def test_delete_all_clears_cached_position_and_leaves_sos_working(
        client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    ping(db, tid)
    ping(db, tid)
    r = client.post(f"/api/tourists/{tid}/location-history/delete", headers=tourist_headers,
                    json={"delete_all": True})
    assert r.json()["deleted"] == 2 and r.json()["cached_position_cleared"] is True
    db.expire_all()
    t = db.get(Tourist, tid)
    assert t.last_lat is None and t.tracking_enabled is True        # deletion != consent withdrawal
    assert client.post(f"/api/tourists/{tid}/sos", headers=tourist_headers,
                       json={"lat": 26.1, "lng": 91.7, "message": "help"}).status_code in (200, 201)


def test_delete_all_removes_trail_of_resolved_incident_but_keeps_open_one(
        client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    done, live = sos(db, tid, "resolved", True), sos(db, tid)
    live.detected_at = utc_now() - timedelta(hours=1)
    db.add_all([EmergencyLocationPing(incident_id=done.id, lat=1, lng=1),
                EmergencyLocationPing(incident_id=live.id, lat=2, lng=2)])
    ping(db, tid, ts=utc_now() - timedelta(days=3))
    ping(db, tid, ts=utc_now())               # after the open emergency began
    db.commit()
    r = client.post(f"/api/tourists/{tid}/location-history/delete", headers=tourist_headers,
                    json={"delete_all": True}).json()
    assert r["deleted"] == 1 and r["retained"] == 1 and r["emergency_trail_deleted"] == 1
    assert r["retained_reason"] and r["cached_position_cleared"] is False
    assert [p.incident_id for p in db.query(EmergencyLocationPing).all()] == [live.id]


def test_delete_requires_exactly_one_selector(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    for body in ({}, {"delete_all": True, "ids": [1]}, {"ids": []}):
        assert client.post(f"/api/tourists/{tid}/location-history/delete", headers=tourist_headers,
                           json=body).status_code == 422


def test_location_deletion_is_audited_without_coordinates(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    ping(db, tid, lat=12.345678)
    client.post(f"/api/tourists/{tid}/location-history/delete", headers=tourist_headers, json={"delete_all": True})
    row = db.query(AuditLog).filter_by(action="location_history.deleted").one()
    assert "12.345678" not in (row.detail + row.target)
    assert db.query(DeletionJob).filter_by(job_type="manual", category="location_history").count() == 1


# ================================================================ export ====
def _export(client, headers, tid, fmt="json", pw=PW):
    return client.post(f"/api/tourists/{tid}/data-export", headers=headers, json={"format": fmt, "password": pw})


def test_export_requires_authentication_and_password(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    assert client.post(f"/api/tourists/{tid}/data-export", json={"password": PW}).status_code == 401
    assert _export(client, tourist_headers, tid, pw="wrong").status_code == 403
    assert client.post(f"/api/tourists/{tid}/data-export", headers=tourist_headers,
                       json={"format": "json"}).status_code == 422


def test_json_export_has_own_data_and_no_secrets_or_others(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    other, _ = make_login(db, client, name="Zed Outsider", doc="XXXX-XXXX-7777")
    from app.services import tourist_id as tid_service

    tid_service.issue_token(db, db.get(Tourist, tid))
    ping(db, tid, lat=10.5)
    ping(db, other.id, lat=88.8)
    client.post(f"/api/tourists/{tid}/consents", headers=tourist_headers, json={"purpose": "notifications"})
    db.add(MedicalProfile(tourist_id=tid, blood_group="O+"))
    db.commit()
    db.add(TripGuardian(tourist_id=tid, guardian_name="Mum", token="GUARDIAN-SECRET-TOKEN"))
    db.add(Device(device_id="band-1", tourist_id=tid, hashed_key="DEVICE-HASH-SECRET"))
    db.commit()
    r = _export(client, tourist_headers, tid)
    assert r.status_code == 201 and r.json()["status"] == "ready" and r.json()["expires_at"]
    d = client.get(f"/api/tourists/{tid}/data-export/{r.json()['export_id']}/download", headers=tourist_headers)
    assert d.status_code == 200 and "attachment" in d.headers["content-disposition"]
    text = d.text
    data = json.loads(text)
    assert data["export_id"] == r.json()["export_id"] and data["generated_at"]
    assert data["user_profile"]["full_name"] == "Owner Tourist"
    assert len(data["location_history"]) == 1 and data["location_history"][0]["lat"] == 10.5
    assert data["consent_records"][0]["purpose"] == "notifications"
    assert data["medical_profile"][0]["blood_group"] == "O+"
    assert data["privacy_preferences"]["tracking_enabled"] is True
    assert {"sos_records", "digital_identity", "processing_information"} <= data.keys()
    # nothing secret, nothing about anybody else
    raw_token = db.query(TouristIdToken).filter_by(tourist_id=tid).first().raw_token_encrypted
    for forbidden in (user_hash(db, tourist_user), "GUARDIAN-SECRET-TOKEN", "DEVICE-HASH-SECRET",
                      raw_token, "Zed Outsider", "XXXX-XXXX-7777", "88.8", "duress_pin_hash",
                      "hashed_password"):
        assert forbidden not in text, forbidden


def user_hash(db, u):
    return db.get(User, u.id).hashed_password


def test_csv_export_is_a_valid_zip(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    ping(db, tid)
    r = _export(client, tourist_headers, tid, "csv")
    assert r.status_code == 201
    d = client.get(f"/api/tourists/{tid}/data-export/{r.json()['export_id']}/download", headers=tourist_headers)
    assert d.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(d.content))
    names = set(z.namelist())
    assert {"user_profile.csv", "location_history.csv", "consent_records.csv", "README.txt"} <= names
    assert b"lat" in z.read("location_history.csv")
    assert _export(client, tourist_headers, tid, "xml").status_code == 400


def test_export_file_is_encrypted_at_rest(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    _export(client, tourist_headers, tid)
    files = list(privacy_export.export_dir().iterdir())
    assert len(files) == 1
    assert b"Owner Tourist" not in files[0].read_bytes()


def test_other_users_cannot_see_or_download_an_export(client, tourist_headers, tourist_user, admin_headers, db):
    tid = tourist_user.tourist_id
    eid = _export(client, tourist_headers, tid).json()["export_id"]
    other, other_h = make_login(db, client)
    url = f"/api/tourists/{tid}/data-export/{eid}/download"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=other_h).status_code == 403
    assert client.get(url, headers=admin_headers).status_code == 403
    # their own namespace: the id simply does not exist for them
    assert client.get(f"/api/tourists/{other.id}/data-export/{eid}/download", headers=other_h).status_code == 404


def test_export_link_expires_and_files_are_cleaned(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    eid = _export(client, tourist_headers, tid).json()["export_id"]
    row = db.query(DataExportRequest).filter_by(export_id=eid).one()
    row.expires_at = utc_now() - timedelta(minutes=1)
    db.commit()
    d = client.get(f"/api/tourists/{tid}/data-export/{eid}/download", headers=tourist_headers)
    assert d.status_code == 410
    assert client.get(f"/api/tourists/{tid}/data-export/{eid}", headers=tourist_headers).json()["status"] == "expired"
    assert list(privacy_export.export_dir().iterdir()) == []


def test_cleanup_tick_removes_expired_export_files(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    eid = _export(client, tourist_headers, tid).json()["export_id"]
    row = db.query(DataExportRequest).filter_by(export_id=eid).one()
    row.expires_at = utc_now() - timedelta(hours=1)
    db.commit()
    privacy_retention.tick(db)
    assert list(privacy_export.export_dir().iterdir()) == []
    db.expire_all()
    assert db.query(DataExportRequest).filter_by(export_id=eid).one().status == "expired"


def test_export_rate_limit(client, tourist_headers, tourist_user, monkeypatch):
    monkeypatch.setattr(settings, "PRIVACY_EXPORT_MAX_PER_DAY", 2)
    tid = tourist_user.tourist_id
    assert _export(client, tourist_headers, tid).status_code == 201
    assert _export(client, tourist_headers, tid).status_code == 201
    assert _export(client, tourist_headers, tid).status_code == 429


# ======================================================= account deletion ====
def _delete_flow(client, headers, tid, pw=PW):
    start = client.post(f"/api/tourists/{tid}/account-deletion", headers=headers, json={"password": pw})
    assert start.status_code == 201, start.text
    return start.json()["request_id"]


def _confirm(client, headers, tid, rid, pw=PW, phrase="DELETE"):
    return client.post(f"/api/tourists/{tid}/account-deletion/{rid}/confirm", headers=headers,
                       json={"password": pw, "confirmation": phrase})


def test_preview_explains_consequences(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    ping(db, tid)
    p = client.get(f"/api/tourists/{tid}/account-deletion/preview", headers=tourist_headers).json()
    assert any(i["item"] == "Location history" for i in p["will_be_erased"])
    assert p["backups"] and p["confirm_phrase"] == "DELETE" and "external_processors" in p


def test_deletion_needs_password_and_exact_phrase(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    assert client.post(f"/api/tourists/{tid}/account-deletion", headers=tourist_headers,
                       json={"password": "nope"}).status_code == 403
    rid = _delete_flow(client, tourist_headers, tid)
    assert _confirm(client, tourist_headers, tid, rid, pw="nope").status_code == 403
    assert _confirm(client, tourist_headers, tid, rid, phrase="delete me").status_code == 400
    st = client.get(f"/api/tourists/{tid}/account-deletion", headers=tourist_headers).json()
    assert st["status"] == "awaiting_confirmation"      # nothing happened yet


def test_full_deletion_erases_everything_and_revokes_access(client, tourist_user, db):
    from tests.conftest import _token

    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    other = make_tourist(db, name="Bystander", doc="XXXX-XXXX-5555")
    ping(db, tid)
    ping(db, other.id)
    db.add(MedicalProfile(tourist_id=tid, blood_group="A+"))
    db.add(TripGuardian(tourist_id=tid, guardian_name="Mum"))
    db.add(TouristIdScan(tourist_id=tid, scanner_role="responder", verification_status="valid"))
    db.commit()
    client.post(f"/api/tourists/{tid}/consents", headers=h, json={"purpose": "notifications"})
    eid = _export(client, h, tid).json()["export_id"]
    assert eid
    rid = _delete_flow(client, h, tid)
    r = _confirm(client, h, tid, rid)
    assert r.status_code == 200
    db.expire_all()
    # background task ran (TestClient): everything is gone
    req = db.query(AccountDeletionRequest).filter_by(request_id=rid).one()
    assert req.status == "completed" and req.completed_at and req.tourist_id is None
    assert db.get(Tourist, tid) is None
    assert db.query(User).filter_by(email="tourist@test.com").count() == 0
    assert db.query(LocationPing).filter_by(tourist_id=tid).count() == 0
    assert db.query(MedicalProfile).filter_by(tourist_id=tid).count() == 0
    assert db.query(TripGuardian).filter_by(tourist_id=tid).count() == 0
    assert db.query(TouristIdToken).filter_by(tourist_id=tid).count() == 0
    assert db.query(DataExportRequest).filter_by(tourist_id=tid).count() == 0
    assert list(privacy_export.export_dir().iterdir()) == []
    scan = db.query(TouristIdScan).one()
    assert scan.tourist_id is None                                  # responder's record kept, link cut
    # consent history survives, de-identified
    cons = db.query(ConsentRecord).all()
    assert cons and all(c.tourist_id is None and c.subject_ref for c in cons)
    # the bystander is untouched
    assert db.get(Tourist, other.id) is not None
    assert db.query(LocationPing).filter_by(tourist_id=other.id).count() == 1
    # no e-mail left (checked before the login attempt below, which a stranger could make) in the security log
    assert not any("tourist@test.com" in (a.actor + a.target + a.detail) for a in db.query(AuditLog).all())
    # old token no longer works, login impossible
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.post("/api/auth/login", data={"username": "tourist@test.com", "password": PW}).status_code == 401


def test_public_status_page_has_no_personal_data(client, tourist_user, db):
    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    rid = _delete_flow(client, h, tid)
    _confirm(client, h, tid, rid)
    r = client.get(f"/api/privacy/deletion-status/{rid}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed" and body["label"] == "Deletion completed"
    assert "tourist@test.com" not in r.text and "Owner Tourist" not in r.text
    assert body["backups"]
    assert client.get("/api/privacy/deletion-status/DEL-NOPE").status_code == 404


def test_open_emergency_is_retained_and_status_is_honest(client, tourist_user, db):
    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    open_inc = sos(db, tid)
    done_inc = sos(db, tid, "resolved", True)
    ping(db, tid)
    rid = _delete_flow(client, h, tid)
    _confirm(client, h, tid, rid)
    db.expire_all()
    req = db.query(AccountDeletionRequest).filter_by(request_id=rid).one()
    assert req.status == "partially_retained" and req.completed_at is None
    retained = json.loads(req.retained)
    assert any(r["category"] == "sos_incident_records" and r["access"] == "administrators only" for r in retained)
    assert db.get(Incident, open_inc.id).tourist_id == tid            # still linked, still open
    assert db.get(Incident, done_inc.id).tourist_id is None            # de-identified
    assert db.get(Incident, done_inc.id).description == ""
    t = db.get(Tourist, tid)
    assert t.full_name == "Deleted user" and t.phone == "" and t.photo is None
    assert t.digital_id.startswith("DELETED-")
    assert db.query(LocationPing).filter_by(tourist_id=tid).count() == 0
    assert db.query(User).filter_by(tourist_id=tid).count() == 0       # cannot log in
    # reason ends -> next sweep finishes the job
    inc = db.get(Incident, open_inc.id)
    inc.status, inc.resolved_at = "resolved", utc_now()
    db.commit()
    privacy_deletion.process_due(db)
    db.expire_all()
    req = db.query(AccountDeletionRequest).filter_by(request_id=rid).one()
    assert req.status == "completed" and json.loads(req.retained) == []
    assert db.get(Tourist, tid) is None


def test_policy_retain_keeps_efir_with_reason(client, tourist_user, db):
    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    inc = sos(db, tid, "resolved", True)
    db.add(EFIR(fir_number="FIR-1", incident_id=inc.id, tourist_id=tid, narrative="n",
                document_hash="h"))
    db.commit()
    rid = _delete_flow(client, h, tid)
    _confirm(client, h, tid, rid)
    db.expire_all()
    req = db.query(AccountDeletionRequest).filter_by(request_id=rid).one()
    assert req.status == "partially_retained"
    assert any(r["category"] == "efir_records" for r in json.loads(req.retained))
    assert db.query(EFIR).count() == 1
    # an admin decision (policy change) releases it on the next sweep
    privacy_retention.update_policy(db, "efir_records", {"account_deletion_action": "delete"}, "admin")
    db.query(EFIR).delete()          # (the FIR table itself is not erased by the engine for resolved FIRs)
    db.commit()
    privacy_deletion.process_due(db)
    db.expire_all()
    assert db.query(AccountDeletionRequest).filter_by(request_id=rid).one().status == "completed"


def test_legal_hold_exception_is_respected_on_deletion(client, tourist_user, db):
    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    ping(db, tid)
    privacy_retention.add_exception(db, category="location_history", reason="Police request",
                                    basis="Notice 9", review_days=30, actor="admin", tourist_id=tid)
    rid = _delete_flow(client, h, tid)
    _confirm(client, h, tid, rid)
    db.expire_all()
    req = db.query(AccountDeletionRequest).filter_by(request_id=rid).one()
    assert req.status == "partially_retained"
    assert db.query(LocationPing).filter_by(tourist_id=tid).count() == 1
    assert any(r["category"] == "location_history" for r in json.loads(req.retained))


def test_repeated_requests_are_safe(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    a = _delete_flow(client, tourist_headers, tid)
    b = _delete_flow(client, tourist_headers, tid)
    assert a == b
    assert db.query(AccountDeletionRequest).count() == 1
    first = _confirm(client, tourist_headers, tid, a)
    assert first.status_code == 200
    second = privacy_deletion.process_request(db, db.query(AccountDeletionRequest).one().id)
    assert second.status == "completed"
    again = privacy_deletion.process_request(db, second.id)      # nothing left to do, still fine
    assert again.status == "completed"


def test_cancel_before_processing(client, tourist_headers, tourist_user, db):
    tid = tourist_user.tourist_id
    rid = _delete_flow(client, tourist_headers, tid)
    r = client.post(f"/api/tourists/{tid}/account-deletion/{rid}/cancel", headers=tourist_headers)
    assert r.json()["status"] == "cancelled"
    assert db.get(Tourist, tid) is not None
    assert _delete_flow(client, tourist_headers, tid) != rid          # can start a fresh one


def test_grace_period_blocks_login_until_processed(client, tourist_user, db, monkeypatch):
    monkeypatch.setattr(settings, "PRIVACY_DELETION_GRACE_HOURS", 2)
    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    rid = _delete_flow(client, h, tid)
    r = _confirm(client, h, tid, rid)
    assert r.json()["status"] == "requested" and r.json()["label"] == "Deletion requested"
    # sessions revoked immediately, new login refused, tracking stopped, nothing deleted yet
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.post("/api/auth/login", data={"username": "tourist@test.com", "password": PW}).status_code == 403
    db.expire_all()
    assert db.get(Tourist, tid).tracking_enabled is False
    assert privacy_deletion.process_due(db) == []                     # not due yet
    assert db.get(Tourist, tid) is not None
    req = db.query(AccountDeletionRequest).one()
    req.process_after = utc_now() - timedelta(seconds=1)
    db.commit()
    assert privacy_deletion.process_due(db) == [f"{rid}:completed"]
    db.expire_all()
    assert db.get(Tourist, tid) is None


def test_failed_deletion_is_visible_and_retried(client, tourist_user, db, monkeypatch):
    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    real = privacy_deletion._erase_source
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db went away")
        return real(*a, **k)

    monkeypatch.setattr(privacy_deletion, "_erase_source", boom)
    rid = _delete_flow(client, h, tid)
    _confirm(client, h, tid, rid)
    db.expire_all()
    req = db.query(AccountDeletionRequest).filter_by(request_id=rid).one()
    assert req.status == "failed" and "db went away" in req.failure_reason
    assert privacy_deletion.status_out(req)["label"] == "Deletion failed - requires attention"
    assert db.get(Tourist, tid) is not None                           # not claimed as deleted
    privacy_deletion.process_due(db)
    db.expire_all()
    req = db.query(AccountDeletionRequest).filter_by(request_id=rid).one()
    assert req.status == "completed" and req.attempts == 2
    assert db.get(Tourist, tid) is None


def test_retries_are_bounded(db, tourist_user, monkeypatch):
    monkeypatch.setattr(settings, "PRIVACY_DELETION_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(privacy_deletion, "_erase_source", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    req = AccountDeletionRequest(request_id="DEL-X", tourist_id=tourist_user.tourist_id,
                                 user_id=tourist_user.id, subject_ref="r", status="requested",
                                 process_after=utc_now())
    db.add(req)
    db.commit()
    for _ in range(5):
        privacy_deletion.process_due(db)
    db.refresh(req)
    assert req.status == "failed" and req.attempts == 2


def test_deletion_cannot_be_triggered_by_others(client, tourist_headers, tourist_user, admin_headers, db):
    tid = tourist_user.tourist_id
    other, other_h = make_login(db, client)
    for h in (other_h, admin_headers):
        assert client.post(f"/api/tourists/{tid}/account-deletion", headers=h,
                           json={"password": PW}).status_code == 403
    rid = _delete_flow(client, tourist_headers, tid)
    assert client.post(f"/api/tourists/{other.id}/account-deletion/{rid}/confirm", headers=other_h,
                       json={"password": PW, "confirmation": "DELETE"}).status_code == 404
    assert client.post(f"/api/tourists/{tid}/account-deletion", json={"password": PW}).status_code == 401
    assert db.get(Tourist, tid) is not None


def test_external_processors_are_reported_not_pretended(client, tourist_user, db, monkeypatch):
    monkeypatch.setattr(settings, "PRIVACY_EXTERNAL_PROCESSORS", '[{"name":"SMS gateway","contact":"dpo@sms.example"}]')
    h = _token(client, "tourist@test.com", PW)
    rid = _delete_flow(client, h, tourist_user.tourist_id)
    _confirm(client, h, tourist_user.tourist_id, rid)
    body = client.get(f"/api/privacy/deletion-status/{rid}").json()
    assert body["external_processors"] == [
        {"name": "SMS gateway", "contact": "dpo@sms.example", "status": "manual_notification_required"}]
    assert "backups" in body and body["backups"]


def test_deletion_job_audit_counts_only(client, tourist_user, db):
    h = _token(client, "tourist@test.com", PW)
    ping(db, tourist_user.tourist_id, lat=33.333333)
    rid = _delete_flow(client, h, tourist_user.tourist_id)
    _confirm(client, h, tourist_user.tourist_id, rid)
    job = db.query(DeletionJob).filter_by(category="account_deletion").one()
    assert job.records_deleted >= 1 and job.subject_ref
    assert "33.333333" not in json.dumps({c.name: str(getattr(job, c.name)) for c in job.__table__.columns})


# ============================================================ grievances ====
def test_grievance_lifecycle(client, tourist_headers, tourist_user, admin_headers, db):
    tid = tourist_user.tourist_id
    r = client.post(f"/api/tourists/{tid}/grievances", headers=tourist_headers,
                    json={"category": "location", "description": "I withdrew consent but still see pings"})
    assert r.status_code == 201
    g = r.json()
    assert g["grievance_id"].startswith("GRV-") and g["status"] == "open" and g["due_at"]
    mine = client.get(f"/api/tourists/{tid}/grievances", headers=tourist_headers).json()
    assert [x["grievance_id"] for x in mine] == [g["grievance_id"]]
    admin_list = client.get("/api/admin/privacy/grievances?status=open", headers=admin_headers).json()
    assert admin_list[0]["grievance_id"] == g["grievance_id"] and "subject_ref" in admin_list[0]
    resp = client.post(f"/api/admin/privacy/grievances/{g['grievance_id']}/respond", headers=admin_headers,
                       json={"message": "Checked - collection had stopped. Closing.", "status": "resolved"})
    assert resp.status_code == 200 and resp.json()["status"] == "resolved" and resp.json()["resolved_at"]
    detail = client.get(f"/api/tourists/{tid}/grievances/{g['grievance_id']}", headers=tourist_headers).json()
    assert detail["responses"][0]["author_role"] == "admin" and detail["status"] == "resolved"
    closed = client.post(f"/api/tourists/{tid}/grievances/{g['grievance_id']}/reply", headers=tourist_headers,
                         json={"message": "thanks"})
    assert closed.status_code == 409


def test_grievance_access_control_and_validation(client, tourist_headers, tourist_user, admin_headers, db):
    tid = tourist_user.tourist_id
    other, other_h = make_login(db, client)
    g = client.post(f"/api/tourists/{tid}/grievances", headers=tourist_headers,
                    json={"category": "other", "description": "Please explain my data"}).json()
    assert client.get(f"/api/tourists/{tid}/grievances/{g['grievance_id']}", headers=other_h).status_code == 403
    assert client.get(f"/api/tourists/{other.id}/grievances/{g['grievance_id']}", headers=other_h).status_code == 404
    assert client.get("/api/admin/privacy/grievances", headers=tourist_headers).status_code == 403
    assert client.post(f"/api/admin/privacy/grievances/{g['grievance_id']}/respond", headers=tourist_headers,
                       json={"message": "x"}).status_code == 403
    assert client.post(f"/api/tourists/{tid}/grievances", headers=tourist_headers,
                       json={"category": "other", "description": "short"}).status_code == 400
    assert client.post(f"/api/tourists/{tid}/grievances", headers=tourist_headers,
                       json={"category": "bogus", "description": "long enough text"}).status_code == 400
    assert client.post(f"/api/admin/privacy/grievances/{g['grievance_id']}/respond", headers=admin_headers,
                       json={"message": "ok", "status": "weird"}).status_code == 400


def test_open_grievance_blocks_deletion_of_its_record(client, tourist_user, db):
    from app.models.privacy import PrivacyGrievance

    tid = tourist_user.tourist_id
    h = _token(client, "tourist@test.com", PW)
    client.post(f"/api/tourists/{tid}/grievances", headers=h,
                json={"category": "other", "description": "Still unresolved complaint"})
    rid = _delete_flow(client, h, tid)
    _confirm(client, h, tid, rid)
    db.expire_all()
    assert db.query(AccountDeletionRequest).filter_by(request_id=rid).one().status == "partially_retained"
    assert db.query(PrivacyGrievance).one().description.startswith("Still unresolved")


# ====================================================== registry / misc ====
def test_registry_covers_every_table_that_references_a_tourist():
    """A new table with a tourist_id must be added to the personal-data registry
    (or knowingly listed here) -- otherwise export/erasure would silently miss it."""
    from app.db.session import Base
    from app.services.privacy_registry import SOURCES

    covered = {s.model.__tablename__ for s in SOURCES} | {
        "tourists", "users", "incident_events", "emergency_location_pings"}
    # not personal data of a tourist, or handled by their parent row
    exempt = {"privacy_grievance_responses", "retention_exceptions", "account_deletion_requests",
              "deletion_jobs"}
    missing = []
    for table in Base.metadata.tables.values():
        if table.name in covered or table.name in exempt:
            continue
        if any(fk.column.table.name == "tourists" for fk in table.foreign_keys):
            missing.append(table.name)
    assert missing == []


def test_inventory_lists_purpose_access_sharing_and_retention(client, admin_headers):
    inv = client.get("/api/admin/privacy/inventory", headers=admin_headers).json()
    loc = next(i for i in inv if i["category"] == "location_history")
    assert "location_pings" in loc["tables"] and loc["purpose"] and loc["who_can_access"]
    assert loc["retention"]["retention_days"] == 90
    assert {i["category"] for i in inv} >= {"digital_identity", "sos_incident_records", "medical_profile"}


def test_policy_row_survives_seed_rerun(db):
    privacy_retention.seed_policies(db)
    p = db.query(RetentionPolicy).filter_by(category="notification_logs").one()
    p.note = "edited by admin"
    db.commit()
    assert privacy_retention.seed_policies(db) == 0
    assert db.query(RetentionPolicy).filter_by(category="notification_logs").one().note == "edited by admin"


def test_privacy_consent_helpers_do_not_treat_legacy_users_as_withdrawn(db):
    t = make_tourist(db)
    assert privacy_consent.is_withdrawn(db, t.id, "location_tracking") is False
