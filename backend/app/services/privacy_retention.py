"""Central data-retention management.

* Policies live in `retention_policies`, seeded from `PRIVACY_RETENTION_DEFAULTS`
  and editable by an administrator. A policy with no `retention_days` means
  "no period established": the engine will never delete under it, and the
  dashboard says so. Nothing here invents a legal period.
* The engine (`run`) is idempotent: each category handler only touches rows
  that are *currently* expired, so running it twice (or after a crash) is safe.
* Exceptions (`retention_exceptions`) and open emergencies stop deletion; each
  is counted as "retained", and every run leaves a `deletion_jobs` row holding
  counts only -- never the data itself.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.audit import AuditLog
from app.models.incident import Incident, IncidentEvent
from app.models.notification import NotificationLog
from app.models.password_reset import PasswordResetToken
from app.models.privacy import DeletionJob, RetentionException, RetentionPolicy
from app.models.revoked_token import RevokedToken
from app.services.privacy_registry import new_public_id

logger = get_logger(__name__)

ACTIONS = ("delete", "anonymize", "retain")
START_CONDITIONS = ("created", "trip_end", "account", "incident_resolved", "token_expiry")


# ------------------------------------------------------------------ policies
def _defaults() -> list[dict]:
    try:
        return [d for d in json.loads(settings.PRIVACY_RETENTION_DEFAULTS or "[]") if d.get("category")]
    except (ValueError, TypeError):
        return []


def seed_policies(db: Session) -> int:
    """Insert any configured category that has no row yet. Never overwrites an
    administrator's edits."""
    have = {c for (c,) in db.query(RetentionPolicy.category).all()}
    added = 0
    for d in _defaults():
        if d["category"] in have:
            continue
        days = d.get("retention_days")
        db.add(RetentionPolicy(
            category=d["category"], title=d.get("title", ""), purpose=d.get("purpose", ""),
            legal_basis=d.get("legal_basis", ""), retention_days=days,
            start_condition=d.get("start", "created"), deletion_action=d.get("action", "delete"),
            account_deletion_action=d.get("account_deletion", "delete"),
            per_tourist=bool(d.get("per_tourist")), is_active=days is not None and d.get("action") != "retain",
            requires_review=bool(d.get("requires_review", True)), note=d.get("note", ""),
        ))
        added += 1
    if added:
        db.commit()
    return added


def list_policies(db: Session) -> list[RetentionPolicy]:
    seed_policies(db)
    return db.query(RetentionPolicy).order_by(RetentionPolicy.category).all()


def get_policy(db: Session, category: str) -> RetentionPolicy | None:
    seed_policies(db)
    return db.query(RetentionPolicy).filter(RetentionPolicy.category == category).first()


def policy_public(p: RetentionPolicy) -> dict:
    if p.deletion_action == "retain":
        state = "Held as a documented exception"
    elif p.is_active and p.retention_days is not None:
        state = "Scheduled deletion on"
    elif p.retention_days is None:
        state = "No period established - not deleted automatically"
    else:
        state = "Scheduled deletion off"
    return {
        "category": p.category, "title": p.title, "purpose": p.purpose,
        "legal_basis": p.legal_basis, "retention_days": p.retention_days,
        "start_condition": p.start_condition, "deletion_action": p.deletion_action,
        "account_deletion_action": p.account_deletion_action, "per_tourist": p.per_tourist,
        "is_active": p.is_active, "requires_review": p.requires_review, "note": p.note,
        "state": state, "automated": p.category in HANDLERS,
        "updated_at": p.updated_at, "last_reviewed_at": p.last_reviewed_at,
    }


def update_policy(db: Session, category: str, changes: dict, actor: str) -> RetentionPolicy:
    p = get_policy(db, category)
    if p is None:
        raise LookupError("Unknown retention category")
    if "retention_days" in changes:
        d = changes["retention_days"]
        if d is not None and d < 0:
            raise ValueError("retention_days cannot be negative")
        p.retention_days = d
    for f in ("deletion_action", "account_deletion_action"):
        if changes.get(f) is not None:
            if changes[f] not in ACTIONS:
                raise ValueError(f"{f} must be one of {', '.join(ACTIONS)}")
            setattr(p, f, changes[f])
    if changes.get("start_condition") is not None:
        if changes["start_condition"] not in START_CONDITIONS:
            raise ValueError("Unknown start_condition")
        p.start_condition = changes["start_condition"]
    for f in ("legal_basis", "purpose", "note", "title"):
        if changes.get(f) is not None:
            setattr(p, f, changes[f])
    if changes.get("is_active") is not None:
        p.is_active = bool(changes["is_active"])
    if changes.get("requires_review") is not None:
        p.requires_review = bool(changes["requires_review"])
        if not p.requires_review:
            p.last_reviewed_at = utc_now()
    if p.is_active and p.retention_days is None:
        raise ValueError("Set retention_days before switching scheduled deletion on.")
    if p.is_active and p.deletion_action == "retain":
        raise ValueError("A 'retain' policy cannot also be scheduled for deletion.")
    p.updated_by, p.updated_at = actor, utc_now()
    db.commit()
    return p


# ---------------------------------------------------------------- exceptions
def exception_out(e: RetentionException) -> dict:
    now = utc_now()
    return {
        "id": e.id, "category": e.category, "tourist_id": e.tourist_id, "reason": e.reason,
        "basis": e.basis, "created_by": e.created_by, "created_at": e.created_at,
        "review_by": e.review_by, "released_at": e.released_at, "active": e.active,
        "review_overdue": bool(e.active and e.review_by and e.review_by < now),
    }


def list_exceptions(db: Session, active_only: bool = False) -> list[RetentionException]:
    q = db.query(RetentionException)
    if active_only:
        q = q.filter(RetentionException.active.is_(True))
    return q.order_by(RetentionException.id.desc()).all()


def add_exception(db: Session, *, category: str, reason: str, basis: str, review_days: int,
                  actor: str, tourist_id: int | None = None) -> RetentionException:
    from app.services.privacy_registry import subject_ref

    if get_policy(db, category) is None:
        raise LookupError("Unknown retention category")
    if not reason.strip() or not basis.strip():
        raise ValueError("A reason and a retention basis are both required.")
    e = RetentionException(
        category=category, reason=reason.strip(), basis=basis.strip(), created_by=actor,
        review_by=utc_now() + timedelta(days=max(1, review_days)), tourist_id=tourist_id,
        subject_ref=subject_ref(tourist_id) if tourist_id else None,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def release_exception(db: Session, exception_id: int, actor: str) -> RetentionException:
    e = db.get(RetentionException, exception_id)
    if e is None:
        raise LookupError("Exception not found")
    if e.active:
        e.active, e.released_at = False, utc_now()
        e.created_by = e.created_by  # creator kept; releaser is in the audit log
        db.commit()
    return e


def category_held(db: Session, category: str) -> bool:
    """A hold with no tourist = the whole category is frozen."""
    return db.query(RetentionException.id).filter(
        RetentionException.category == category, RetentionException.active.is_(True),
        RetentionException.tourist_id.is_(None)).first() is not None


def held_tourist_ids(db: Session, category: str) -> set[int]:
    return {t for (t,) in db.query(RetentionException.tourist_id).filter(
        RetentionException.category == category, RetentionException.active.is_(True),
        RetentionException.tourist_id.isnot(None)).all()}


def exceptions_for_tourist(db: Session, tourist_id: int) -> list[RetentionException]:
    return db.query(RetentionException).filter(
        RetentionException.tourist_id == tourist_id, RetentionException.active.is_(True)).all()


# ---------------------------------------------------------------- job audit
def record_job(db: Session, category: str, *, job_type: str = "scheduled", processed: int = 0,
               deleted: int = 0, retained: int = 0, status: str = "completed",
               failure: str | None = None, subject_ref: str | None = None,
               attempts: int = 1) -> DeletionJob:
    now = utc_now()
    job = DeletionJob(
        job_id=new_public_id("DEL"), category=category, job_type=job_type, status=status,
        subject_ref=subject_ref, started_at=now, finished_at=now, records_processed=processed,
        records_deleted=deleted, records_retained=retained, failure_reason=failure,
        attempts=attempts,
    )
    db.add(job)
    db.commit()
    return job


def job_out(j: DeletionJob) -> dict:
    return {
        "job_id": j.job_id, "category": j.category, "job_type": j.job_type, "status": j.status,
        "started_at": j.started_at, "finished_at": j.finished_at,
        "records_processed": j.records_processed, "records_deleted": j.records_deleted,
        "records_retained": j.records_retained, "attempts": j.attempts,
        "failure_reason": j.failure_reason,
    }


# ----------------------------------------------------------------- handlers
# Each returns (processed, deleted, retained) and is safe to repeat.
def _cutoff(policy: RetentionPolicy):
    return utc_now() - timedelta(days=policy.retention_days or 0)


def _h_location(db: Session, policy: RetentionPolicy):
    from app.services import privacy

    examined, deleted, retained = privacy.purge_expired_location(db)
    return examined, deleted, retained


def _h_sessions(db: Session, policy: RetentionPolicy):
    now = utc_now()
    a = db.query(RevokedToken).filter(RevokedToken.expires_at < now).delete(synchronize_session=False)
    b = db.query(PasswordResetToken).filter(
        PasswordResetToken.expires_at < now).delete(synchronize_session=False)
    db.commit()
    return a + b, a + b, 0


def _h_exports(db: Session, policy: RetentionPolicy):
    from app.services import privacy_export

    n = privacy_export.cleanup_expired(db)
    return n, n, 0


def _h_notifications(db: Session, policy: RetentionPolicy):
    q = db.query(NotificationLog).filter(
        NotificationLog.created_at < _cutoff(policy),
        NotificationLog.status.notin_(("queued", "sending", "retrying")))
    n = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return n, n, 0


def _h_audit(db: Session, policy: RetentionPolicy):
    q = db.query(AuditLog).filter(AuditLog.timestamp < _cutoff(policy))
    n = q.count()
    q.delete(synchronize_session=False)
    db.commit()
    return n, n, 0


def _h_incidents(db: Session, policy: RetentionPolicy):
    """De-identify resolved incidents past the period; an open one is never touched."""
    held = held_tourist_ids(db, "sos_incident_records")
    base = db.query(Incident).filter(
        Incident.status == "resolved", Incident.tourist_id.isnot(None),
        Incident.resolved_at.isnot(None), Incident.resolved_at < _cutoff(policy))
    rows = base.all()
    done = kept = 0
    for inc in rows:
        if inc.tourist_id in held:
            kept += 1
            continue
        for ev in db.query(IncidentEvent).filter(IncidentEvent.incident_id == inc.id).all():
            ev.note = ""
        inc.tourist_id, inc.description = None, ""
        if inc.lat is not None:
            inc.lat, inc.lng = round(inc.lat, 2), round(inc.lng, 2)
        done += 1
    db.commit()
    return len(rows), done, kept


HANDLERS = {
    "location_history": _h_location,
    "session_tokens": _h_sessions,
    "data_exports": _h_exports,
    "notification_logs": _h_notifications,
    "security_audit_logs": _h_audit,
    "sos_incident_records": _h_incidents,
}


def run(db: Session, *, job_type: str = "scheduled", category: str | None = None) -> list[dict]:
    """Run every active, period-defined policy that has a handler. A failure is
    recorded and does not stop the other categories; the next tick retries it."""
    seed_policies(db)
    out = []
    q = db.query(RetentionPolicy).filter(RetentionPolicy.is_active.is_(True))
    if category:
        q = q.filter(RetentionPolicy.category == category)
    for p in q.order_by(RetentionPolicy.category).all():
        handler = HANDLERS.get(p.category)
        if handler is None or p.retention_days is None or p.deletion_action == "retain":
            continue
        if category_held(db, p.category):
            out.append(job_out(record_job(db, p.category, job_type=job_type, retained=1,
                                          failure="Category is under an active retention exception")))
            continue
        try:
            processed, deleted, retained = handler(db, p)
            job = record_job(db, p.category, job_type=job_type, processed=processed,
                             deleted=deleted, retained=retained)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.error("retention_job_failed", category=p.category, error=str(exc))
            prior = db.query(DeletionJob).filter(
                DeletionJob.category == p.category, DeletionJob.status == "failed").count()
            job = record_job(db, p.category, job_type=job_type, status="failed",
                             failure=type(exc).__name__ + ": " + str(exc)[:300], attempts=prior + 1)
        out.append(job_out(job))
    return out


def tick(db: Session) -> list[dict]:
    """Scheduler entry (replaces the location-only tick; same schedule/lock): the
    retention sweep, export cleanup and any deletion request that is due."""
    from app.services import privacy_deletion

    results = run(db, job_type="scheduled")
    try:
        privacy_deletion.process_due(db)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.error("deletion_due_failed", error=str(exc))
    return results


def summary(db: Session, limit: int = 50) -> dict:
    jobs = db.query(DeletionJob).order_by(DeletionJob.id.desc()).limit(limit).all()
    now = utc_now()
    overdue = db.query(RetentionException).filter(
        RetentionException.active.is_(True), RetentionException.review_by < now).count()
    return {
        "recent_jobs": [job_out(j) for j in jobs],
        "failed_jobs": db.query(DeletionJob).filter(DeletionJob.status == "failed").count(),
        "active_exceptions": db.query(RetentionException).filter(
            RetentionException.active.is_(True)).count(),
        "exceptions_overdue_for_review": overdue,
        "policies_needing_review": db.query(RetentionPolicy).filter(
            RetentionPolicy.requires_review.is_(True)).count(),
    }
