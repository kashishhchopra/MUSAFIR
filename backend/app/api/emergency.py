"""SOS live-location sharing endpoints -- see services/emergency_location.py.

Deliberately its own router/incident-scoped auth rather than reusing
require_self_or_admin (which expects a `tourist_id` path segment): these
routes are addressed by incident, and "does this tourist own this
incident" has to be checked against the incident row itself, not just a
path parameter.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin_or_responder
from app.db.session import get_db
from app.models.incident import Incident
from app.models.user import User
from app.schemas.emergency_location import EmergencyLocationUpdate, EmergencyTrackOut
from app.services import emergency_location as svc

router = APIRouter(prefix="/incidents", tags=["emergency-location"])


def _get_incident_or_404(incident_id: int, db: Session) -> Incident:
    inc = db.get(Incident, incident_id)
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")
    return inc


def _require_owner_or_admin(inc: Incident, user: User) -> None:
    """Only the tourist this incident belongs to, or an admin, may push
    location updates or stop their own sharing. A hotel account has no role
    that reaches this dependency at all in this app -- there is no
    "hotel" role -- so it's excluded by construction, not by an extra
    check bolted on here."""
    if user.role == "admin":
        return
    if user.role == "tourist" and user.tourist_id == inc.tourist_id:
        return
    raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/live", response_model=list[EmergencyTrackOut])
def list_live_emergencies(db: Session = Depends(get_db),
                          _: User = Depends(require_admin_or_responder)):
    """Every incident currently sharing live location -- the central safety
    dashboard's "LIVE EMERGENCIES" section. Police/responder only; never
    reachable by a tourist or any hotel-facing account."""
    return svc.list_active_emergencies(db)


@router.get("/{incident_id}/location", response_model=EmergencyTrackOut)
def get_emergency_location(incident_id: int, db: Session = Depends(get_db),
                           _: User = Depends(require_admin_or_responder)):
    inc = _get_incident_or_404(incident_id, db)
    return svc.build_track(db, inc)


@router.post("/{incident_id}/location", status_code=201)
def post_emergency_location(incident_id: int, payload: EmergencyLocationUpdate,
                            demo: bool = False,
                            db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """One live-location ping during an active SOS. `demo=true` marks a
    simulated position (no real GPS available) -- stored and shown to
    police as such, never presented as a real fix. See
    services/emergency_location.py for the (light-touch, human-reviewed)
    plausibility check on consecutive pings."""
    inc = _get_incident_or_404(incident_id, db)
    _require_owner_or_admin(inc, user)
    try:
        svc.record_location(
            db, inc, payload.lat, payload.lng,
            payload.accuracy_m, payload.speed_kmh, payload.heading_deg, demo=demo,
        )
    except svc.EmergencyLocationError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.commit()
    return {"status": "ok"}


@router.post("/{incident_id}/stop-tracking")
def stop_emergency_tracking(incident_id: int, db: Session = Depends(get_db),
                            user: User = Depends(get_current_user)):
    """The control room turns an emergency's live sharing off. It does NOT close
    the incident (that is update_incident in api/incidents.py).

    Admin only: a tourist can no longer end their own emergency from the app --
    ending it is the control room's decision, and the tourist can always press
    SOS again (which resumes sharing, see monitoring.trigger_sos)."""
    inc = _get_incident_or_404(incident_id, db)
    if user.role != "admin":
        raise HTTPException(status_code=403,
                            detail="Only the control room can end live location sharing for an emergency.")
    svc.stop_tracking(db, inc, "stopped by the control room")
    db.commit()
    return {"status": "stopped"}
