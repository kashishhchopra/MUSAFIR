"""Emergency Medical Profile API.

  Owner (tourist, own record only):
    GET/PUT  /tourists/{id}/medical-profile          read / create-or-partial-update
    DELETE   /tourists/{id}/medical-profile/{field}  clear one section
    GET      /tourists/{id}/medical-qr               QR + state (same QR as the Digital ID)
    POST     /tourists/{id}/medical-qr/regenerate    new token, old one dies
    POST     /tourists/{id}/medical-qr/revoke        disable QR access
    GET      /tourists/{id}/medical-access           who viewed my profile (role + time)
  Responder (admin/responder only, verified on the backend):
    POST     /tourist-id/medical                     scanned token -> medical profile
  Admin:
    GET      /medical-access-log                     audit view

There is deliberately NO public endpoint and NO endpoint that returns another
tourist's medical data by numeric id: the only way in is a valid QR token plus
an authorized account.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin, require_self_or_admin
from app.db.session import get_db
from app.models.tourist import Tourist
from app.models.user import User
from app.schemas.medical_profile import (
    MedicalAccessLogOut,
    MedicalAccessRequest,
    MedicalProfileUpdate,
)
from app.services import medical_profile as svc
from app.services.tourist_id import SCAN_AUTHORIZED_ROLES

router = APIRouter(tags=["medical-profile"])


def _tourist(tourist_id: int, db: Session) -> Tourist:
    t = db.get(Tourist, tourist_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tourist not found")
    return t


def require_self(tourist_id: int, user: User = Depends(get_current_user)) -> User:
    """Medical data is editable/readable here only by the tourist it belongs to.
    Admins are NOT included: they reach medical data only through the audited
    QR access path."""
    if user.role == "tourist" and user.tourist_id == tourist_id:
        return user
    raise HTTPException(status_code=403, detail="Forbidden")


# ------------------------------------------------------------------ options
@router.get("/medical-profile/options")
def medical_options(_: User = Depends(get_current_user)):
    return svc.options()


# -------------------------------------------------------------------- owner
@router.get("/tourists/{tourist_id}/medical-profile")
def get_medical_profile(tourist_id: int, db: Session = Depends(get_db),
                        _: User = Depends(require_self)):
    return svc.get_own(db, _tourist(tourist_id, db))


@router.put("/tourists/{tourist_id}/medical-profile")
def put_medical_profile(tourist_id: int, payload: MedicalProfileUpdate, request: Request,
                        db: Session = Depends(get_db), user: User = Depends(require_self)):
    return svc.update(db, _tourist(tourist_id, db), payload, user, request)


@router.delete("/tourists/{tourist_id}/medical-profile/{field}")
def delete_medical_field(tourist_id: int, field: str, request: Request,
                         db: Session = Depends(get_db), user: User = Depends(require_self)):
    return svc.clear_field(db, _tourist(tourist_id, db), field, user, request)


@router.get("/tourists/{tourist_id}/medical-qr")
def get_medical_qr(tourist_id: int, db: Session = Depends(get_db),
                   _: User = Depends(require_self_or_admin)):
    return svc.qr_state(db, _tourist(tourist_id, db))


@router.post("/tourists/{tourist_id}/medical-qr/regenerate")
def regenerate_medical_qr(tourist_id: int, request: Request, db: Session = Depends(get_db),
                          user: User = Depends(require_self_or_admin)):
    return svc.regenerate_qr(db, _tourist(tourist_id, db), user, request)


@router.post("/tourists/{tourist_id}/medical-qr/revoke")
def revoke_medical_qr(tourist_id: int, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(require_self_or_admin)):
    return svc.revoke_qr(db, _tourist(tourist_id, db), user, request)


@router.get("/tourists/{tourist_id}/medical-access")
def my_medical_access_history(tourist_id: int, limit: int = 20, db: Session = Depends(get_db),
                              _: User = Depends(require_self)):
    return svc.own_access_history(db, _tourist(tourist_id, db), limit)


# ---------------------------------------------------------------- responder
@router.post("/tourist-id/medical")
def scan_medical(payload: MedicalAccessRequest, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """Scanned QR token -> the tourist's tourist-reported medical profile.

    The role is read from the authenticated account on the server -- nothing
    the browser sends can widen it. An unauthorized account gets a bare 403
    (and the attempt is audited); a failed/revoked/expired token returns a
    non-medical status body. Only `status == "granted"` carries medical data.
    """
    if user.role not in SCAN_AUTHORIZED_ROLES:
        svc.deny_unauthorized(user, request)
        raise HTTPException(status_code=403, detail="Not authorized to view medical profiles")
    return svc.access(db, user, payload.token, payload.purpose, payload.lat, payload.lng, request)


# -------------------------------------------------------------------- admin
@router.get("/medical-access-log", response_model=list[MedicalAccessLogOut])
def medical_access_log(limit: int = 100, result: str | None = None,
                       digital_id: str | None = None, db: Session = Depends(get_db),
                       _: User = Depends(require_admin)):
    """Who opened which tourist's medical profile, when, why, and with what
    outcome. Contains no medical content."""
    return svc.access_log(db, limit, result, digital_id)
