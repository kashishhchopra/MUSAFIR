"""Complete account deletion.

Flow (each status is real, nothing is reported "done" early):
  awaiting_confirmation -> requested -> in_progress -> completed
                                                    -> partially_retained
                                                    -> failed (retried by the scheduler)
  A request can be cancelled until processing starts.

What happens to each kind of data is decided by the registry plus the live
retention policy (`account_deletion_action`) -- not hard-coded here:
  delete     the rows are removed
  anonymize  the link to the person is cut and free text is blanked
  retain     kept, restricted, with the reason recorded (never silently)
Always retained, regardless of policy: an emergency that is still open, an
unresolved privacy grievance, and anything under an active retention exception.
Retained items are re-examined by every scheduler tick and erased as soon as
the reason ends.

Nothing here can erase data we do not control: copies in database backups and
in external processors are reported honestly in the request (see
`PRIVACY_BACKUP_NOTE` / `PRIVACY_EXTERNAL_PROCESSORS`).
"""
from __future__ import annotations

import json
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_password
from app.core.time import utc_now
from app.models.device import Device
from app.models.emergency_location import EmergencyLocationPing
from app.models.guardian import TripGuardian
from app.models.incident import Incident, IncidentEvent
from app.models.notification import PushSubscription
from app.models.privacy import AccountDeletionRequest, PrivacyGrievance
from app.models.tourist import Tourist
from app.models.user import User
from app.services import audit, privacy_export, privacy_retention
from app.services.privacy_registry import SOURCES, external_processors, subject_ref

logger = get_logger(__name__)
LIVE = ("awaiting_confirmation", "requested", "in_progress", "failed")
BLOCKING = ("requested", "in_progress", "failed")
CONFIRM_PHRASE = "DELETE"
STALE_IN_PROGRESS = timedelta(minutes=15)


class DeletionError(ValueError):
    pass


def _reauth(user: User, password: str) -> None:
    if not password or not verify_password(password, user.hashed_password):
        raise DeletionError("Password is incorrect.")


def _active_request(db: Session, tourist_id: int) -> AccountDeletionRequest | None:
    return db.query(AccountDeletionRequest).filter(
        AccountDeletionRequest.tourist_id == tourist_id,
        AccountDeletionRequest.status.in_(LIVE + ("partially_retained",)),
    ).order_by(AccountDeletionRequest.id.desc()).first()


def login_blocked(db: Session, user: User) -> bool:
    """Used by login: a confirmed deletion must not be undone by a new session."""
    if user.tourist_id is None:
        return False
    return db.query(AccountDeletionRequest.id).filter(
        AccountDeletionRequest.tourist_id == user.tourist_id,
        AccountDeletionRequest.status.in_(BLOCKING)).first() is not None


# ------------------------------------------------------------------ decisions
def _action_for(db: Session, tourist: Tourist, source) -> tuple[str, str]:
    """(action, reason). Open emergencies / holds override the policy."""
    pol = privacy_retention.get_policy(db, source.category)
    action = pol.account_deletion_action if pol else source.default_action
    if privacy_retention.category_held(db, source.category) or \
            tourist.id in privacy_retention.held_tourist_ids(db, source.category):
        return "retain", "Under an active retention exception"
    if action == "retain":
        return "retain", (pol.note or pol.legal_basis) if pol else "Retained by policy"
    return action, ""


def _open_incident_ids(db: Session, tourist_id: int):
    return db.query(Incident.id).filter(Incident.tourist_id == tourist_id, Incident.status != "resolved")


def _resolved_incident_ids(db: Session, tourist_id: int):
    return db.query(Incident.id).filter(Incident.tourist_id == tourist_id, Incident.status == "resolved")


def _scoped_query(db: Session, tourist: Tourist, user: User | None, source):
    """The rows of `source` that erasure is allowed to act on right now."""
    q = source.query(db, tourist, user)
    k = source.key
    if k == "sos_incidents":
        return q.filter(Incident.status == "resolved")
    if k == "emergency_location_trail":
        return q.filter(EmergencyLocationPing.incident_id.in_(_resolved_incident_ids(db, tourist.id)))
    if k == "incident_events":
        return q.filter(IncidentEvent.incident_id.in_(_resolved_incident_ids(db, tourist.id)))
    if k == "privacy_grievances":
        return q.filter(PrivacyGrievance.status.in_(("resolved", "rejected")))
    return q


def _held_count(db: Session, tourist: Tourist, user: User | None, source) -> int:
    """Rows that must stay for a reason other than the policy action."""
    q = source.query(db, tourist, user)
    scoped = _scoped_query(db, tourist, user, source)
    return max(q.count() - scoped.count(), 0)


def preview(db: Session, tourist: Tourist, user: User | None) -> dict:
    """What deleting the account would do, before the person confirms."""
    erased, retained = [], []
    for s in SOURCES:
        total = s.query(db, tourist, user).count()
        if not total:
            continue
        action, reason = _action_for(db, tourist, s)
        held = _held_count(db, tourist, user, s)
        movable = total - held
        if action == "retain":
            retained.append({"category": s.category, "item": s.title, "count": total,
                             "reason": reason, "restricted_access": "administrators only"})
            continue
        erased.append({"item": s.title, "count": movable,
                       "result": "deleted" if action == "delete" or s.anonymizer is None
                       else "personal details removed"})
        if held:
            retained.append({"category": s.category, "item": s.title, "count": held,
                             "reason": "An unresolved emergency or complaint still needs it",
                             "restricted_access": "administrators only"})
    return {
        "will_be_erased": erased, "will_be_retained": retained,
        "also": ["Your login and all sessions", "Your profile, photo and ID document details"],
        "backups": settings.PRIVACY_BACKUP_NOTE,
        "backup_rotation_days": settings.PRIVACY_BACKUP_ROTATION_DAYS or None,
        "external_processors": external_processors(),
        "grace_hours": settings.PRIVACY_DELETION_GRACE_HOURS,
        "confirm_phrase": CONFIRM_PHRASE,
    }


# ------------------------------------------------------------------- request
def start_request(db: Session, tourist: Tourist, user: User, password: str) -> AccountDeletionRequest:
    _reauth(user, password)
    existing = _active_request(db, tourist.id)
    if existing is not None:
        return existing                       # repeated requests are safe
    req = AccountDeletionRequest(
        request_id="DEL-" + secrets.token_hex(12).upper(), tourist_id=tourist.id,
        user_id=user.id, subject_ref=subject_ref(tourist.id), status="awaiting_confirmation")
    db.add(req)
    db.commit()
    audit.record(db, "account_deletion.requested", actor=f"subject:{req.subject_ref}",
                 target=req.request_id)
    return req


def confirm(db: Session, tourist: Tourist, user: User, request_id: str, password: str,
            phrase: str) -> AccountDeletionRequest:
    _reauth(user, password)
    if (phrase or "").strip() != CONFIRM_PHRASE:
        raise DeletionError(f'Type {CONFIRM_PHRASE} to confirm.')
    req = db.query(AccountDeletionRequest).filter(
        AccountDeletionRequest.request_id == request_id,
        AccountDeletionRequest.tourist_id == tourist.id).first()
    if req is None:
        raise LookupError("Deletion request not found")
    if req.status != "awaiting_confirmation":
        return req                            # already confirmed / running: idempotent
    now = utc_now()
    req.status, req.confirmed_at = "requested", now
    req.process_after = now + timedelta(hours=settings.PRIVACY_DELETION_GRACE_HOURS)
    # Immediately: end every session, stop optional collection, cut guardian
    # sharing, so nothing new is gathered while the request is queued.
    user.sessions_valid_from = now + timedelta(seconds=2)
    tourist.tracking_enabled = False
    db.query(TripGuardian).filter(TripGuardian.tourist_id == tourist.id).update(
        {TripGuardian.revoked: True}, synchronize_session=False)
    db.query(Device).filter(Device.tourist_id == tourist.id).update(
        {Device.active: False}, synchronize_session=False)
    db.query(PushSubscription).filter(PushSubscription.user_id == user.id).update(
        {PushSubscription.active: False}, synchronize_session=False)
    db.commit()
    audit.record(db, "account_deletion.confirmed", actor=f"subject:{req.subject_ref}",
                 target=req.request_id)
    return req


def cancel(db: Session, tourist: Tourist, request_id: str) -> AccountDeletionRequest:
    req = db.query(AccountDeletionRequest).filter(
        AccountDeletionRequest.request_id == request_id,
        AccountDeletionRequest.tourist_id == tourist.id).first()
    if req is None:
        raise LookupError("Deletion request not found")
    if req.status not in ("awaiting_confirmation", "requested"):
        raise DeletionError("Deletion has already started and cannot be cancelled.")
    req.status = "cancelled"
    db.commit()
    return req


# ---------------------------------------------------------------- processing
def _step(req: AccountDeletionRequest, name: str, status: str, count: int = 0, note: str = "") -> None:
    steps = [s for s in json.loads(req.steps or "[]") if s["step"] != name]
    steps.append({"step": name, "status": status, "count": count, **({"note": note} if note else {})})
    req.steps = json.dumps(steps)


def _erase_source(db: Session, tourist: Tourist, user: User | None, s) -> tuple[int, int, str]:
    """(erased, retained, reason) for one registry source."""
    if s.key == "export_requests":
        return privacy_export.delete_all_for_tourist(db, tourist.id), 0, ""
    action, reason = _action_for(db, tourist, s)
    total = s.query(db, tourist, user).count()
    if total == 0:
        return 0, 0, ""
    if action == "retain":
        return 0, total, reason
    scoped = _scoped_query(db, tourist, user, s)
    movable = scoped.count()
    held = total - movable
    if s.key == "trip_guardians":      # their push subscriptions point at the guardian rows
        gids = [g for (g,) in db.query(TripGuardian.id).filter(TripGuardian.tourist_id == tourist.id)]
        if gids:
            db.query(PushSubscription).filter(PushSubscription.guardian_id.in_(gids)).delete(
                synchronize_session=False)
    # An incident row is referenced by other records (transfers, escalations), so
    # "delete" for it means "de-identify" -- the link to the person is what goes.
    if action == "anonymize" or s.key == "sos_incidents":
        n = s.anonymizer(db, scoped, tourist, user) if s.anonymizer else scoped.delete(synchronize_session=False)
    else:
        n = scoped.delete(synchronize_session=False)
    db.commit()
    return n, held, ("An unresolved emergency or complaint still needs it" if held else "")


def _finalise_person(db: Session, tourist: Tourist, user: User | None, ref: str,
                     has_retained: bool) -> str:
    """Remove the login and the profile. If retained records still point at the
    tourist row it is de-identified instead of deleted."""
    if user is not None:
        db.query(PushSubscription).filter(PushSubscription.user_id == user.id).delete(
            synchronize_session=False)
        db.delete(user)
        db.flush()
    still_linked = has_retained or db.query(Incident.id).filter(
        Incident.tourist_id == tourist.id).first() is not None
    if still_linked:
        tourist.full_name, tourist.document_number, tourist.phone = "Deleted user", "", ""
        tourist.photo = tourist.hotel = tourist.visa_number = tourist.duress_pin_hash = None
        tourist.visa_type = tourist.visa_expiry = tourist.passport_expiry = None
        tourist.itinerary = tourist.emergency_contacts = tourist.planned_states = "[]"
        tourist.last_lat = tourist.last_lng = tourist.last_seen = None
        tourist.tracking_enabled, tourist.status = False, "deleted"
        tourist.nationality, tourist.nationality_code = "", None
        tourist.digital_id = f"DELETED-{ref}"
        db.commit()
        return "anonymized"
    db.delete(tourist)
    db.commit()
    return "deleted"


def process_request(db: Session, req_id: int) -> AccountDeletionRequest:
    """Run (or resume) a confirmed request. Safe to call repeatedly."""
    req = db.get(AccountDeletionRequest, req_id)
    if req is None or req.status in ("completed", "cancelled", "awaiting_confirmation"):
        return req
    ref = req.subject_ref
    req.status = "in_progress"
    req.started_at = req.started_at or utc_now()
    req.attempts += 1
    req.failure_reason = None
    db.commit()
    tourist = db.get(Tourist, req.tourist_id) if req.tourist_id else None
    user = db.query(User).filter(User.tourist_id == req.tourist_id).first() if req.tourist_id else None
    erased_total = retained_total = 0
    retained: list[dict] = []
    try:
        if tourist is not None:
            if user is not None:
                user.sessions_valid_from = utc_now() + timedelta(seconds=2)
            tourist.tracking_enabled = False
            db.commit()
            _step(req, "stop_collection_and_revoke_sessions", "done")
            for s in SOURCES:
                n, kept, reason = _erase_source(db, tourist, user, s)
                erased_total += n
                retained_total += kept
                if kept:
                    pol = privacy_retention.get_policy(db, s.category)
                    retained.append({"category": s.category, "item": s.title, "count": kept,
                                     "reason": reason, "basis": pol.legal_basis if pol else "",
                                     "access": "administrators only"})
                _step(req, s.key, "retained" if kept and not n else "done", n)
                db.commit()
            outcome = _finalise_person(db, tourist, user, ref, bool(retained))
            _step(req, "profile_and_login", outcome, 1)
            if outcome == "deleted":
                req.tourist_id = None
        elif user is not None:                # profile gone but the login lingered
            db.delete(user)
            db.commit()
        req.user_id = None
        req.processors = json.dumps([
            {"name": p["name"], "contact": p.get("contact"), "status": "manual_notification_required"}
            for p in external_processors()])
        req.retained = json.dumps(retained)
        req.status = "partially_retained" if retained else "completed"
        if not retained:
            req.completed_at = utc_now()
        privacy_retention.record_job(
            db, "account_deletion", job_type="account_deletion", processed=erased_total + retained_total,
            deleted=erased_total, retained=retained_total, subject_ref=ref, attempts=req.attempts)
        db.commit()
        audit.record(db, f"account_deletion.{req.status}", actor=f"subject:{ref}",
                     target=req.request_id, detail=f"erased={erased_total} retained={retained_total}")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        req = db.get(AccountDeletionRequest, req_id)
        req.status, req.failure_reason = "failed", type(exc).__name__ + ": " + str(exc)[:300]
        db.commit()
        logger.error("account_deletion_failed", request_id=req.request_id, error=str(exc))
    return req


def process_due(db: Session) -> list[str]:
    """Scheduler: start requests whose grace period has passed, retry failed
    ones (bounded), resume stuck ones, and re-examine retained records."""
    now = utc_now()
    ids: list[int] = []
    for r in db.query(AccountDeletionRequest).filter(
            AccountDeletionRequest.status.in_(("requested", "in_progress", "failed",
                                               "partially_retained"))).all():
        if r.status == "requested" and (r.process_after is None or r.process_after <= now):
            ids.append(r.id)
        elif r.status == "failed" and r.attempts < settings.PRIVACY_DELETION_MAX_ATTEMPTS:
            ids.append(r.id)
        elif r.status == "in_progress" and r.started_at and now - r.started_at > STALE_IN_PROGRESS:
            ids.append(r.id)
        elif r.status == "partially_retained":
            ids.append(r.id)
    done = []
    for rid in ids:
        r = process_request(db, rid)
        if r is not None:
            done.append(f"{r.request_id}:{r.status}")
    return done


# -------------------------------------------------------------------- status
LABELS = {
    "awaiting_confirmation": "Deletion requested - awaiting your confirmation",
    "requested": "Deletion requested",
    "in_progress": "Deletion in progress",
    "completed": "Deletion completed",
    "partially_retained": "Partially retained under a documented exception",
    "failed": "Deletion failed - requires attention",
    "cancelled": "Deletion cancelled",
}


def status_out(req: AccountDeletionRequest) -> dict:
    return {
        "request_id": req.request_id, "status": req.status, "label": LABELS.get(req.status, req.status),
        "requested_at": req.requested_at, "confirmed_at": req.confirmed_at,
        "process_after": req.process_after, "started_at": req.started_at,
        "completed_at": req.completed_at, "attempts": req.attempts,
        "failure_reason": req.failure_reason if req.status == "failed" else None,
        "steps": json.loads(req.steps or "[]"),
        "retained": json.loads(req.retained or "[]"),
        "external_processors": json.loads(req.processors or "[]"),
        "backups": settings.PRIVACY_BACKUP_NOTE,
        "backup_rotation_days": settings.PRIVACY_BACKUP_ROTATION_DAYS or None,
    }


def latest_for_tourist(db: Session, tourist_id: int) -> AccountDeletionRequest | None:
    return db.query(AccountDeletionRequest).filter(
        AccountDeletionRequest.tourist_id == tourist_id
    ).order_by(AccountDeletionRequest.id.desc()).first()


def by_request_id(db: Session, request_id: str) -> AccountDeletionRequest | None:
    return db.query(AccountDeletionRequest).filter(
        AccountDeletionRequest.request_id == request_id).first()
