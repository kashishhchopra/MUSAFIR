"""Fall & crash detection: the "Are you OK?" countdown in front of the
existing incident pipeline.

Flow (see models/fall_event.py for the states):

    start_event()  ->  pending  --cancel_event()-------->  cancelled   (no incident)
                          |       --confirm_event()------>  confirmed   (1 incident)
                          |       --sweep (tick)--------->  confirmed   (1 incident)
                          '-------sos_now()-------------->  sos_triggered (existing SOS)

Design rules, each one enforced here rather than hoped for:

* The SERVER owns the clock. `countdown_deadline` is fixed at start; the tourist's
  app only displays it. If the app is closed, the phone dies or the network drops
  mid-countdown, `tick_fall_events` still raises the incident after the deadline.
* One state transition out of `pending`, ever. It is a single conditional
  `UPDATE ... WHERE status = 'pending'`, so "I AM SAFE" racing the expiry cannot
  both win: the loser matches zero rows and does nothing. The incident is created
  in the SAME transaction as the winning UPDATE, so a failure rolls the claim
  back and the row stays `pending` for the next sweep -- an event is never
  marked confirmed without an incident, nor an incident made without the claim.
* Incidents are made by `monitoring.raise_motion_incident` / `monitoring.trigger_sos`
  -- the existing pipeline -- never by a parallel one.
* Idempotent: the same `client_event_id` returns the same row; a second detection
  while one is pending (or just after one raised an incident) folds into it. A
  partial unique index additionally guarantees a single pending row per tourist.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.fall_event import (
    CANCELLED,
    CONFIRMED,
    PENDING,
    SOS_TRIGGERED,
    FallEvent,
)
from app.models.incident import Incident, IncidentEvent
from app.models.tourist import Tourist
from app.services import audit
from app.services import monitoring
from app.websocket.manager import broadcast_sync, notify_tourist_sync

logger = get_logger(__name__)


class FallDetectionError(Exception):
    """A request the current state of the event cannot satisfy."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


# ------------------------------------------------------------------ config
def public_config() -> dict:
    """Everything the client needs; the browser carries no hardcoded values."""
    s = settings
    return {
        "enabled": s.FALL_DETECTION_ENABLED,
        "simulation_enabled": s.FALL_SIMULATION_ENABLED,
        "countdown_seconds": s.FALL_COUNTDOWN_SECONDS,
        "cooldown_seconds": s.FALL_DETECTOR_COOLDOWN_SECONDS,
        "history_limit": s.FALL_HISTORY_LIMIT,
        "thresholds": {
            "freefall_g": s.FALL_FREEFALL_G,
            "freefall_min_ms": s.FALL_FREEFALL_MIN_MS,
            "impact_g": s.FALL_IMPACT_G,
            "freefall_impact_window_ms": s.FALL_FREEFALL_IMPACT_WINDOW_MS,
            "crash_impact_g": s.FALL_CRASH_IMPACT_G,
            "crash_min_speed_kmh": s.FALL_CRASH_MIN_SPEED_KMH,
            "stillness_dev_g": s.FALL_STILLNESS_DEV_G,
            "stillness_gyro_dps": s.FALL_STILLNESS_GYRO_DPS,
            "stillness_ms": s.FALL_STILLNESS_MS,
            "post_impact_observe_ms": s.FALL_POST_IMPACT_OBSERVE_MS,
        },
    }


# ------------------------------------------------------------------ helpers
def serialize(ev: FallEvent, *, deduplicated: bool = False, now: datetime | None = None) -> dict:
    now = now or utc_now()
    try:
        meta = json.loads(ev.metadata_json or "{}")
    except ValueError:  # pragma: no cover - only if the column was hand-edited
        meta = {}
    remaining = (ev.countdown_deadline - now).total_seconds() if ev.status == PENDING else 0.0
    return {
        "id": ev.id, "tourist_id": ev.tourist_id, "event_type": ev.event_type,
        "source": ev.source, "simulated": ev.simulated, "device_id": ev.device_id,
        "status": ev.status, "detected_at": _aware(ev.detected_at),
        "countdown_deadline": _aware(ev.countdown_deadline), "resolved_at": _aware(ev.resolved_at),
        "resolution": ev.resolution, "lat": ev.lat, "lng": ev.lng,
        "has_location": ev.lat is not None and ev.lng is not None,
        "incident_id": ev.incident_id, "metadata": meta,
        "server_time": _aware(now), "seconds_remaining": max(0.0, remaining),
        "deduplicated": deduplicated,
    }


def _push(ev: FallEvent, kind: str) -> None:
    """Tell the tourist's own channel(s) and the control room about a state
    change. A wearable-originated countdown reaches the phone this way."""
    payload = {"event": "fall_event", "kind": kind,
               **json.loads(json.dumps(serialize(ev), default=lambda o: o.isoformat()))}
    notify_tourist_sync(ev.tourist_id, payload)
    broadcast_sync({k: payload[k] for k in (
        "event", "kind", "id", "tourist_id", "event_type", "source", "simulated", "status",
        "countdown_deadline", "incident_id",
    )})


def _resolve_location(tourist: Tourist, lat: float | None, lng: float | None):
    """Prefer the event's own fix, fall back to the tourist's last known one,
    and return (lat, lng, note). (None, None, ...) when there is nothing."""
    if lat is not None and lng is not None:
        return lat, lng, ""
    if tourist.last_lat is not None and tourist.last_lng is not None:
        return tourist.last_lat, tourist.last_lng, "Using last known location (no GPS fix with the event)."
    return None, None, ""


def _aware(dt: datetime | None) -> datetime | None:
    """Naive-UTC -> tz-aware, so the JSON carries a 'Z' and a browser never
    mistakes it for local time."""
    return dt.replace(tzinfo=UTC) if dt is not None else None


def _detected_at(age_seconds: float | None, now: datetime) -> datetime:
    """When the detection happened, from how long ago the DEVICE says it was.
    Relative on purpose: a phone with a wrong clock cannot shorten (or extend)
    the countdown. Clamped to [0, FALL_MAX_EVENT_AGE_SECONDS] so a stale or
    replayed request cannot insta-escalate or buy extra time."""
    age = min(max(age_seconds or 0.0, 0.0), float(settings.FALL_MAX_EVENT_AGE_SECONDS))
    return now - timedelta(seconds=age)


# ------------------------------------------------------------------ start
def start_event(
    db: Session, tourist: Tourist, *, event_type: str, source: str,
    client_event_id: str, age_seconds: float | None = None,
    lat: float | None = None, lng: float | None = None, accuracy_m: float | None = None,
    metadata: dict | None = None, device_id: str | None = None, now: datetime | None = None,
) -> tuple[FallEvent, bool]:
    """Open the countdown. Returns (event, deduplicated).

    `deduplicated=True` means nothing new was created: the same client_event_id
    was already recorded, a countdown is already running for this tourist, or an
    incident was raised for them inside FALL_DEDUPE_WINDOW_SECONDS.
    """
    if not settings.FALL_DETECTION_ENABLED:
        raise FallDetectionError("Fall detection is disabled", 404)
    if source == "simulation" and not settings.FALL_SIMULATION_ENABLED:
        raise FallDetectionError("Simulation mode is disabled on this server", 403)

    now = now or utc_now()

    # 1) idempotent retry
    same = (db.query(FallEvent)
            .filter(FallEvent.tourist_id == tourist.id, FallEvent.client_event_id == client_event_id)
            .first())
    if same:
        return same, True

    # 2) a countdown is already running, or one just raised an incident
    window = now - timedelta(seconds=settings.FALL_DEDUPE_WINDOW_SECONDS)
    recent = (db.query(FallEvent)
              .filter(FallEvent.tourist_id == tourist.id,
                      FallEvent.status.in_([PENDING, CONFIRMED, SOS_TRIGGERED]),
                      FallEvent.created_at >= window)
              .order_by(FallEvent.created_at.desc())
              .first())
    if recent:
        logger.info("fall_event_deduplicated", tourist_id=tourist.id, existing_id=recent.id)
        return recent, True

    detected = _detected_at(age_seconds, now)
    ev = FallEvent(
        tourist_id=tourist.id, client_event_id=client_event_id, event_type=event_type,
        source=source, device_id=device_id, status=PENDING,
        detected_at=detected,
        countdown_deadline=detected + timedelta(seconds=settings.FALL_COUNTDOWN_SECONDS),
        lat=lat, lng=lng, accuracy_m=accuracy_m,
        metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
        created_at=now,
    )
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race with a concurrent start for this tourist (the partial
        # unique index, or the same client id): return the winner.
        db.rollback()
        winner = (db.query(FallEvent)
                  .filter(FallEvent.tourist_id == tourist.id,
                          (FallEvent.client_event_id == client_event_id) | (FallEvent.status == PENDING))
                  .order_by(FallEvent.created_at.desc()).first())
        if winner is None:  # pragma: no cover - the conflicting row vanished
            raise
        return winner, True

    db.refresh(ev)
    logger.warning("fall_event_started", event_id=ev.id, tourist_id=tourist.id,
                   event_type=event_type, source=source, has_location=lat is not None)
    audit.record(db, "fall_event_started", actor=f"tourist:{tourist.id}", target=tourist.digital_id,
                 detail=f"id={ev.id} type={event_type} source={source}")
    _push(ev, "started")
    return ev, False


# ------------------------------------------------------------------ transitions
def _claim(db: Session, ev: FallEvent, new_status: str, resolution: str, now: datetime,
           *, only_if_due_before: datetime | None = None) -> bool:
    """Atomically move pending -> `new_status`. True only for the single caller
    whose UPDATE matched the row. Does not commit (the caller attaches the
    incident to the same transaction)."""
    q = db.query(FallEvent).filter(FallEvent.id == ev.id, FallEvent.status == PENDING)
    if only_if_due_before is not None:
        q = q.filter(FallEvent.countdown_deadline <= only_if_due_before)
    claimed = q.update(
        {"status": new_status, "resolved_at": now, "resolution": resolution},
        synchronize_session=False,
    )
    return claimed == 1


def cancel_event(db: Session, ev: FallEvent, *, resolution: str = "tourist_cancelled",
                 actor: str = "tourist", now: datetime | None = None) -> FallEvent:
    """"I AM SAFE". Accepted whenever the event is still pending -- including a
    few seconds past the nominal deadline while the sweep's grace window is
    open -- so a tap that was in flight when the clock ran out still wins."""
    now = now or utc_now()
    if _claim(db, ev, CANCELLED, resolution, now):
        db.commit()
        db.refresh(ev)
        logger.info("fall_event_cancelled", event_id=ev.id, tourist_id=ev.tourist_id)
        audit.record(db, "fall_event_cancelled", actor=actor, target=f"fall_event:{ev.id}",
                     detail=f"type={ev.event_type} source={ev.source}")
        _push(ev, "cancelled")
        return ev
    db.rollback()
    db.refresh(ev)
    if ev.status == CANCELLED:
        return ev  # idempotent
    raise FallDetectionError(f"Event already {ev.status}; it can no longer be cancelled")


def _peak_g(ev: FallEvent):
    """Stored detector peak acceleration, if the client sent one (else None = unknown)."""
    try:
        return json.loads(ev.metadata_json or "{}").get("peak_g")
    except ValueError:
        return None


def _confirm_claimed(db: Session, ev: FallEvent, resolution: str, now: datetime) -> FallEvent:
    """Raise the incident for an event this caller has just claimed. Same
    transaction as the claim: any failure rolls both back."""
    tourist = db.get(Tourist, ev.tourist_id)
    lat, lng, note = _resolve_location(tourist, ev.lat, ev.lng)
    detail = f"Detector: {ev.metadata_json}." if ev.metadata_json not in ("", "{}") else ""
    try:
        inc = monitoring.raise_motion_incident(
            db, tourist, ev.event_type, lat, lng, source=ev.source,
            simulated=ev.simulated, detail=" ".join(p for p in (note, detail) if p),
            evidence={"peak_g": _peak_g(ev)},
        )
        ev.incident_id = inc.id
        db.add(IncidentEvent(
            incident_id=inc.id, status="detected",
            note=f"Raised from fall_event #{ev.id} ({ev.source}); countdown expired ({resolution}).",
        ))
        db.commit()
    except Exception:
        db.rollback()  # the claim goes with it -> still pending, swept again
        logger.exception("fall_event_incident_failed", event_id=ev.id)
        raise
    db.refresh(ev)
    logger.warning("fall_event_confirmed", event_id=ev.id, incident_id=ev.incident_id,
                   tourist_id=ev.tourist_id, resolution=resolution)
    audit.record(db, "fall_event_confirmed", actor="system", target=tourist.digital_id,
                 detail=f"id={ev.id} incident={ev.incident_id} type={ev.event_type} via {resolution}")
    _push(ev, "confirmed")
    return ev


def confirm_event(db: Session, ev: FallEvent, *, now: datetime | None = None,
                  resolution: str = "countdown_expired:client") -> FallEvent:
    """The countdown reached zero on the client. Refused until the SERVER's
    deadline has passed (a client cannot trigger an incident early)."""
    now = now or utc_now()
    if ev.status in (CONFIRMED, SOS_TRIGGERED):
        return ev  # idempotent: already has its incident
    if ev.status == CANCELLED:
        raise FallDetectionError("Event was cancelled")
    if now < ev.countdown_deadline:
        left = (ev.countdown_deadline - now).total_seconds()
        raise FallDetectionError(f"Countdown still running ({left:.1f}s left)", 409)
    if not _claim(db, ev, CONFIRMED, resolution, now):
        db.rollback()
        db.refresh(ev)
        if ev.status == CANCELLED:
            raise FallDetectionError("Event was cancelled")
        return ev  # someone else (the sweep) confirmed it first
    return _confirm_claimed(db, ev, resolution, now)


def sos_now(db: Session, ev: FallEvent, *, message: str, lat: float | None = None,
            lng: float | None = None, now: datetime | None = None) -> tuple[FallEvent, dict | None]:
    """"Trigger SOS immediately": runs the EXISTING SOS workflow
    (monitoring.trigger_sos: nearest-unit dispatch, SOS notifications, live
    tracking, escalation clock) and links it to this event. Falls back to the
    fall/crash incident when there is no location at all, because dispatch
    needs coordinates."""
    now = now or utc_now()
    if ev.status == SOS_TRIGGERED:
        return ev, None
    if ev.status != PENDING:
        raise FallDetectionError(f"Event already {ev.status}")
    if not _claim(db, ev, SOS_TRIGGERED, "immediate_sos", now):
        db.rollback()
        db.refresh(ev)
        if ev.status == SOS_TRIGGERED:
            return ev, None
        raise FallDetectionError(f"Event already {ev.status}")

    tourist = db.get(Tourist, ev.tourist_id)
    rlat, rlng, note = _resolve_location(tourist, lat if lat is not None else ev.lat,
                                         lng if lng is not None else ev.lng)
    tag = "[SIMULATED] " if ev.simulated else ""
    try:
        result = None
        if rlat is not None:
            result = monitoring.trigger_sos(db, tourist, rlat, rlng, f"{tag}{message}")
            ev.incident_id = result["incident_id"]
            db.add(IncidentEvent(incident_id=ev.incident_id, status="detected",
                                 note=f"Immediate SOS from fall_event #{ev.id} ({ev.event_type}, {ev.source})."))
        else:
            inc = monitoring.raise_motion_incident(
                db, tourist, ev.event_type, None, None, source=ev.source, simulated=ev.simulated,
                detail="Tourist requested immediate help.")
            ev.incident_id = inc.id
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("fall_event_sos_failed", event_id=ev.id)
        raise
    db.refresh(ev)
    logger.warning("fall_event_sos", event_id=ev.id, incident_id=ev.incident_id, note=note)
    audit.record(db, "fall_event_sos", actor=f"tourist:{ev.tourist_id}", target=tourist.digital_id,
                 detail=f"id={ev.id} incident={ev.incident_id}")
    _push(ev, "sos_triggered")
    return ev, result


# ------------------------------------------------------------------ background sweep
def tick_fall_events(db: Session, now: datetime | None = None) -> list[int]:
    """Raise the incident for every countdown that expired with nobody
    cancelling -- the guarantee that a closed app, a dead phone or a lost
    connection still ends in a real alert. Waits FALL_CONFIRM_GRACE_SECONDS past
    the deadline so an in-flight "I AM SAFE" is not overtaken. Returns the ids
    confirmed. Registered in app/main.py's lifespan next to the other ticks."""
    if not settings.FALL_DETECTION_ENABLED:
        return []
    now = now or utc_now()
    cutoff = now - timedelta(seconds=settings.FALL_CONFIRM_GRACE_SECONDS)
    due = (db.query(FallEvent)
           .filter(FallEvent.status == PENDING, FallEvent.countdown_deadline <= cutoff)
           .order_by(FallEvent.countdown_deadline).all())
    done: list[int] = []
    for ev in due:
        try:
            if _claim(db, ev, CONFIRMED, "countdown_expired:sweep", now, only_if_due_before=cutoff):
                _confirm_claimed(db, ev, "countdown_expired:sweep", now)
                done.append(ev.id)
            else:
                db.rollback()
        except Exception:  # noqa: BLE001 -- one bad event must not stop the sweep
            db.rollback()
    return done


# ------------------------------------------------------------------ queries
def active_event(db: Session, tourist_id: int) -> FallEvent | None:
    return (db.query(FallEvent)
            .filter(FallEvent.tourist_id == tourist_id, FallEvent.status == PENDING).first())


def history(db: Session, tourist_id: int, limit: int | None = None) -> list[FallEvent]:
    return (db.query(FallEvent).filter(FallEvent.tourist_id == tourist_id)
            .order_by(FallEvent.created_at.desc(), FallEvent.id.desc())
            .limit(limit or settings.FALL_HISTORY_LIMIT).all())


def incident_for(db: Session, ev: FallEvent) -> Incident | None:
    return db.get(Incident, ev.incident_id) if ev.incident_id else None
