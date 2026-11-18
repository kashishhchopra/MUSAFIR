"""Trip Guardian (Family Live-Share).

Management endpoints (create/list/revoke a share) require the tourist's own
login, same as the rest of their data. The resolve endpoint is deliberately
public and unauthenticated -- a guardian has no account, only the link the
tourist handed them -- so it exposes nothing beyond what that one read-only
share link is scoped to (this tourist's live status), and never the tourist's
document number, phone, or full profile.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_self_or_admin
from app.db.session import get_db
from app.models.guardian import TripGuardian
from app.models.tourist import Tourist
from app.models.user import User
from app.schemas.guardian import TripGuardianCreate, TripGuardianOut, TripGuardianUpdate

router = APIRouter(tags=["guardian"])


def _reject_duplicate(db: Session, tourist_id: int, name: str, contact: str, exclude_id: int | None = None) -> None:
    """One live guardian per contact (or per name when there is no contact)."""
    for g in db.query(TripGuardian).filter(TripGuardian.tourist_id == tourist_id,
                                           TripGuardian.revoked.is_(False)).all():
        if g.id == exclude_id:
            continue
        same_contact = bool(contact) and g.guardian_contact == contact
        same_name_no_contact = not contact and not g.guardian_contact and g.guardian_name.lower() == name.lower()
        if same_contact or same_name_no_contact:
            raise HTTPException(status_code=409, detail=f"{g.guardian_name} is already in your trusted contacts.")


@router.patch("/tourists/{tourist_id}/guardians/{guardian_id}", response_model=TripGuardianOut)
def update_guardian(tourist_id: int, guardian_id: int, payload: TripGuardianUpdate,
                    db: Session = Depends(get_db), _: User = Depends(require_self_or_admin)):
    g = (db.query(TripGuardian)
         .filter(TripGuardian.id == guardian_id, TripGuardian.tourist_id == tourist_id,
                 TripGuardian.revoked.is_(False)).first())
    if not g:
        raise HTTPException(status_code=404, detail="Guardian share not found")
    name = payload.guardian_name if payload.guardian_name is not None else g.guardian_name
    contact = payload.guardian_contact if payload.guardian_contact is not None else g.guardian_contact
    _reject_duplicate(db, tourist_id, name, contact, exclude_id=g.id)
    g.guardian_name, g.guardian_contact = name, contact
    db.commit()
    db.refresh(g)
    return g


@router.post("/tourists/{tourist_id}/guardians", response_model=TripGuardianOut, status_code=201)
def create_guardian(tourist_id: int, payload: TripGuardianCreate, db: Session = Depends(get_db),
                    _: User = Depends(require_self_or_admin)):
    t = db.get(Tourist, tourist_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tourist not found")
    from app.services.privacy_consent import is_withdrawn

    if is_withdrawn(db, tourist_id, "guardian_sharing"):
        raise HTTPException(status_code=403, detail="Guardian sharing consent is withdrawn. Grant it in Privacy settings first.")
    _reject_duplicate(db, tourist_id, payload.guardian_name, payload.guardian_contact)
    guardian = TripGuardian(
        tourist_id=tourist_id, guardian_name=payload.guardian_name,
        guardian_contact=payload.guardian_contact,
    )
    db.add(guardian)
    db.commit()
    db.refresh(guardian)
    return guardian


@router.get("/tourists/{tourist_id}/guardians", response_model=list[TripGuardianOut])
def list_guardians(tourist_id: int, db: Session = Depends(get_db),
                   _: User = Depends(require_self_or_admin)):
    return (
        db.query(TripGuardian)
        .filter(TripGuardian.tourist_id == tourist_id)
        .order_by(TripGuardian.created_at.desc())
        .all()
    )


@router.post("/tourists/{tourist_id}/guardians/{guardian_id}/revoke", response_model=TripGuardianOut)
def revoke_guardian(tourist_id: int, guardian_id: int, db: Session = Depends(get_db),
                    _: User = Depends(require_self_or_admin)):
    guardian = (
        db.query(TripGuardian)
        .filter(TripGuardian.id == guardian_id, TripGuardian.tourist_id == tourist_id)
        .first()
    )
    if not guardian:
        raise HTTPException(status_code=404, detail="Guardian share not found")
    guardian.revoked = True
    db.commit()
    db.refresh(guardian)
    return guardian


@router.get("/guardian/{token}")
def resolve_guardian_link(token: str, db: Session = Depends(get_db)):
    """Public, unauthenticated: what the family member's browser polls. No
    login -- the token itself is the credential, same trust model as a
    calendar share link or a password-reset link."""
    guardian = db.query(TripGuardian).filter(TripGuardian.token == token).first()
    if not guardian or guardian.revoked:
        raise HTTPException(status_code=404, detail="This share link is invalid or has been revoked")
    t = db.get(Tourist, guardian.tourist_id)
    if not t:
        raise HTTPException(status_code=404, detail="This share link is invalid or has been revoked")
    return {
        "guardian_name": guardian.guardian_name,
        "tourist_name": t.full_name,
        "status": t.status,
        "safety_score": t.safety_score,
        "last_lat": t.last_lat,
        "last_lng": t.last_lng,
        "last_seen": t.last_seen,
        "trip_start": t.trip_start,
        "trip_end": t.trip_end,
        "trip_active": t.is_valid,
    }
