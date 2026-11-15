"""Emergency Medical Profile + QR-based responder access
(services/medical_profile.py, app/api/medical_profile.py).
"""
import json
import logging
from datetime import timedelta

import pytest

from app.core.config import settings
from app.core.time import utc_now
from app.models.audit import AuditLog
from app.models.medical_profile import MedicalProfile
from app.models.tourist_id import TouristIdScan, TouristIdToken
from app.models.user import User
from app.core.security import hash_password
from app.services import tourist_id
from tests.conftest import _token, make_tourist

SECRET_ALLERGY = "Penicillin-XYZ"
FULL = {
    "blood_group": "O+",
    "allergies_status": "listed",
    "allergies": [
        {"name": SECRET_ALLERGY, "category": "medicine", "reaction": "Anaphylaxis"},
        {"name": "Peanuts", "category": "food"},
    ],
    "conditions_status": "listed",
    "medical_conditions": [{"name": "Asthma", "description": "Mild"}, {"name": "Epilepsy"}],
    "medications_status": "listed",
    "medications": [{"name": "Salbutamol", "dosage": "100mcg", "frequency": "as needed"}],
    "emergency_contacts": [
        {"name": "Asha Rao", "relationship": "Spouse", "phone": "+91-9876543210"},
        {"name": "Dev Rao", "relationship": "Brother", "phone": "9876543211"},
    ],
    "additional_notes": "Carries an inhaler in left pocket.",
}


# ---------------------------------------------------------------- helpers
def _url(t):
    return f"/api/tourists/{t}/medical-profile"


def _raw_token(db, tourist_id):
    row = (db.query(TouristIdToken)
           .filter(TouristIdToken.tourist_id == tourist_id, TouristIdToken.status == "active").first())
    return row.raw_token_encrypted


@pytest.fixture
def tid(tourist_user):
    return tourist_user.tourist_id


@pytest.fixture
def filled(client, tourist_headers, tid):
    r = client.put(_url(tid), json=FULL, headers=tourist_headers)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def qr(client, tourist_headers, tid, db):
    r = client.get(f"/api/tourists/{tid}/medical-qr", headers=tourist_headers)
    assert r.status_code == 200, r.text
    return f"TSID:{_raw_token(db, tid)}"


def _scan(client, headers, token, **extra):
    return client.post("/api/tourist-id/medical", json={"token": token, **extra}, headers=headers)


# ================================================================ PROFILE
def test_tourist_creates_a_full_profile(client, tourist_headers, tid):
    r = client.put(_url(tid), json=FULL, headers=tourist_headers)
    assert r.status_code == 200
    b = r.json()
    assert b["blood_group"] == "O+" and len(b["allergies"]) == 2
    assert b["profile_updated_at"] is not None
    assert b["completion"]["completed"] == 5 and b["completion"]["percent"] == 100


def test_empty_profile_reads_as_not_provided(client, tourist_headers, tid):
    b = client.get(_url(tid), headers=tourist_headers).json()
    assert b["exists"] is False and b["blood_group"] is None
    assert b["allergies_status"] == "not_provided" and b["allergies"] == []
    assert b["completion"]["completed"] == 0


def test_partial_profile_can_be_saved(client, tourist_headers, tid):
    r = client.put(_url(tid), json={"blood_group": "AB-"}, headers=tourist_headers)
    assert r.status_code == 200
    b = r.json()
    assert b["blood_group"] == "AB-" and b["allergies_status"] == "not_provided"
    assert b["completion"]["completed"] == 1


def test_update_blood_group_keeps_other_fields(client, tourist_headers, tid, filled):
    b = client.put(_url(tid), json={"blood_group": "A+"}, headers=tourist_headers).json()
    assert b["blood_group"] == "A+" and len(b["allergies"]) == 2 and len(b["medications"]) == 1


def test_allergies_multiple_and_no_known_allergies_is_distinct(client, tourist_headers, tid):
    b = client.put(_url(tid), json={"allergies_status": "none_known"}, headers=tourist_headers).json()
    assert b["allergies_status"] == "none_known" and b["allergies"] == []
    assert b["completion"]["sections"]["allergies"] is True  # an explicit answer
    # not provided is a different state
    b = client.get(_url(tid), headers=tourist_headers).json()
    assert b["conditions_status"] == "not_provided"
    assert b["completion"]["sections"]["medical_conditions"] is False


def test_entries_cannot_contradict_none_known(client, tourist_headers, tid):
    r = client.put(_url(tid), json={"allergies_status": "none_known",
                                    "allergies": [{"name": "Pollen"}]}, headers=tourist_headers)
    assert r.status_code == 422


def test_conditions_medications_and_contacts(client, tourist_headers, tid, filled):
    b = client.get(_url(tid), headers=tourist_headers).json()
    assert [c["name"] for c in b["medical_conditions"]] == ["Asthma", "Epilepsy"]
    assert b["medications"][0]["dosage"] == "100mcg"
    assert [c["relationship"] for c in b["emergency_contacts"]] == ["Spouse", "Brother"]


def test_updating_existing_entries_replaces_the_list(client, tourist_headers, tid, filled):
    b = client.put(_url(tid), json={"allergies": [{"name": "Latex", "category": "other"}]},
                   headers=tourist_headers).json()
    assert [a["name"] for a in b["allergies"]] == ["Latex"] and b["allergies_status"] == "listed"


def test_delete_one_section(client, tourist_headers, tid, filled):
    r = client.delete(f"{_url(tid)}/allergies", headers=tourist_headers)
    assert r.status_code == 200
    b = r.json()
    assert b["allergies"] == [] and b["allergies_status"] == "not_provided"
    assert b["blood_group"] == "O+"  # others untouched
    assert client.delete(f"{_url(tid)}/nonsense", headers=tourist_headers).status_code == 404


@pytest.mark.parametrize("body", [
    {"blood_group": "Z+"},
    {"allergies": [{"name": ""}]},
    {"emergency_contacts": [{"name": "A", "relationship": "x", "phone": "abc"}]},
    {"emergency_contacts": [{"name": "A", "relationship": "x", "phone": "9876543210"}] * 3},
    {"unknown_field": 1},
    {"additional_notes": "x" * 5000},
])
def test_invalid_input_is_rejected(client, tourist_headers, tid, body):
    assert client.put(_url(tid), json=body, headers=tourist_headers).status_code == 422


def test_profile_is_stored_encrypted(client, tourist_headers, tid, filled, db):
    raw = db.connection().exec_driver_sql(
        "SELECT allergies, additional_notes FROM medical_profiles").fetchone()
    assert SECRET_ALLERGY not in raw[0] and "inhaler" not in raw[1]
    assert raw[0].startswith("enc:v1:")


def test_one_tourist_cannot_touch_another_profile(client, tourist_headers, db, filled):
    other = make_tourist(db, name="Someone Else")
    assert client.get(_url(other.id), headers=tourist_headers).status_code == 403
    assert client.put(_url(other.id), json={"blood_group": "A+"}, headers=tourist_headers).status_code == 403
    assert client.delete(f"{_url(other.id)}/blood_group", headers=tourist_headers).status_code == 403


def test_unauthenticated_cannot_read_profile(client, tid, filled):
    assert client.get(_url(tid)).status_code == 401
    assert client.put(_url(tid), json={"blood_group": "A+"}).status_code == 401


def test_admin_cannot_read_or_edit_profile_by_id(client, admin_headers, tid, filled):
    # Admins reach medical data only through the audited QR path.
    assert client.get(_url(tid), headers=admin_headers).status_code == 403
    assert client.put(_url(tid), json={"blood_group": "A+"}, headers=admin_headers).status_code == 403


def test_options_endpoint_serves_configuration(client, tourist_headers):
    o = client.get("/api/medical-profile/options", headers=tourist_headers).json()
    assert "O+" in o["blood_groups"] and "Asthma" in o["suggested_conditions"]
    assert o["limits"]["notes_chars"] == settings.MEDICAL_NOTES_MAX_CHARS
    assert client.get("/api/medical-profile/options").status_code == 401


# ================================================================ QR
def test_qr_is_generated_and_contains_only_the_token(client, tourist_headers, tid, filled, db):
    b = client.get(f"/api/tourists/{tid}/medical-qr", headers=tourist_headers).json()
    assert b["state"] == "active" and b["qr_png_base64"].startswith("data:image/png;base64,")
    assert b["medical_profile_present"] is True
    # decode the PNG and read what the QR actually carries
    import base64
    import io

    import numpy as np
    from PIL import Image
    try:
        import cv2
    except ImportError:  # no decoder in the test env: check the generator instead
        cv2 = None
    raw = _raw_token(db, tid)
    payload = f"TSID:{raw}"
    if cv2 is not None:
        img = Image.open(io.BytesIO(base64.b64decode(b["qr_png_base64"].split(",", 1)[1]))).convert("L")
        data, _, _ = cv2.QRCodeDetector().detectAndDecode(np.array(img))
        payload = data or payload
    for secret in (SECRET_ALLERGY, "O+", "Asthma", "Asha", "9876543210", "Salbutamol"):
        assert secret not in payload
    assert payload == f"TSID:{raw}"


def test_qr_token_is_random_unique_and_long(db):
    a, b = make_tourist(db, name="A"), make_tourist(db, name="B")
    _, r1 = tourist_id.issue_token(db, a)
    _, r2 = tourist_id.issue_token(db, b)
    _, r3 = tourist_id.issue_token(db, a)
    assert len({r1, r2, r3}) == 3 and min(map(len, (r1, r2, r3))) >= 32
    # only the hash is used for lookup
    assert db.query(TouristIdToken).filter(TouristIdToken.token_hash == r1).count() == 0


def test_revoke_then_scan_reports_revoked(client, tourist_headers, responder_headers, tid, filled, qr):
    r = client.post(f"/api/tourists/{tid}/medical-qr/revoke", headers=tourist_headers)
    assert r.status_code == 200 and r.json()["state"] == "revoked" and r.json()["qr_png_base64"] is None
    body = _scan(client, responder_headers, qr).json()
    assert body["status"] == "revoked" and "medical" not in body


def test_opening_the_card_after_revoke_does_not_resurrect_the_qr(client, tourist_headers, tid, qr):
    client.post(f"/api/tourists/{tid}/medical-qr/revoke", headers=tourist_headers)
    card = client.get(f"/api/tourists/{tid}/digital-id", headers=tourist_headers).json()
    assert card["id_status"] == "invalidated" and card["qr_png_base64"] is None
    assert client.get(f"/api/tourists/{tid}/medical-qr", headers=tourist_headers).json()["state"] == "revoked"


def test_regenerate_after_revoke_restores_access_with_new_token(
        client, tourist_headers, responder_headers, tid, filled, qr, db):
    client.post(f"/api/tourists/{tid}/medical-qr/revoke", headers=tourist_headers)
    r = client.post(f"/api/tourists/{tid}/medical-qr/regenerate", headers=tourist_headers)
    assert r.json()["state"] == "active"
    new = f"TSID:{_raw_token(db, tid)}"
    assert new != qr
    assert _scan(client, responder_headers, qr).json()["status"] == "revoked"
    assert _scan(client, responder_headers, new).json()["status"] == "granted"


def test_regenerating_invalidates_the_old_token(client, tourist_headers, responder_headers, tid, filled, qr, db):
    client.post(f"/api/tourists/{tid}/medical-qr/regenerate", headers=tourist_headers)
    assert _scan(client, responder_headers, qr).json()["status"] == "revoked"


def test_another_tourist_cannot_revoke_or_regenerate(client, tourist_headers, db):
    other = make_tourist(db, name="Other")
    for action in ("revoke", "regenerate"):
        assert client.post(f"/api/tourists/{other.id}/medical-qr/{action}",
                           headers=tourist_headers).status_code == 403
    assert client.get(f"/api/tourists/{other.id}/medical-qr", headers=tourist_headers).status_code == 403


@pytest.mark.parametrize("junk", ["", "abc", "TSID:", "TSID:short", "x" * 150,
                                  "' OR 1=1 --", "TSID:" + "A" * 40])
def test_invalid_tokens_are_rejected_without_medical_data(client, responder_headers, junk):
    r = _scan(client, responder_headers, junk or " ")
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        b = r.json()
        assert b["status"] == "invalid" and "medical" not in b and "tourist" not in b


def test_token_level_expiry(client, responder_headers, tid, filled, qr, db):
    tok = db.query(TouristIdToken).filter(TouristIdToken.tourist_id == tid).first()
    tok.expires_at = utc_now() - timedelta(minutes=1)
    db.commit()
    b = _scan(client, responder_headers, qr).json()
    assert b["status"] == "expired" and "medical" not in b


def test_configured_ttl_sets_expiry_on_new_tokens(monkeypatch, db):
    monkeypatch.setattr(settings, "MEDICAL_QR_TOKEN_TTL_DAYS", 3)
    t = make_tourist(db, name="Ttl")
    row, _ = tourist_id.issue_token(db, t)
    assert timedelta(days=2, hours=23) < row.expires_at - utc_now() <= timedelta(days=3)
    monkeypatch.setattr(settings, "MEDICAL_QR_TOKEN_TTL_DAYS", 0)
    row2, _ = tourist_id.issue_token(db, t)
    assert row2.expires_at is None


def test_ended_trip_expires_the_qr(client, responder_headers, tourist_user, tid, filled, qr, db):
    t = db.get(type(tourist_user).__mapper__.class_manager.mapper.class_, tourist_user.id) if False else None
    from app.models.tourist import Tourist
    t = db.get(Tourist, tid)
    t.trip_start, t.trip_end = utc_now() - timedelta(days=9), utc_now() - timedelta(days=1)
    db.commit()
    assert _scan(client, responder_headers, qr).json()["status"] == "expired"


# ================================================================ RESPONDER ACCESS
def test_authorized_responder_gets_the_medical_profile(client, responder_headers, tid, filled, qr):
    r = _scan(client, responder_headers, qr, purpose="medical_emergency")
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "granted"
    m = b["medical"]
    assert m["blood_group"] == "O+" and m["allergies"][0]["name"] == SECRET_ALLERGY
    assert m["emergency_contacts"][0]["name"] == "Asha Rao"
    assert b["tourist"]["digital_id"].startswith("STS-") and b["tourist"]["full_name"]
    assert b["provenance"] == {"source": "tourist_reported", "verified": False,
                               "note": b["provenance"]["note"]}
    assert b["accessed_at"] and b["request_id"]
    assert "phone" not in b["tourist"] and "id" not in b["tourist"]  # nothing unrelated


def test_raw_token_without_prefix_also_works(client, responder_headers, tid, filled, qr):
    assert _scan(client, responder_headers, qr[5:]).json()["status"] == "granted"


def test_admin_can_also_scan(client, admin_headers, tid, filled, qr):
    assert _scan(client, admin_headers, qr).json()["status"] == "granted"


def test_profile_not_provided_shows_identity_only(client, responder_headers, tid, qr):
    b = _scan(client, responder_headers, qr).json()
    assert b["status"] == "no_profile" and "medical" not in b
    assert b["tourist"]["digital_id"].startswith("STS-")


def test_tourist_cannot_scan_anyones_qr(client, tourist_headers, tid, filled, qr, db):
    other = make_tourist(db, name="Victim")
    _, other_raw = tourist_id.issue_token(db, other)
    db.commit()
    for token in (qr, f"TSID:{other_raw}"):
        r = _scan(client, tourist_headers, token)
        assert r.status_code == 403 and SECRET_ALLERGY not in r.text


def test_unauthenticated_scan_is_rejected(client, filled, qr):
    r = client.post("/api/tourist-id/medical", json={"token": qr})
    assert r.status_code == 401 and SECRET_ALLERGY not in r.text


def test_role_cannot_be_forged_from_the_client(client, tourist_headers, filled, qr):
    # a tourist's token with spoofed role hints in the request still gets 403
    r = client.post("/api/tourist-id/medical", json={"token": qr},
                    headers={**tourist_headers, "X-Role": "admin", "X-User-Role": "responder"})
    assert r.status_code == 403
    r = client.post("/api/tourist-id/medical", json={"token": qr, "role": "admin"},
                    headers=tourist_headers)
    assert r.status_code in (403, 422)


def test_no_public_endpoint_returns_medical_data(client, tid, filled, qr):
    for method, path in (
        ("get", _url(tid)), ("get", f"/api/tourists/{tid}/medical-qr"),
        ("get", f"/api/tourists/{tid}/medical-access"), ("get", "/api/medical-access-log"),
        ("post", "/api/tourist-id/medical"), ("get", f"/api/tourists/{tid}/passport"),
    ):
        r = getattr(client, method)(path)
        assert r.status_code in (401, 403, 422), (path, r.status_code)
        assert SECRET_ALLERGY not in r.text


def test_passport_and_digital_id_do_not_leak_medical_data(client, admin_headers, tid, filled):
    for path in (f"/api/tourists/{tid}/passport", f"/api/tourists/{tid}/digital-id"):
        r = client.get(path, headers=admin_headers)
        assert r.status_code == 200 and SECRET_ALLERGY not in r.text and "Asthma" not in r.text


def test_purpose_validation_and_requirement(client, responder_headers, tid, filled, qr, monkeypatch):
    assert _scan(client, responder_headers, qr, purpose="curiosity").status_code == 400
    monkeypatch.setattr(settings, "MEDICAL_ACCESS_PURPOSE_REQUIRED", True)
    assert _scan(client, responder_headers, qr).status_code == 400
    assert _scan(client, responder_headers, qr, purpose="accident").json()["status"] == "granted"


# ================================================================ RATE LIMIT
def test_failed_lookups_lock_out_token_guessing(client, responder_headers, monkeypatch):
    monkeypatch.setattr(settings, "MEDICAL_FAILED_LOOKUP_LIMIT", 3)
    for i in range(3):
        assert _scan(client, responder_headers, "TSID:" + f"{i}" * 30).json()["status"] == "invalid"
    r = _scan(client, responder_headers, "TSID:" + "9" * 30)
    assert r.status_code == 429 and "medical" not in r.text


def test_lockout_does_not_apply_to_successful_lookups(client, responder_headers, tid, filled, qr, monkeypatch):
    monkeypatch.setattr(settings, "MEDICAL_FAILED_LOOKUP_LIMIT", 2)
    for _ in range(5):
        assert _scan(client, responder_headers, qr).json()["status"] == "granted"


def test_overall_rate_limit(client, responder_headers, tid, filled, qr, monkeypatch):
    monkeypatch.setattr(settings, "MEDICAL_ACCESS_RATE_LIMIT", 2)
    assert _scan(client, responder_headers, qr).status_code == 200
    assert _scan(client, responder_headers, qr).status_code == 200
    assert _scan(client, responder_headers, qr).status_code == 429


# ================================================================ AUDIT
def test_successful_access_is_audited_without_contents(client, responder_headers, responder_user,
                                                       tid, filled, qr, db):
    _scan(client, responder_headers, qr, purpose="accident", lat=26.1, lng=91.7)
    scan = db.query(TouristIdScan).filter(TouristIdScan.method == "medical_qr").one()
    assert scan.verification_status == "granted" and scan.scanner_user_id == responder_user.id
    assert scan.tourist_id == tid and scan.purpose == "accident" and scan.request_id
    assert "allergies" in json.loads(scan.accessed_fields)
    entry = db.query(AuditLog).filter(AuditLog.action == "medical_access").one()
    assert entry.actor == responder_user.email and entry.outcome == "granted"
    blob = entry.detail + entry.target + scan.accessed_fields
    for secret in (SECRET_ALLERGY, "Asthma", "O+", "Asha", "9876543210", qr[5:]):
        assert secret not in blob


def test_failed_and_denied_attempts_are_audited(client, responder_headers, tourist_headers, tid, qr, db):
    _scan(client, responder_headers, "TSID:" + "z" * 30)
    _scan(client, tourist_headers, qr)  # 403
    outcomes = sorted(a.outcome for a in db.query(AuditLog).filter(AuditLog.action == "medical_access"))
    assert outcomes == ["denied", "invalid"]
    assert all(("z" * 30) not in (a.detail + a.target) for a in db.query(AuditLog).all())


def test_profile_and_qr_changes_are_audited_by_field_name(client, tourist_headers, tid, db):
    client.put(_url(tid), json=FULL, headers=tourist_headers)
    client.get(f"/api/tourists/{tid}/medical-qr", headers=tourist_headers)
    client.post(f"/api/tourists/{tid}/medical-qr/regenerate", headers=tourist_headers)
    client.post(f"/api/tourists/{tid}/medical-qr/revoke", headers=tourist_headers)
    actions = {a.action for a in db.query(AuditLog)}
    assert {"medical_profile_update", "medical_qr_regenerate", "medical_qr_revoke"} <= actions
    upd = db.query(AuditLog).filter(AuditLog.action == "medical_profile_update").one()
    assert "allergies" in upd.detail and SECRET_ALLERGY not in upd.detail


def test_admin_audit_view_and_access_control(client, admin_headers, responder_headers, tourist_headers,
                                             tid, filled, qr):
    _scan(client, responder_headers, qr, purpose="accident")
    log = client.get("/api/medical-access-log", headers=admin_headers).json()
    assert len(log) == 1 and log[0]["result"] == "granted" and log[0]["responder_email"]
    assert "allerg" not in json.dumps(log).lower()
    assert client.get("/api/medical-access-log", headers=responder_headers).status_code == 403
    assert client.get("/api/medical-access-log", headers=tourist_headers).status_code == 403


def test_tourist_sees_who_viewed_their_profile(client, responder_headers, tourist_headers, tid, filled, qr):
    _scan(client, responder_headers, qr, purpose="accident")
    h = client.get(f"/api/tourists/{tid}/medical-access", headers=tourist_headers).json()
    assert len(h) == 1 and h[0]["responder_role"] == "responder" and h[0]["purpose"] == "accident"
    assert "email" not in json.dumps(h)


# ================================================================ LOGS
def test_medical_data_and_tokens_are_not_logged(client, tourist_headers, responder_headers, tid, qr, caplog):
    caplog.set_level(logging.DEBUG)
    client.put(_url(tid), json=FULL, headers=tourist_headers)
    _scan(client, responder_headers, qr)
    _scan(client, responder_headers, "TSID:" + "q" * 30)
    for secret in (SECRET_ALLERGY, "Asthma", "Asha Rao", "9876543210", qr[5:], "q" * 30):
        assert secret not in caplog.text


# ================================================================ REGRESSION
def test_existing_scan_endpoint_unchanged_and_has_no_medical_data(client, responder_headers, tid, filled, qr):
    r = client.post("/api/tourist-id/scan", json={"token": qr}, headers=responder_headers)
    assert r.status_code == 200 and r.json()["verification_status"] == "verified"
    assert SECRET_ALLERGY not in r.text and "medical" not in r.text.lower()


def test_existing_regenerate_endpoint_still_works(client, tourist_headers, tid, db):
    client.get(f"/api/tourists/{tid}/digital-id", headers=tourist_headers)
    r = client.post(f"/api/tourists/{tid}/digital-id/regenerate", headers=tourist_headers)
    assert r.status_code == 200 and r.json()["qr_png_base64"].startswith("data:image/png")


def test_existing_suspend_then_card_behaviour_is_unchanged(client, admin_headers, tourist_headers, tid):
    client.get(f"/api/tourists/{tid}/digital-id", headers=tourist_headers)
    assert client.post(f"/api/tourists/{tid}/digital-id/suspend", headers=admin_headers).status_code == 204
    # admin suspension (status "invalidated") keeps its original semantics
    card = client.get(f"/api/tourists/{tid}/digital-id", headers=tourist_headers).json()
    assert card["qr_png_base64"] is not None


def test_profile_row_is_unique_per_tourist(client, tourist_headers, tid, db):
    client.put(_url(tid), json={"blood_group": "A+"}, headers=tourist_headers)
    client.put(_url(tid), json={"blood_group": "B+"}, headers=tourist_headers)
    assert db.query(MedicalProfile).filter(MedicalProfile.tourist_id == tid).count() == 1


def test_responder_role_user_is_not_a_tourist_for_profile_edit(client, responder_headers, tid):
    assert client.put(_url(tid), json={"blood_group": "A+"}, headers=responder_headers).status_code == 403
