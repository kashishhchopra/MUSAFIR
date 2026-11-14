"""Walk-Me-Home endpoints -- see services/walk_home.py.

Tourist routes sit under /tourists/{id}/walk-home and use the same
`require_self_or_admin` scoping as the rest of the tourist API, so one tourist
can never read or change another's journey. The guardian route is public and
token-based, like the existing Trip Guardian link (api/guardian.py): a guardian
has no account, only the link the tourist gave them -- and it resolves ONLY to a
journey the tourist selected that guardian for, while it is live.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_self_or_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.tourist import Tourist
from app.models.user import User
from app.schemas.walk_home import WalkHomeCreate, WalkHomeExtend, WalkHomeLocation
from app.services import walk_home as svc

router = APIRouter(tags=["walk-home"])


def _enabled() -> None:
    if not settings.WALKHOME_ENABLED:
        raise HTTPException(status_code=404, detail="Walk-Me-Home is disabled")


def _err(e: svc.WalkHomeError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=str(e))


def _tourist_or_404(db: Session, tourist_id: int) -> Tourist:
    t = db.get(Tourist, tourist_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tourist not found")
    return t


@router.get("/walk-home/config")
def get_config(_: User = Depends(get_current_user)):
    """Bounds, defaults and timings for the app; nothing is hardcoded client-side."""
    return svc.public_config()


@router.post("/tourists/{tourist_id}/walk-home", status_code=201)
def start_journey(tourist_id: int, payload: WalkHomeCreate, db: Session = Depends(get_db),
                  _: User = Depends(require_self_or_admin)):
    _enabled()
    tourist = _tourist_or_404(db, tourist_id)
    try:
        c = svc.start(
            db, tourist, destination_name=payload.destination_name,
            dest_lat=payload.dest_lat, dest_lng=payload.dest_lng,
            start_lat=payload.start_lat, start_lng=payload.start_lng,
            duration_minutes=payload.duration_minutes,
            check_interval_minutes=payload.check_interval_minutes,
            grace_minutes=payload.grace_minutes, guardian_ids_in=payload.guardian_ids,
        )
    except svc.WalkHomeError as e:
        raise _err(e) from e
    return svc.serialize(db, c)


@router.get("/tourists/{tourist_id}/walk-home/active")
def get_active_journey(tourist_id: int, db: Session = Depends(get_db),
                       _: User = Depends(require_self_or_admin)):
    """The live journey, or null. Lets a refreshed or reopened app resume it:
    every deadline in the answer is the server's."""
    _enabled()
    c = svc.active_session(db, tourist_id)
    return svc.serialize(db, c) if c else None


@router.get("/tourists/{tourist_id}/walk-home")
def journey_history(tourist_id: int, limit: int = 20, db: Session = Depends(get_db),
                    _: User = Depends(require_self_or_admin)):
    _enabled()
    return [svc.serialize(db, c) for c in svc.history(db, tourist_id, limit)]


@router.post("/tourists/{tourist_id}/walk-home/{session_id}/checkin")
def checkin(tourist_id: int, session_id: int, db: Session = Depends(get_db),
            _: User = Depends(require_self_or_admin)):
    """"I AM SAFE - CHECK IN": moves the next deadline out (late is fine during grace)."""
    _enabled()
    try:
        return svc.serialize(db, svc.check_in(db, tourist_id, session_id))
    except svc.WalkHomeError as e:
        raise _err(e) from e


@router.post("/tourists/{tourist_id}/walk-home/{session_id}/location")
def share_location(tourist_id: int, session_id: int, payload: WalkHomeLocation,
                   db: Session = Depends(get_db), _: User = Depends(require_self_or_admin)):
    """One browser position. 409 once the journey has ended (the app stops
    sharing); a duplicate or out-of-order fix is accepted=false, not an error."""
    _enabled()
    try:
        c, accepted, reason = svc.update_location(
            db, tourist_id, session_id, lat=payload.lat, lng=payload.lng,
            accuracy_m=payload.accuracy_m, fix_age_seconds=payload.fix_age_seconds)
    except svc.WalkHomeError as e:
        raise _err(e) from e
    return {"accepted": accepted, "reason": reason, "journey": svc.serialize(db, c)}


@router.post("/tourists/{tourist_id}/walk-home/{session_id}/extend")
def extend_journey(tourist_id: int, session_id: int, payload: WalkHomeExtend,
                   db: Session = Depends(get_db), _: User = Depends(require_self_or_admin)):
    _enabled()
    try:
        return svc.serialize(db, svc.extend(db, tourist_id, session_id, payload.minutes))
    except svc.WalkHomeError as e:
        raise _err(e) from e


@router.post("/tourists/{tourist_id}/walk-home/{session_id}/end")
def end_journey(tourist_id: int, session_id: int, db: Session = Depends(get_db),
                _: User = Depends(require_self_or_admin)):
    """Arrived safely: ends monitoring and sharing, tells the guardians."""
    _enabled()
    try:
        return svc.serialize(db, svc.end(db, tourist_id, session_id, svc.COMPLETED))
    except svc.WalkHomeError as e:
        raise _err(e) from e


@router.post("/tourists/{tourist_id}/walk-home/{session_id}/cancel")
def cancel_journey(tourist_id: int, session_id: int, db: Session = Depends(get_db),
                   _: User = Depends(require_self_or_admin)):
    _enabled()
    try:
        return svc.serialize(db, svc.end(db, tourist_id, session_id, svc.CANCELLED))
    except svc.WalkHomeError as e:
        raise _err(e) from e


@router.get("/guardian/{token}/journey")
def guardian_journey(token: str, db: Session = Depends(get_db)):
    """Public, token-scoped: the live journey shared with THIS guardian, or
    `{"journey": null}`. Revoked links and ended journeys expose nothing."""
    _enabled()
    try:
        return svc.guardian_journey(db, token)
    except svc.WalkHomeError as e:
        raise _err(e) from e
