"""Purpose-specific consent: a append-only ledger plus the enforcement hooks
that make withdrawal actually stop the processing it covered.

Consent is NOT treated as the basis for everything. Each purpose in
`settings.PRIVACY_PURPOSES` is either `kind: "consent"` (the person can grant
and withdraw it) or `kind: "notice"` (required for the service, or relying on a
different basis that legal review must confirm -- shown, never pre-ticked, and
not pretended to be withdrawable).
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.guardian import TripGuardian
from app.models.notification import PushSubscription
from app.models.privacy import ConsentRecord
from app.models.tourist import Tourist
from app.models.user import User
from app.services.privacy_registry import new_public_id, subject_ref

# Recorded like consents (so there is proof), but not withdrawable.
ACK_PURPOSES = {
    "privacy_notice_ack": "Read the privacy notice",
    "adult_declaration": "Declared to be 18 or older",
}


class ConsentError(ValueError):
    pass


def purposes() -> list[dict]:
    try:
        return [p for p in json.loads(settings.PRIVACY_PURPOSES or "[]") if p.get("key")]
    except (ValueError, TypeError):
        return []


def purpose_map() -> dict[str, dict]:
    return {p["key"]: p for p in purposes()}


def consent_purpose_keys() -> list[str]:
    return [p["key"] for p in purposes() if p.get("kind") == "consent"]


def notice() -> dict:
    return {
        "version": settings.PRIVACY_NOTICE_VERSION,
        "updated": settings.PRIVACY_NOTICE_UPDATED,
        "purposes": purposes(),
        "legal_note": settings.PRIVACY_LEGAL_NOTE,
        "contact_email": settings.PRIVACY_CONTACT_EMAIL or None,
        "rights": [
            "See what we hold about you (Download my data).",
            "Withdraw any consent at any time -- as easily as you gave it.",
            "Delete your location history, or your whole account.",
            "Raise a privacy complaint and track its outcome.",
        ],
    }


def _out(r: ConsentRecord) -> dict:
    return {
        "consent_id": r.consent_id, "purpose": r.purpose, "status": r.status,
        "consent_version": r.consent_version, "notice_version": r.notice_version,
        "consented_at": r.consented_at, "withdrawn_at": r.withdrawn_at, "source": r.source,
        "data_categories": json.loads(r.data_categories or "[]"),
    }


def _records(db: Session, ref: str, purpose: str | None = None):
    q = db.query(ConsentRecord).filter(ConsentRecord.subject_ref == ref)
    if purpose:
        q = q.filter(ConsentRecord.purpose == purpose)
    return q.order_by(ConsentRecord.id.desc())


def _latest(db: Session, ref: str, purpose: str) -> ConsentRecord | None:
    return _records(db, ref, purpose).first()


def is_withdrawn(db: Session, tourist_id: int, purpose: str) -> bool:
    """True only when the person has explicitly withdrawn. A tourist with no
    record at all (registered before this feature) is not treated as withdrawn."""
    last = _latest(db, subject_ref(tourist_id), purpose)
    return last is not None and last.status == "withdrawn"


def status_overview(db: Session, tourist: Tourist) -> list[dict]:
    ref = subject_ref(tourist.id)
    out = []
    for p in purposes():
        last = _latest(db, ref, p["key"])
        state = "not_recorded" if last is None else last.status
        out.append({
            **{k: p.get(k) for k in ("key", "title", "kind", "description", "data_categories",
                                     "shared_with", "basis_note", "withdraw_effect")},
            "status": state,
            "consent_id": last.consent_id if last else None,
            "consented_at": last.consented_at if last else None,
            "withdrawn_at": last.withdrawn_at if last else None,
            "notice_version": last.notice_version if last else None,
            # A newer notice than the one this consent was given under -> ask again.
            "needs_review": bool(last and last.status == "granted"
                                 and last.notice_version != settings.PRIVACY_NOTICE_VERSION),
        })
    return out


def history(db: Session, tourist: Tourist) -> list[dict]:
    return [_out(r) for r in _records(db, subject_ref(tourist.id)).all()]


def _categories(purpose: str) -> list[str]:
    p = purpose_map().get(purpose)
    return list(p.get("data_categories", [])) if p else []


def _check_purpose(purpose: str, *, withdraw: bool = False) -> None:
    if purpose in ACK_PURPOSES:
        if withdraw:
            raise ConsentError("This acknowledgement cannot be withdrawn.")
        return
    p = purpose_map().get(purpose)
    if p is None:
        raise ConsentError("Unknown consent purpose.")
    if p.get("kind") != "consent":
        raise ConsentError(
            "This purpose is not based on consent and cannot be withdrawn here. "
            "Delete your account if you no longer want it processed.")


def grant(db: Session, tourist: Tourist, purpose: str, *, source: str = "privacy_dashboard",
          evidence: dict | None = None, commit: bool = True) -> ConsentRecord:
    _check_purpose(purpose)
    ref = subject_ref(tourist.id)
    last = _latest(db, ref, purpose)
    if last is not None and last.status == "granted" \
            and last.notice_version == settings.PRIVACY_NOTICE_VERSION:
        return last                      # idempotent
    if last is not None and last.status == "granted":
        last.status = "superseded"       # re-consent to a newer notice: close the old row
        last.withdrawn_at = utc_now()
    count = _records(db, ref, purpose).count()
    rec = ConsentRecord(
        consent_id=new_public_id("CNS"), tourist_id=tourist.id, subject_ref=ref,
        purpose=purpose, data_categories=json.dumps(_categories(purpose)),
        status="granted", consent_version=count + 1,
        notice_version=settings.PRIVACY_NOTICE_VERSION, consented_at=utc_now(),
        source=source, evidence=json.dumps(evidence or {"action": "explicit_opt_in"}),
    )
    db.add(rec)
    _apply_effects(db, tourist, purpose, granted=True)
    if commit:
        db.commit()
        db.refresh(rec)
    return rec


def withdraw(db: Session, tourist: Tourist, purpose: str, *, source: str = "privacy_dashboard",
             commit: bool = True) -> tuple[ConsentRecord, list[str]]:
    _check_purpose(purpose, withdraw=True)
    ref = subject_ref(tourist.id)
    last = _latest(db, ref, purpose)
    now = utc_now()
    if last is not None and last.status == "withdrawn":
        return last, []                  # idempotent
    if last is not None and last.status == "granted":
        last.status = "withdrawn"
        last.withdrawn_at = now
        rec = last
    else:                                # never recorded: make the withdrawal explicit
        rec = ConsentRecord(
            consent_id=new_public_id("CNS"), tourist_id=tourist.id, subject_ref=ref,
            purpose=purpose, data_categories=json.dumps(_categories(purpose)),
            status="withdrawn", consent_version=_records(db, ref, purpose).count() + 1,
            notice_version=settings.PRIVACY_NOTICE_VERSION, consented_at=now, withdrawn_at=now,
            source=source, evidence=json.dumps({"action": "explicit_withdrawal"}),
        )
        db.add(rec)
    effects = _apply_effects(db, tourist, purpose, granted=False)
    if commit:
        db.commit()
        db.refresh(rec)
    return rec, effects


def _apply_effects(db: Session, tourist: Tourist, purpose: str, *, granted: bool) -> list[str]:
    """Make the rest of the system respect the decision. Returns what changed."""
    effects: list[str] = []
    if purpose == "location_tracking":
        if tourist.tracking_enabled != granted:
            tourist.tracking_enabled = granted
        effects.append("location_collection_started" if granted else "location_collection_stopped")
    elif purpose == "guardian_sharing" and not granted:
        n = db.query(TripGuardian).filter(
            TripGuardian.tourist_id == tourist.id, TripGuardian.revoked.is_(False)
        ).update({TripGuardian.revoked: True}, synchronize_session=False)
        if n:
            effects.append(f"guardian_links_revoked:{n}")
    elif purpose == "notifications" and not granted:
        user = db.query(User).filter(User.tourist_id == tourist.id).first()
        if user:
            n = db.query(PushSubscription).filter(
                PushSubscription.user_id == user.id, PushSubscription.active.is_(True)
            ).update({PushSubscription.active: False}, synchronize_session=False)
            if n:
                effects.append(f"push_subscriptions_deactivated:{n}")
    return effects


def sync_tracking(db: Session, tourist: Tourist, enabled: bool) -> None:
    """The pre-existing tracking switch (`POST /tourists/{id}/tracking`) is the
    same decision as the location consent -- keep the ledger truthful. Does not
    commit; the caller does. Never raises into the request."""
    try:
        ref = subject_ref(tourist.id)
        last = _latest(db, ref, "location_tracking")
        now_granted = last is not None and last.status == "granted"
        if enabled and not now_granted:
            grant(db, tourist, "location_tracking", source="tracking_toggle", commit=False,
                  evidence={"action": "tracking_switch_on"})
        elif not enabled and (now_granted or last is None):   # legacy user: tracking was on without a record
            withdraw(db, tourist, "location_tracking", source="tracking_toggle", commit=False)
    except Exception:  # noqa: BLE001 - ledger is best-effort here, the switch itself must still work
        pass


def record_registration(db: Session, tourist: Tourist, choices: list[dict]) -> None:
    """Persist what the person explicitly ticked at sign-up. Only `granted: True`
    entries create a record; an unticked box leaves no consent behind."""
    ticked = set()
    for c in choices:
        if not c.get("granted"):
            continue
        ticked.add(c["purpose"])
        grant(db, tourist, c["purpose"], source="registration", commit=False,
              evidence={"action": "checkbox_ticked", "screen": "registration"})
    # The person saw the choice and did not tick location: do not start tracking them.
    if "location_tracking" not in ticked:
        tourist.tracking_enabled = False


def missing_required(choices: list[dict]) -> list[str]:
    """Which acknowledgement purposes a registration must carry when
    PRIVACY_REGISTRATION_CONSENT_REQUIRED is on."""
    given = {c["purpose"] for c in choices if c.get("granted")}
    return [k for k in ACK_PURPOSES if k not in given]


def validate_choices(choices: list[dict]) -> None:
    for c in choices:
        _check_purpose(c["purpose"])
    if settings.PRIVACY_REGISTRATION_CONSENT_REQUIRED:
        miss = missing_required(choices)
        if miss:
            raise ConsentError(f"Required acknowledgements missing: {', '.join(miss)}")
