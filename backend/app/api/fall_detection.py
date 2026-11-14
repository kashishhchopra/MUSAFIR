"""Fall & crash detection endpoints -- see services/fall_detection.py.

Tourist-facing routes sit under /tourists/{id}/fall-events and use the same
`require_self_or_admin` scoping as the rest of the tourist API, so one tourist
can never read, cancel or confirm another's event. Wearables use the device-key
auth the smart-band telemetry already uses (api/devices.py). Incidents are never
created here -- only by the service, through the existing monitoring pipeline.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    authenticate_device,
    get_current_user,
    require_admin_or_responder,
    require_self_or_admin,
)
from app.core.config import settings
from app.core.time import utc_now
from app.db.session import get_db
from app.models.device import Device
from app.models.fall_event import FallEvent
from app.models.tourist import Tourist
from app.models.user import User
from app.schemas.fall_detection import (
    DeviceFallEventCreate,
    FallDetectionConfigOut,
    FallEventCreate,
    FallEventOut,
    FallSosRequest,
)
from app.services import fall_detection as svc

router = APIRouter(tags=["fall-detection"])


def _enabled() -> None:
    if not settings.FALL_DETECTION_ENABLED:
        raise HTTPException(status_code=404, detail="Fall detection is disabled")


def _tourist_or_404(db: Session, tourist_id: int) -> Tourist:
    t = db.get(Tourist, tourist_id)
    if not t:
        raise HTTPException(status_code=404, detail="Tourist not found")
    return t


def _event_or_404(db: Session, tourist_id: int, event_id: int) -> FallEvent:
    ev = db.get(FallEvent, event_id)
    # A different tourist's event is reported as not found, not forbidden, so
    # ids cannot be probed.
    if not ev or ev.tourist_id != tourist_id:
        raise HTTPException(status_code=404, detail="Fall event not found")
    return ev


def _err(e: svc.FallDetectionError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=str(e))


@router.get("/fall-detection/config", response_model=FallDetectionConfigOut)
def get_config(_: User = Depends(get_current_user)):
    """Thresholds, countdown length and feature flags for the client detector."""
    return svc.public_config()


@router.post("/tourists/{tourist_id}/fall-events", response_model=FallEventOut)
def start_fall_event(tourist_id: int, payload: FallEventCreate, response: Response,
                     db: Session = Depends(get_db), _: User = Depends(require_self_or_admin)):
    """A phone (or the labelled simulation) reports a possible fall/crash and
    starts the cancellation countdown. 201 = new countdown, 200 = folded into an
    existing one (idempotent retry / duplicate detection)."""
    _enabled()
    tourist = _tourist_or_404(db, tourist_id)
    try:
        ev, dup = svc.start_event(
            db, tourist, event_type=payload.event_type, source=payload.source,
            client_event_id=payload.client_event_id, age_seconds=payload.seconds_since_detection,
            lat=payload.lat, lng=payload.lng, accuracy_m=payload.accuracy_m,
            metadata=payload.metadata.model_dump(exclude_none=True),
        )
    except svc.FallDetectionError as e:
        raise _err(e) from e
    response.status_code = 200 if dup else 201
    return svc.serialize(ev, deduplicated=dup)


@router.get("/tourists/{tourist_id}/fall-events/active", response_model=FallEventOut | None)
def get_active_fall_event(tourist_id: int, db: Session = Depends(get_db),
                          _: User = Depends(require_self_or_admin)):
    """The countdown currently running for this tourist, or null. Lets a
    reloaded app (or a phone told about a wearable event) resume it."""
    _enabled()
    ev = svc.active_event(db, tourist_id)
    return svc.serialize(ev) if ev else None


@router.get("/tourists/{tourist_id}/fall-events", response_model=list[FallEventOut])
def list_fall_events(tourist_id: int, limit: int | None = None, db: Session = Depends(get_db),
                     _: User = Depends(require_self_or_admin)):
    _enabled()
    cap = settings.FALL_HISTORY_LIMIT
    return [svc.serialize(e) for e in svc.history(db, tourist_id, min(limit or cap, cap))]


@router.post("/tourists/{tourist_id}/fall-events/{event_id}/cancel", response_model=FallEventOut)
def cancel_fall_event(tourist_id: int, event_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_self_or_admin)):
    """"I AM SAFE - CANCEL ALERT". No incident is created."""
    _enabled()
    ev = _event_or_404(db, tourist_id, event_id)
    try:
        ev = svc.cancel_event(db, ev, actor=user.email)
    except svc.FallDetectionError as e:
        raise _err(e) from e
    return svc.serialize(ev)


@router.post("/tourists/{tourist_id}/fall-events/{event_id}/confirm", response_model=FallEventOut)
def confirm_fall_event(tourist_id: int, event_id: int, db: Session = Depends(get_db),
                       _: User = Depends(require_self_or_admin)):
    """The client's countdown hit zero. The server only honours it once ITS
    deadline has passed (409 otherwise), then opens the critical incident via
    the existing monitoring pipeline. Safe to call repeatedly."""
    _enabled()
    ev = _event_or_404(db, tourist_id, event_id)
    try:
        ev = svc.confirm_event(db, ev)
    except svc.FallDetectionError as e:
        raise _err(e) from e
    return svc.serialize(ev)


@router.post("/tourists/{tourist_id}/fall-events/{event_id}/sos")
def sos_from_fall_event(tourist_id: int, event_id: int, payload: FallSosRequest,
                        request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require_self_or_admin)):
    """"Trigger SOS immediately": skips the rest of the countdown and runs the
    existing SOS workflow. Returns the same body as POST /tourists/{id}/sos
    (plus the event), so the app can reuse its Emergency Mode screen."""
    _enabled()
    ev = _event_or_404(db, tourist_id, event_id)
    try:
        ev, sos = svc.sos_now(db, ev, message=payload.message, lat=payload.lat, lng=payload.lng)
    except svc.FallDetectionError as e:
        raise _err(e) from e
    return {"event": svc.serialize(ev), "sos": sos}


@router.get("/fall-events", response_model=list[FallEventOut])
def list_all_fall_events(status: str | None = None, limit: int = 50,
                         db: Session = Depends(get_db),
                         _: User = Depends(require_admin_or_responder)):
    """Control-room audit view of recent detections across tourists."""
    _enabled()
    q = db.query(FallEvent)
    if status:
        q = q.filter(FallEvent.status == status)
    rows = q.order_by(FallEvent.created_at.desc()).limit(max(1, min(limit, 200))).all()
    return [svc.serialize(e) for e in rows]


# ------------------------------------------------------------------ wearables
@router.post("/devices/{device_id}/fall-event", response_model=FallEventOut)
def device_fall_event(device_id: str, payload: DeviceFallEventCreate, response: Response,
                      db: Session = Depends(get_db), device: Device = Depends(authenticate_device)):
    """A wearable reports a fall/crash. Enters the SAME countdown as a phone
    detection (the tourist's phone shows the "Are you OK?" screen over its live
    channel and can cancel; if nobody does, the sweep raises the incident).
    Authenticated by the device key, exactly like /devices/{id}/telemetry."""
    _enabled()
    tourist = _tourist_or_404(db, device.tourist_id)
    device.last_heartbeat = utc_now()
    db.commit()
    # A band without a client-side id still gets idempotency per device+minute.
    cid = payload.client_event_id or f"dev-{device.device_id}-{int(utc_now().timestamp() // 60)}"
    try:
        ev, dup = svc.start_event(
            db, tourist, event_type=payload.event_type, source="wearable",
            client_event_id=cid[:64], age_seconds=payload.seconds_since_detection,
            lat=payload.lat, lng=payload.lng, accuracy_m=payload.accuracy_m,
            metadata=payload.metadata.model_dump(exclude_none=True), device_id=device.device_id,
        )
    except svc.FallDetectionError as e:
        raise _err(e) from e
    response.status_code = 200 if dup else 201
    return svc.serialize(ev, deduplicated=dup)


@router.post("/devices/{device_id}/fall-event/{event_id}/cancel", response_model=FallEventOut)
def device_cancel_fall_event(device_id: str, event_id: int, db: Session = Depends(get_db),
                             device: Device = Depends(authenticate_device)):
    """The wearer presses "I'm OK" on the band itself."""
    _enabled()
    ev = _event_or_404(db, device.tourist_id, event_id)
    if ev.device_id != device.device_id:
        raise HTTPException(status_code=404, detail="Fall event not found")
    try:
        ev = svc.cancel_event(db, ev, resolution="wearable_cancelled", actor=f"device:{device.device_id}")
    except svc.FallDetectionError as e:
        raise _err(e) from e
    return svc.serialize(ev)
