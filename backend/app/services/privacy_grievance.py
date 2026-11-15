"""Privacy grievances: a tourist raises a concern, an administrator answers it,
and the whole exchange stays on the record.

The response target (`PRIVACY_GRIEVANCE_RESPONSE_DAYS`) is configuration. It is
an internal service level, not a statement of the statutory deadline -- confirm
that with legal review and set it accordingly.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.privacy import GrievanceResponse, PrivacyGrievance
from app.models.tourist import Tourist
from app.services.privacy_registry import new_public_id, subject_ref

CATEGORIES = ("access_or_export", "correction", "erasure", "consent", "location", "security", "other")
STATUSES = ("open", "in_review", "resolved", "rejected")
CLOSED = ("resolved", "rejected")


class GrievanceError(ValueError):
    pass


def submit(db: Session, tourist: Tourist, category: str, description: str) -> PrivacyGrievance:
    if category not in CATEGORIES:
        raise GrievanceError(f"category must be one of: {', '.join(CATEGORIES)}")
    description = (description or "").strip()
    if len(description) < 10:
        raise GrievanceError("Please describe the concern in at least 10 characters.")
    now = utc_now()
    g = PrivacyGrievance(
        grievance_id=new_public_id("GRV"), tourist_id=tourist.id, subject_ref=subject_ref(tourist.id),
        category=category, description=description, status="open", submitted_at=now,
        due_at=now + timedelta(days=settings.PRIVACY_GRIEVANCE_RESPONSE_DAYS), updated_at=now)
    db.add(g)
    db.commit()
    db.refresh(g)
    return g


def _responses(db: Session, g: PrivacyGrievance) -> list[GrievanceResponse]:
    return db.query(GrievanceResponse).filter(
        GrievanceResponse.grievance_pk == g.id).order_by(GrievanceResponse.id).all()


def out(db: Session, g: PrivacyGrievance, *, with_responses: bool = True) -> dict:
    now = utc_now()
    d = {
        "grievance_id": g.grievance_id, "category": g.category, "description": g.description,
        "status": g.status, "submitted_at": g.submitted_at, "due_at": g.due_at,
        "resolved_at": g.resolved_at, "updated_at": g.updated_at,
        "overdue": bool(g.status not in CLOSED and g.due_at and g.due_at < now),
    }
    if with_responses:
        d["responses"] = [{"author_role": r.author_role, "message": r.message,
                           "status_after": r.status_after, "created_at": r.created_at}
                          for r in _responses(db, g)]
    return d


def list_for(db: Session, tourist_id: int) -> list[PrivacyGrievance]:
    return db.query(PrivacyGrievance).filter(
        PrivacyGrievance.tourist_id == tourist_id).order_by(PrivacyGrievance.id.desc()).all()


def get_for(db: Session, tourist_id: int, grievance_id: str) -> PrivacyGrievance | None:
    return db.query(PrivacyGrievance).filter(
        PrivacyGrievance.grievance_id == grievance_id,
        PrivacyGrievance.tourist_id == tourist_id).first()


def tourist_reply(db: Session, g: PrivacyGrievance, message: str) -> PrivacyGrievance:
    if g.status in CLOSED:
        raise GrievanceError("This grievance is closed. Please raise a new one.")
    message = (message or "").strip()
    if not message:
        raise GrievanceError("Message cannot be empty.")
    db.add(GrievanceResponse(grievance_pk=g.id, author_role="tourist", message=message))
    g.updated_at = utc_now()
    db.commit()
    return g


# ---- admin ---------------------------------------------------------------
def list_all(db: Session, status: str | None = None, limit: int = 200) -> list[PrivacyGrievance]:
    q = db.query(PrivacyGrievance)
    if status:
        q = q.filter(PrivacyGrievance.status == status)
    return q.order_by(PrivacyGrievance.id.desc()).limit(limit).all()


def get_any(db: Session, grievance_id: str) -> PrivacyGrievance | None:
    return db.query(PrivacyGrievance).filter(PrivacyGrievance.grievance_id == grievance_id).first()


def respond(db: Session, g: PrivacyGrievance, message: str, new_status: str | None) -> PrivacyGrievance:
    message = (message or "").strip()
    if not message:
        raise GrievanceError("A response message is required.")
    if new_status is not None and new_status not in STATUSES:
        raise GrievanceError(f"status must be one of: {', '.join(STATUSES)}")
    status = new_status or ("in_review" if g.status == "open" else g.status)
    db.add(GrievanceResponse(grievance_pk=g.id, author_role="admin", message=message,
                             status_after=status))
    g.status = status
    now = utc_now()
    g.updated_at = now
    g.resolved_at = now if status in CLOSED else None
    db.commit()
    return g
