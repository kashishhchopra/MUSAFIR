"""Emergency Medical Profile: tourist-managed medical facts, and the
authorized, audited, rate-limited path by which a responder reads them after
scanning the tourist's QR.

    QR code ("TSID:<token>")  ->  SHA-256 hash lookup  ->  TouristIdToken
        ->  tourist  ->  responder role check  ->  MedicalProfile

Nothing medical is ever placed in the QR. The QR is the *existing* Digital
Tourist Safety ID token (services/tourist_id.py) -- one credential, not a
second one -- and possessing its image grants nothing: every read needs a
logged-in admin/responder, is rate-limited, and is audited (success or not).

Privacy rules enforced here:
  * clinical columns are encrypted at rest (models/medical_profile.py);
  * all content is labelled tourist-reported -- never "verified";
  * a missing field is "not provided"; "no known allergies" is a separate,
    explicit statement and the two are never conflated;
  * audit rows record *which sections* were disclosed, never their contents;
  * tokens and medical text are never written to application logs.
"""
from __future__ import annotations

import json
import re

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import settings
from app.core.logging import current_request_id
from app.core.time import utc_now
from app.models.medical_profile import MedicalProfile
from app.models.tourist import Tourist
from app.models.tourist_id import TouristIdScan, TouristIdToken
from app.models.user import User
from app.schemas.medical_profile import (
    BLOOD_GROUPS,
    CLEARABLE_FIELDS,
    MedicalProfileUpdate,
    access_purposes,
    suggested_conditions,
)
from app.services import audit
from app.services import tourist_id as tourist_id_service

METHOD = "medical_qr"
# Matches secrets.token_urlsafe output; anything else cannot be a real token and
# is rejected before touching the database.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_LIST_FIELDS = {  # profile field -> status column
    "allergies": "allergies_status",
    "medical_conditions": "conditions_status",
    "medications": "medications_status",
}
# Sections disclosed to a responder (names only go to the audit trail).
_SECTIONS = ("identity", "blood_group", "allergies", "medical_conditions", "medications",
             "emergency_contacts", "additional_notes")


# ----------------------------------------------------------------- helpers
def _loads(raw: str | None) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except ValueError:
        return []


def _row(db: Session, tourist_id: int) -> MedicalProfile | None:
    return db.query(MedicalProfile).filter(MedicalProfile.tourist_id == tourist_id).first()


def _ip(request: Request | None) -> str:
    if request is None:
        return ""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def options() -> dict:
    """Form options and limits, served to the UI so it never hard-codes them."""
    return {
        "blood_groups": list(BLOOD_GROUPS),
        "allergy_categories": ["food", "medicine", "other"],
        "suggested_conditions": suggested_conditions(),
        "access_purposes": access_purposes(),
        "access_purpose_required": settings.MEDICAL_ACCESS_PURPOSE_REQUIRED,
        "limits": {
            "allergies": settings.MEDICAL_MAX_ALLERGIES,
            "medical_conditions": settings.MEDICAL_MAX_CONDITIONS,
            "medications": settings.MEDICAL_MAX_MEDICATIONS,
            "emergency_contacts": 2,
            "notes_chars": settings.MEDICAL_NOTES_MAX_CHARS,
        },
    }


# ------------------------------------------------------- profile (owner side)
def _serialize(row: MedicalProfile | None) -> dict:
    """The shape shared by the owner view and the responder view."""
    if row is None:
        return {
            "exists": False, "blood_group": None,
            "allergies_status": "not_provided", "allergies": [],
            "conditions_status": "not_provided", "medical_conditions": [],
            "medications_status": "not_provided", "medications": [],
            "emergency_contacts": [], "additional_notes": None,
            "profile_updated_at": None,
        }
    return {
        "exists": True,
        "blood_group": row.blood_group,
        "allergies_status": row.allergies_status,
        "allergies": _loads(row.allergies),
        "conditions_status": row.conditions_status,
        "medical_conditions": _loads(row.medical_conditions),
        "medications_status": row.medications_status,
        "medications": _loads(row.medications),
        "emergency_contacts": _loads(row.emergency_contacts),
        "additional_notes": row.additional_notes,
        "profile_updated_at": row.profile_updated_at,
    }


def completion(data: dict) -> dict:
    """Which optional sections the tourist has addressed. Informational only --
    nothing is ever required, and "none known" counts as an answer."""
    done = {
        "blood_group": bool(data["blood_group"]),
        "allergies": data["allergies_status"] != "not_provided",
        "medical_conditions": data["conditions_status"] != "not_provided",
        "medications": data["medications_status"] != "not_provided",
        "emergency_contacts": bool(data["emergency_contacts"]),
    }
    n = sum(done.values())
    return {"sections": done, "completed": n, "total": len(done),
            "percent": round(100 * n / len(done))}


def get_own(db: Session, tourist: Tourist) -> dict:
    data = _serialize(_row(db, tourist.id))
    data["completion"] = completion(data)
    return data


def _check_list_state(field: str, status: str | None, items: list | None, current_status: str):
    """Reconcile a list field with its status. Returns (status, items|None)."""
    if items is not None and status is None:
        status = "listed" if items else "not_provided"
    if status is None:
        return current_status, items
    if status == "listed":
        if items is not None and not items:
            return "not_provided", []
        return status, items
    # none_known / not_provided can never coexist with entries
    if items:
        raise HTTPException(
            status_code=422,
            detail=f"{field}: entries cannot be combined with '{status.replace('_', ' ')}'",
        )
    return status, []


def update(db: Session, tourist: Tourist, payload: MedicalProfileUpdate,
           actor: User, request: Request | None = None) -> dict:
    """Create-or-update (partial) the tourist's own profile."""
    data = payload.model_dump(exclude_unset=True)
    row = _row(db, tourist.id)
    created = row is None
    if created:
        now = utc_now()
        row = MedicalProfile(tourist_id=tourist.id, created_at=now, profile_updated_at=now)
        db.add(row)
    changed: list[str] = []

    if "blood_group" in data:
        row.blood_group = data["blood_group"]
        changed.append("blood_group")
    for field, status_col in _LIST_FIELDS.items():  # payload keys == column names
        if field in data or status_col in data:
            status, items = _check_list_state(
                field, data.get(status_col), data.get(field), getattr(row, status_col))
            setattr(row, status_col, status)
            if items is not None:
                setattr(row, field, json.dumps(items))
            changed.append(field)
    if "emergency_contacts" in data:
        row.emergency_contacts = json.dumps(data["emergency_contacts"])
        changed.append("emergency_contacts")
    if "additional_notes" in data:
        row.additional_notes = data["additional_notes"]
        changed.append("additional_notes")

    if changed:
        row.profile_updated_at = utc_now()
    db.commit()
    # Field NAMES only -- never the medical content.
    audit.record(db, action="medical_profile_update", actor=actor.email, target=tourist.digital_id,
                 detail=json.dumps({"fields": changed, "created": created}), request=request)
    return get_own(db, tourist)


def clear_field(db: Session, tourist: Tourist, field: str, actor: User,
                request: Request | None = None) -> dict:
    """Delete one whole section (back to 'not provided')."""
    if field not in CLEARABLE_FIELDS:
        raise HTTPException(status_code=404, detail="Unknown medical profile field")
    row = _row(db, tourist.id)
    if row is None:
        return get_own(db, tourist)
    if field == "blood_group":
        row.blood_group = None
    elif field in _LIST_FIELDS:
        setattr(row, field, "[]")
        setattr(row, _LIST_FIELDS[field], "not_provided")
    elif field == "emergency_contacts":
        row.emergency_contacts = "[]"
    else:
        row.additional_notes = None
    row.profile_updated_at = utc_now()
    db.commit()
    audit.record(db, action="medical_profile_clear", actor=actor.email, target=tourist.digital_id,
                 detail=json.dumps({"field": field}), request=request)
    return get_own(db, tourist)


# ------------------------------------------------------------ QR (owner side)
def _token_expired(token: TouristIdToken) -> bool:
    return token.expires_at is not None and utc_now() > token.expires_at


def qr_state(db: Session, tourist: Tourist) -> dict:
    """The tourist's QR as the Medical Profile section needs it: the same
    card/QR as the Digital ID, plus revoked/expired state and profile presence."""
    card = tourist_id_service.digital_id_card(db, tourist)
    token = tourist_id_service._current_token(db, tourist.id)
    if token is None:
        state = "revoked"
    elif _token_expired(token):
        state = "expired"
    else:
        state = card["id_status"]
    return {
        "state": state,
        "qr_png_base64": card["qr_png_base64"] if token is not None else None,
        "issued_at": card["issued_at"],
        "expires_at": token.expires_at if token is not None else None,
        "digital_id": tourist.digital_id,
        "trip_end": tourist.trip_end,
        "medical_profile_present": _row(db, tourist.id) is not None,
    }


def regenerate_qr(db: Session, tourist: Tourist, actor: User, request: Request | None = None) -> dict:
    """New token; the old one stops working immediately."""
    tourist_id_service.regenerate_card(db, tourist)
    audit.record(db, action="medical_qr_regenerate", actor=actor.email, target=tourist.digital_id,
                 request=request)
    return qr_state(db, tourist)


def revoke_qr(db: Session, tourist: Tourist, actor: User, request: Request | None = None) -> dict:
    """Disable QR access with no replacement until the tourist regenerates."""
    n = tourist_id_service.revoke_tokens(db, tourist)
    db.commit()
    audit.record(db, action="medical_qr_revoke", actor=actor.email, target=tourist.digital_id,
                 detail=json.dumps({"revoked": n}), request=request)
    return qr_state(db, tourist)


# --------------------------------------------------------- responder access
def _log_access(db: Session, scanner: User, tourist: Tourist | None, result: str,
                sections: list[str], purpose: str | None, lat, lng,
                request: Request | None) -> str:
    rid = current_request_id()
    db.add(TouristIdScan(
        tourist_id=tourist.id if tourist else None, scanner_user_id=scanner.id,
        scanner_role=scanner.role, method=METHOD, scan_lat=lat, scan_lng=lng,
        verification_status=result, accessed_fields=json.dumps(sections),
        purpose=purpose, request_id=rid,
    ))
    db.commit()
    audit.record(
        db, action="medical_access", actor=scanner.email,
        target=tourist.digital_id if tourist else "unknown",
        detail=json.dumps({"sections": sections, "purpose": purpose, "request_id": rid}),
        outcome=result, request=request,
    )
    return rid


def deny_unauthorized(user: User, request: Request | None = None) -> None:
    """Audit an attempt by an account that may not read medical data at all."""
    audit.record(action="medical_access", actor=user.email, target="unknown",
                 detail=json.dumps({"role": user.role}), outcome="denied", request=request)


def access(db: Session, scanner: User, token_raw: str, purpose: str | None = None,
           lat: float | None = None, lng: float | None = None,
           request: Request | None = None) -> dict:
    """Resolve a scanned QR to the tourist's medical profile.

    Caller has already verified the scanner is admin/responder (backend-side).
    Every outcome is audited. Only `granted` ever carries medical data.
    """
    ip = _ip(request)
    # 1) throttle: overall volume, and (separately) failed lookups -- the
    #    brute-force / token-guessing guard.
    ratelimit.check_key(f"medical:{scanner.id}", settings.MEDICAL_ACCESS_RATE_LIMIT,
                        settings.MEDICAL_ACCESS_RATE_WINDOW_SECONDS)
    fail_keys = (f"medical-fail:u{scanner.id}", f"medical-fail:ip{ip}")
    for k in fail_keys:
        if ratelimit.hits_in_window(k, settings.MEDICAL_FAILED_LOOKUP_WINDOW_SECONDS) >= \
                settings.MEDICAL_FAILED_LOOKUP_LIMIT:
            raise HTTPException(status_code=429, detail="Too many failed lookups - try again later.")

    # 2) purpose
    allowed = access_purposes()
    if purpose is not None:
        purpose = purpose.strip() or None
    if purpose is not None and allowed and purpose not in allowed:
        raise HTTPException(status_code=400, detail="Unknown access purpose")
    if settings.MEDICAL_ACCESS_PURPOSE_REQUIRED and not purpose:
        raise HTTPException(status_code=400, detail="An access purpose is required")

    def fail(result: str, reason: str, tourist: Tourist | None = None) -> dict:
        for k in fail_keys:
            ratelimit.add_hit(k)
        rid = _log_access(db, scanner, tourist, result, [], purpose, lat, lng, request)
        return {"status": result, "reason": reason, "request_id": rid}

    # 3) token -> row (hash lookup only; the raw token is never stored/logged)
    raw = token_raw.strip()
    if raw.upper().startswith("TSID:"):
        raw = raw[5:]
    if not _TOKEN_RE.match(raw):
        return fail("invalid", "This is not a valid MUSAFIR tourist QR code.")
    token = (db.query(TouristIdToken)
             .filter(TouristIdToken.token_hash == tourist_id_service._hash(raw)).first())
    if token is None:
        return fail("invalid", "This is not a valid MUSAFIR tourist QR code.")
    tourist = db.get(Tourist, token.tourist_id)
    if tourist is None:
        return fail("invalid", "This is not a valid MUSAFIR tourist QR code.")
    if token.status != "active":
        return fail("revoked", "This QR code was revoked or replaced and is no longer valid.", tourist)
    if _token_expired(token) or tourist_id_service.id_status(tourist, token) == "expired":
        return fail("expired", "This QR code has expired.", tourist)

    # 4) the profile
    row = _row(db, tourist.id)
    identity = {"full_name": tourist.full_name, "digital_id": tourist.digital_id, "photo": tourist.photo}
    if row is None:
        rid = _log_access(db, scanner, tourist, "no_profile", ["identity"], purpose, lat, lng, request)
        return {"status": "no_profile", "tourist": identity, "request_id": rid,
                "reason": "This tourist has not provided a medical profile."}

    data = _serialize(row)
    rid = _log_access(db, scanner, tourist, "granted", list(_SECTIONS), purpose, lat, lng, request)
    return {
        "status": "granted",
        "accessed_at": utc_now(),
        "request_id": rid,
        "purpose": purpose,
        "tourist": identity,
        "medical": {k: v for k, v in data.items() if k != "exists"},
        # Honest provenance: nothing here is medically verified.
        "provenance": {
            "source": "tourist_reported", "verified": False,
            "note": "Reported by the tourist. Not verified by any medical authority.",
        },
        "completion": completion(data),
    }


# ----------------------------------------------------------- admin audit view
def access_log(db: Session, limit: int = 100, result: str | None = None,
               digital_id: str | None = None) -> list[dict]:
    q = (db.query(TouristIdScan, User, Tourist)
         .outerjoin(User, User.id == TouristIdScan.scanner_user_id)
         .outerjoin(Tourist, Tourist.id == TouristIdScan.tourist_id)
         .filter(TouristIdScan.method == METHOD))
    if result:
        q = q.filter(TouristIdScan.verification_status == result)
    if digital_id:
        q = q.filter(Tourist.digital_id == digital_id)
    rows = q.order_by(TouristIdScan.scanned_at.desc(), TouristIdScan.id.desc()).limit(min(limit, 500)).all()
    return [{
        "id": s.id, "scanned_at": s.scanned_at,
        "responder_email": u.email if u else None, "responder_role": s.scanner_role,
        "tourist_digital_id": t.digital_id if t else None,
        "result": s.verification_status, "purpose": s.purpose, "request_id": s.request_id,
    } for s, u, t in rows]


def own_access_history(db: Session, tourist: Tourist, limit: int = 20) -> list[dict]:
    """Who viewed *my* medical profile -- for the owner. Exposes the viewer's
    role and time, not their identity."""
    rows = (db.query(TouristIdScan)
            .filter(TouristIdScan.tourist_id == tourist.id, TouristIdScan.method == METHOD,
                    TouristIdScan.verification_status == "granted")
            .order_by(TouristIdScan.scanned_at.desc(), TouristIdScan.id.desc())
            .limit(min(limit, 100)).all())
    return [{"id": r.id, "scanned_at": r.scanned_at, "responder_role": r.scanner_role,
             "purpose": r.purpose} for r in rows]

