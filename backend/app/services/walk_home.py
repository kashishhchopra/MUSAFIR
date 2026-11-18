"""Walk-Me-Home: a monitored journey with live guardian tracking.

This is NOT a second emergency engine. A journey is a `CheckIn` row with
`kind == "walk_home"` (models/checkin.py) and rides the existing check-in
machinery end to end:

    planned --(deadline passes)--> missed --(grace passes)--> escalated
       ^  \\__ "I am safe" moves the deadline forward ___/          |
       |                                                            v
       '--- late "I am safe" during grace -------------   incident (existing pipeline)

`services/checkin.py:tick_checkins` is the ONLY thing that flips a journey to
missed/escalated, and it opens the incident through `monitoring._open_incident`,
exactly as for a planned outing. This module adds what a live journey needs on
top: creating and ending it, the check-in that pushes the deadline out, location
sharing, who may watch, and the walk-specific wording/notifications that
`tick_checkins` calls through `on_missed` / `on_escalated`.

Rules, enforced in code:

* The SERVER owns every deadline (`expected_return_at`, `arrive_by`). The app
  shows them; closing it changes nothing -- the background tick still escalates.
* Every state change is ONE conditional `UPDATE ... WHERE status IN (...)`
  (and, where a deadline is involved, `AND expected_return_at = <seen>`), so a
  check-in racing the escalation tick, two check-ins at once, or "end" racing
  "cancel" can never both win or leave a half-applied state. The incident is
  created in the same transaction as the escalation claim.
* A guardian sees a journey only if the tourist selected that guardian for it,
  the guardian link is not revoked, and the journey is live. Once it ends or is
  cancelled, access ends with it.
* Location is only accepted for a live journey, never invented, and always
  returned with its age so a stale fix is shown as stale rather than "live".
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.checkin import CheckIn
from app.models.guardian import TripGuardian
from app.models.incident import Incident, IncidentEvent
from app.models.tourist import Tourist
from app.services import audit, notifications
from app.websocket.manager import broadcast_sync, notify_guardian_sync, notify_tourist_sync

logger = get_logger(__name__)

KIND = "walk_home"
ACTIVE = ("planned", "missed", "escalated")
COMPLETED = "completed"
CANCELLED = "cancelled"


class WalkHomeError(Exception):
    """A request the current state of the journey cannot satisfy."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


# ------------------------------------------------------------------ helpers
def _aware(dt: datetime | None) -> datetime | None:
    """Naive-UTC -> tz-aware so the JSON carries a 'Z'."""
    return dt.replace(tzinfo=UTC) if dt is not None else None


def _presets(raw: str) -> list[int]:
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0:
            out.append(int(part))
    return out


def public_config() -> dict:
    """Everything the app needs; the browser carries no hardcoded values."""
    s = settings
    return {
        "enabled": s.WALKHOME_ENABLED,
        "duration": {"min": s.WALKHOME_MIN_DURATION_MINUTES, "max": s.WALKHOME_MAX_DURATION_MINUTES,
                     "default": s.WALKHOME_DEFAULT_DURATION_MINUTES,
                     "presets": _presets(s.WALKHOME_DURATION_PRESETS)},
        "interval": {"min": s.WALKHOME_MIN_INTERVAL_MINUTES, "max": s.WALKHOME_MAX_INTERVAL_MINUTES,
                     "default": s.WALKHOME_DEFAULT_INTERVAL_MINUTES,
                     "presets": _presets(s.WALKHOME_INTERVAL_PRESETS)},
        "grace": {"min": s.WALKHOME_MIN_GRACE_MINUTES, "max": s.WALKHOME_MAX_GRACE_MINUTES,
                  "default": s.WALKHOME_DEFAULT_GRACE_MINUTES},
        "max_extensions": s.WALKHOME_MAX_EXTENSIONS,
        "max_extension_minutes": s.WALKHOME_MAX_EXTENSION_MINUTES,
        "location_interval_seconds": s.WALKHOME_LOCATION_INTERVAL_SECONDS,
        "location_stale_seconds": s.WALKHOME_LOCATION_STALE_SECONDS,
        "max_guardians": s.WALKHOME_MAX_GUARDIANS,
        "route_buffer_percent": s.WALKHOME_ROUTE_BUFFER_PERCENT,
        "route_buffer_min_minutes": s.WALKHOME_ROUTE_BUFFER_MIN_MINUTES,
    }


def guardian_ids(c: CheckIn) -> list[int]:
    try:
        ids = json.loads(c.guardian_ids_json or "[]")
    except ValueError:  # pragma: no cover - only if the column was hand-edited
        return []
    return [i for i in ids if isinstance(i, int)]


def shared_guardians(db: Session, c: CheckIn) -> list[TripGuardian]:
    """Guardians who may watch this journey right now: selected for it, owned by
    the same tourist, link not revoked -- and only while it is live."""
    if c.status not in ACTIVE:
        return []
    ids = guardian_ids(c)
    if not ids:
        return []
    return (db.query(TripGuardian)
            .filter(TripGuardian.id.in_(ids), TripGuardian.tourist_id == c.tourist_id,
                    TripGuardian.revoked.is_(False))
            .order_by(TripGuardian.id).all())


def _phase(c: CheckIn) -> str:
    return {"planned": "on_track", "missed": "missed", "escalated": "escalated"}.get(c.status, c.status)


def _grace(c: CheckIn) -> int:
    return c.grace_minutes if c.grace_minutes is not None else settings.CHECKIN_GRACE_MINUTES


def _secs(delta: timedelta) -> float:
    return max(0.0, delta.total_seconds())


def _body(db: Session, c: CheckIn, now: datetime) -> dict:
    """The fields tourist and guardian views share."""
    live = c.status in ACTIVE
    loc_age = (now - c.last_loc_at).total_seconds() if c.last_loc_at else None
    has_loc = live and c.last_lat is not None and c.last_lng is not None and loc_age is not None
    return {
        "id": c.id,
        "destination_name": c.destination_name,
        "dest_lat": c.dest_lat, "dest_lng": c.dest_lng,
        "status": c.status, "phase": _phase(c), "active": live,
        "started_at": _aware(c.started_at), "arrive_by": _aware(c.arrive_by),
        "next_checkin_deadline": _aware(c.expected_return_at),
        "check_interval_minutes": c.check_interval_minutes,
        "grace_minutes": _grace(c),
        "grace_deadline": _aware(c.expected_return_at + timedelta(minutes=_grace(c))),
        "last_checkin_at": _aware(c.last_checkin_at), "checkin_count": c.checkin_count,
        "ended_at": _aware(c.ended_at),
        "server_time": _aware(now),
        "seconds_to_deadline": _secs(c.expected_return_at - now) if live else 0.0,
        "seconds_to_arrival": _secs(c.arrive_by - now) if live and c.arrive_by else 0.0,
        "seconds_to_escalation": (
            _secs(c.expected_return_at + timedelta(minutes=_grace(c)) - now) if c.status == "missed" else 0.0),
        "deadline_passed": live and now >= c.expected_return_at,
        "has_location": has_loc,
        "last_lat": c.last_lat if has_loc else None,
        "last_lng": c.last_lng if has_loc else None,
        "last_location_at": _aware(c.last_loc_at) if has_loc else None,
        "location_age_seconds": loc_age if has_loc else None,
        # stale = a fix exists but is too old to call live; the UI must say so.
        "location_stale": bool(has_loc and loc_age > settings.WALKHOME_LOCATION_STALE_SECONDS),
        "location_accuracy_m": c.last_accuracy_m if has_loc else None,
    }


def serialize(db: Session, c: CheckIn, now: datetime | None = None) -> dict:
    """The tourist's own view of their journey."""
    now = now or utc_now()
    out = _body(db, c, now)
    guardians = shared_guardians(db, c) if c.status in ACTIVE else []
    ids = guardian_ids(c)
    names = {g.id: g.guardian_name for g in db.query(TripGuardian).filter(TripGuardian.id.in_(ids)).all()} if ids else {}
    out.update({
        "tourist_id": c.tourist_id, "incident_id": c.incident_id,
        "extensions": c.extensions,
        "extensions_left": max(0, settings.WALKHOME_MAX_EXTENSIONS - c.extensions),
        "start_lat": c.start_lat, "start_lng": c.start_lng,
        "guardians": [{"id": i, "name": names.get(i, "Removed guardian"),
                       "has_access": i in {g.id for g in guardians}} for i in ids],
    })
    return out


def guardian_view(db: Session, c: CheckIn, guardian: TripGuardian, tourist: Tourist,
                  now: datetime | None = None) -> dict:
    """What ONE guardian may see: a first name-level profile, the destination,
    timers, check-in status and the last location -- no document number, phone,
    incident id or other guardians."""
    now = now or utc_now()
    out = _body(db, c, now)
    out.update({"tourist_name": tourist.full_name, "guardian_name": guardian.guardian_name,
                "alert": c.status if c.status in ("missed", "escalated") else None})
    return out


def _active(db: Session, tourist_id: int) -> CheckIn | None:
    return (db.query(CheckIn)
            .filter(CheckIn.tourist_id == tourist_id, CheckIn.kind == KIND, CheckIn.status.in_(ACTIVE))
            .order_by(CheckIn.id.desc()).first())


def active_session(db: Session, tourist_id: int) -> CheckIn | None:
    return _active(db, tourist_id)


def history(db: Session, tourist_id: int, limit: int = 20) -> list[CheckIn]:
    return (db.query(CheckIn)
            .filter(CheckIn.tourist_id == tourist_id, CheckIn.kind == KIND)
            .order_by(CheckIn.id.desc()).limit(max(1, min(limit, 100))).all())


def delete_history(db: Session, tourist_id: int) -> int:
    """Erase the tourist's FINISHED journeys (never one that is still running)."""
    n = (db.query(CheckIn)
         .filter(CheckIn.tourist_id == tourist_id, CheckIn.kind == KIND, CheckIn.status.notin_(ACTIVE))
         .delete(synchronize_session=False))
    db.commit()
    audit.record(db, "walk_home_history_deleted", actor=f"tourist:{tourist_id}",
                 target=f"tourist:{tourist_id}", detail=f"deleted={n}")
    return n


def get_owned(db: Session, tourist_id: int, session_id: int) -> CheckIn:
    c = db.get(CheckIn, session_id)
    # Someone else's journey (or an ordinary outing) is "not found", never
    # "forbidden", so ids cannot be probed.
    if not c or c.kind != KIND or c.tourist_id != tourist_id:
        raise WalkHomeError("Journey not found", 404)
    return c


def _push(db: Session, c: CheckIn, kind: str) -> None:
    """Live push: the tourist's own channel(s), and every guardian who may
    currently watch. Never raises into the request."""
    try:
        tourist = db.get(Tourist, c.tourist_id)
        payload = json.loads(json.dumps(serialize(db, c), default=lambda o: o.isoformat()))
        notify_tourist_sync(c.tourist_id, {"event": "walk_home", "kind": kind, "journey": payload})
        # Guardians get their own, narrower payload -- and one final "ended"
        # message (with no location) so an open page can close cleanly.
        ids = guardian_ids(c)
        if not ids or tourist is None:
            return
        live = {g.id: g for g in shared_guardians(db, c)}
        for gid in ids:
            g = live.get(gid)
            if g is not None:
                body = json.loads(json.dumps(guardian_view(db, c, g, tourist), default=lambda o: o.isoformat()))
                notify_guardian_sync(gid, {"event": "walk_home", "kind": kind, "journey": body})
            elif c.status not in ACTIVE:
                notify_guardian_sync(gid, {"event": "walk_home", "kind": "ended",
                                           "journey": {"id": c.id, "active": False, "status": c.status}})
    except Exception:  # pragma: no cover - a push failure must never fail the request
        logger.exception("walk_home_push_failed", session_id=c.id)


def _bounded(name: str, value: int, lo: int, hi: int) -> int:
    if value < lo or value > hi:
        raise WalkHomeError(f"{name} must be between {lo} and {hi} minutes", 422)
    return value


def _next_deadline(c: CheckIn, now: datetime) -> datetime:
    """Next check-in is one interval away, but never later than the expected
    arrival while that is still ahead (so arrival itself is a check-in point)."""
    nxt = now + timedelta(minutes=c.check_interval_minutes or settings.WALKHOME_DEFAULT_INTERVAL_MINUTES)
    if c.arrive_by is not None and now < c.arrive_by:
        nxt = min(nxt, c.arrive_by)
    return nxt


# ------------------------------------------------------------------ lifecycle
def start(db: Session, tourist: Tourist, *, destination_name: str, dest_lat: float | None,
          dest_lng: float | None, start_lat: float | None, start_lng: float | None,
          duration_minutes: int, check_interval_minutes: int | None, grace_minutes: int | None,
          guardian_ids_in: list[int]) -> CheckIn:
    s = settings
    duration = _bounded("duration", duration_minutes, s.WALKHOME_MIN_DURATION_MINUTES,
                        s.WALKHOME_MAX_DURATION_MINUTES)
    interval = _bounded("check-in interval",
                        check_interval_minutes or s.WALKHOME_DEFAULT_INTERVAL_MINUTES,
                        s.WALKHOME_MIN_INTERVAL_MINUTES, s.WALKHOME_MAX_INTERVAL_MINUTES)
    grace = _bounded("grace period", grace_minutes or s.WALKHOME_DEFAULT_GRACE_MINUTES,
                     s.WALKHOME_MIN_GRACE_MINUTES, s.WALKHOME_MAX_GRACE_MINUTES)
    if len(guardian_ids_in) > s.WALKHOME_MAX_GUARDIANS:
        raise WalkHomeError(f"At most {s.WALKHOME_MAX_GUARDIANS} guardians can follow a journey", 422)
    if guardian_ids_in:
        ok = {g.id for g in db.query(TripGuardian).filter(
            TripGuardian.id.in_(guardian_ids_in), TripGuardian.tourist_id == tourist.id,
            TripGuardian.revoked.is_(False)).all()}
        bad = [i for i in guardian_ids_in if i not in ok]
        if bad:  # someone else's guardian, a revoked one, or one that does not exist
            raise WalkHomeError("One or more selected guardians are not available", 422)
    if _active(db, tourist.id):
        raise WalkHomeError("You already have a journey in progress. End or cancel it first.", 409)

    now = utc_now()
    arrive_by = now + timedelta(minutes=duration)
    c = CheckIn(
        tourist_id=tourist.id, kind=KIND, destination_name=destination_name,
        dest_lat=dest_lat, dest_lng=dest_lng, status="planned",
        started_at=now, arrive_by=arrive_by, check_interval_minutes=interval, grace_minutes=grace,
        expected_return_at=min(now + timedelta(minutes=interval), arrive_by),
        start_lat=start_lat, start_lng=start_lng,
        # The starting fix is the first known location (a real browser reading,
        # or nothing at all) -- never a placeholder.
        last_lat=start_lat, last_lng=start_lng,
        last_loc_at=now if start_lat is not None else None,
        guardian_ids_json=json.dumps(guardian_ids_in),
    )
    db.add(c)
    try:
        db.commit()
    except IntegrityError:
        # The partial unique index: a second simultaneous start lost the race.
        db.rollback()
        raise WalkHomeError("You already have a journey in progress. End or cancel it first.", 409)
    db.refresh(c)
    logger.info("walk_home_started", session_id=c.id, tourist_id=tourist.id, guardians=len(guardian_ids_in))
    audit.record(db, "walk_home_started", actor=f"tourist:{tourist.id}", target=tourist.digital_id,
                 detail=f"id={c.id} minutes={duration} interval={interval} guardians={len(guardian_ids_in)}")
    notifications.notify_walk_home(db, c, tourist, "started", shared_guardians(db, c))
    db.commit()
    _push(db, c, "started")
    return c


def check_in(db: Session, tourist_id: int, session_id: int) -> CheckIn:
    """"I AM SAFE": push the next deadline out. Allowed on time and, if the
    deadline was missed, during the grace period (late check-in). Not allowed
    once the grace ran out and an incident exists."""
    c = get_owned(db, tourist_id, session_id)
    now = utc_now()
    fields = {"status": "planned", "expected_return_at": _next_deadline(c, now),
              "last_checkin_at": now, "checkin_count": CheckIn.checkin_count + 1, "missed_at": None}
    base = db.query(CheckIn).filter(CheckIn.id == c.id, CheckIn.kind == KIND)
    # `missed` first so we know exactly which transition we won (late or on time).
    late = base.filter(CheckIn.status == "missed").update(fields, synchronize_session=False)
    on_time = 0 if late else base.filter(CheckIn.status == "planned").update(fields, synchronize_session=False)
    if not (late or on_time):
        db.rollback()
        db.refresh(c)
        if c.status == "escalated":
            raise WalkHomeError(
                "The check-in window has passed and an alert was raised. End the journey to confirm you are safe.", 409)
        raise WalkHomeError(f"This journey is {c.status}; it can no longer be checked in to", 409)
    db.commit()
    db.refresh(c)
    tourist = db.get(Tourist, tourist_id)
    audit.record(db, "walk_home_checkin", actor=f"tourist:{tourist_id}", target=f"walk_home:{c.id}",
                 detail="late" if late else "on_time")
    if late and tourist:
        notifications.notify_walk_home(db, c, tourist, "recovered", shared_guardians(db, c))
        db.commit()
    _push(db, c, "checkin_late" if late else "checkin")
    return c


def update_location(db: Session, tourist_id: int, session_id: int, *, lat: float, lng: float,
                    accuracy_m: float | None, fix_age_seconds: float) -> tuple[CheckIn, bool, str]:
    """Record a browser position. Returns (journey, accepted, reason).

    Only a LIVE journey accepts it (a 409 tells the app to stop sharing). The
    fix time is "now minus the age the phone reports", so a wrong phone clock
    cannot back- or forward-date it; a fix that is not newer than the stored one
    -- a duplicate, a retry, an out-of-order delivery -- is ignored, not an error."""
    c = get_owned(db, tourist_id, session_id)
    if c.status not in ACTIVE:
        raise WalkHomeError("This journey has ended; location is no longer shared", 409)
    now = utc_now()
    if fix_age_seconds > settings.WALKHOME_MAX_FIX_AGE_SECONDS:
        return c, False, "too_old"
    located_at = now - timedelta(seconds=fix_age_seconds)
    if c.last_loc_at is not None and located_at <= c.last_loc_at:
        return c, False, "duplicate"
    n = (db.query(CheckIn)
         .filter(CheckIn.id == c.id, CheckIn.status.in_(ACTIVE),
                 (CheckIn.last_loc_at.is_(None)) | (CheckIn.last_loc_at < located_at))
         .update({"last_lat": lat, "last_lng": lng, "last_accuracy_m": accuracy_m,
                  "last_loc_at": located_at}, synchronize_session=False))
    if not n:
        db.rollback()
        db.refresh(c)
        return c, False, "duplicate" if c.status in ACTIVE else "ended"
    db.commit()
    db.refresh(c)
    _push(db, c, "location")
    return c, True, ""


def extend(db: Session, tourist_id: int, session_id: int, minutes: int) -> CheckIn:
    s = settings
    c = get_owned(db, tourist_id, session_id)
    if c.status != "planned":
        raise WalkHomeError(
            "Check in first, then add time." if c.status == "missed"
            else f"This journey is {c.status}; time can no longer be added", 409)
    if c.extensions >= s.WALKHOME_MAX_EXTENSIONS:
        raise WalkHomeError(f"Time can be added at most {s.WALKHOME_MAX_EXTENSIONS} times per journey", 409)
    _bounded("extension", minutes, 1, s.WALKHOME_MAX_EXTENSION_MINUTES)
    started, arrive = c.started_at or c.created_at, c.arrive_by or c.expected_return_at
    if (arrive + timedelta(minutes=minutes) - started).total_seconds() > s.WALKHOME_MAX_DURATION_MINUTES * 60:
        raise WalkHomeError(f"A journey can last at most {s.WALKHOME_MAX_DURATION_MINUTES} minutes", 422)
    seen_deadline, seen_ext = c.expected_return_at, c.extensions
    # If the next check-in IS the arrival time, it moves with it; an earlier
    # interval check-in stays where it is.
    new_deadline = seen_deadline + timedelta(minutes=minutes) if seen_deadline >= arrive else seen_deadline
    n = (db.query(CheckIn)
         .filter(CheckIn.id == c.id, CheckIn.status == "planned",
                 CheckIn.expected_return_at == seen_deadline, CheckIn.extensions == seen_ext)
         .update({"arrive_by": arrive + timedelta(minutes=minutes), "expected_return_at": new_deadline,
                  "extensions": seen_ext + 1}, synchronize_session=False))
    if not n:
        db.rollback()
        db.refresh(c)
        raise WalkHomeError("The journey changed while adding time; please try again", 409)
    db.commit()
    db.refresh(c)
    audit.record(db, "walk_home_extended", actor=f"tourist:{tourist_id}", target=f"walk_home:{c.id}",
                 detail=f"minutes={minutes}")
    _push(db, c, "extended")
    return c


def end(db: Session, tourist_id: int, session_id: int, outcome: str) -> CheckIn:
    """`completed` (arrived safely) or `cancelled`. Idempotent for the same
    outcome; conflicting if the journey already ended the other way. Ending
    stops location sharing and ends every guardian's access at once."""
    assert outcome in (COMPLETED, CANCELLED)
    c = get_owned(db, tourist_id, session_id)
    now = utc_now()
    n = (db.query(CheckIn)
         .filter(CheckIn.id == c.id, CheckIn.status.in_(ACTIVE))
         # Data minimisation: the live position is only needed while the journey
         # runs, so it is wiped the moment it ends (history keeps only name,
         # times and outcome).
         .update({"status": outcome, "ended_at": now, "last_lat": None, "last_lng": None,
                  "last_accuracy_m": None, "last_loc_at": None, "start_lat": None, "start_lng": None},
                 synchronize_session=False))
    if not n:
        db.rollback()
        db.refresh(c)
        if c.status == outcome:
            return c
        raise WalkHomeError(f"This journey is already {c.status}", 409)
    db.refresh(c)
    tourist = db.get(Tourist, tourist_id)
    if c.incident_id:
        # The incident belongs to the responders: it is annotated, never closed here.
        inc = db.get(Incident, c.incident_id)
        if inc is not None and inc.status != "resolved":
            note = ("The tourist ended their Walk-Me-Home journey and reports being safe."
                    if outcome == COMPLETED else "The tourist cancelled their Walk-Me-Home journey.")
            db.add(IncidentEvent(incident_id=inc.id, status=inc.status, note=note))
    db.commit()
    audit.record(db, f"walk_home_{outcome}", actor=f"tourist:{tourist_id}", target=f"walk_home:{c.id}",
                 detail=f"incident={c.incident_id}")
    if tourist is not None and guardian_ids(c):
        # `shared_guardians` is empty once the journey has ended, so name the
        # selected (still un-revoked) guardians directly for the closing message.
        selected = db.query(TripGuardian).filter(
            TripGuardian.id.in_(guardian_ids(c)), TripGuardian.tourist_id == tourist_id,
            TripGuardian.revoked.is_(False)).all()
        notifications.notify_walk_home(db, c, tourist, outcome, selected)
        db.commit()
    _push(db, c, outcome)
    return c


# ------------------------------------------------------------------ called by tick_checkins
def _where(c: CheckIn, tourist: Tourist) -> tuple[float | None, float | None, str]:
    """Best position to report: the journey's last live fix, else the tourist's
    last known location, else the destination. Says which it is."""
    if c.last_lat is not None and c.last_lng is not None:
        return c.last_lat, c.last_lng, "journey"
    if tourist.last_lat is not None and tourist.last_lng is not None:
        return tourist.last_lat, tourist.last_lng, "profile"
    if c.dest_lat is not None and c.dest_lng is not None:
        return c.dest_lat, c.dest_lng, "destination"
    return None, None, "none"


def _loc_phrase(c: CheckIn, tourist: Tourist, now: datetime) -> str:
    lat, lng, src = _where(c, tourist)
    if lat is None:
        return "Location unavailable."
    if src == "journey" and c.last_loc_at is not None:
        mins = int((now - c.last_loc_at).total_seconds() // 60)
        return f"Last known location {lat:.5f}, {lng:.5f} (updated {mins} min ago)."
    if src == "profile":
        return f"Last known location {lat:.5f}, {lng:.5f} (from the tourist's profile; no location was shared on this journey)."
    return f"No live location was shared; destination is {lat:.5f}, {lng:.5f}."


def on_missed(db: Session, c: CheckIn, tourist: Tourist) -> None:
    """The deadline passed with no check-in (already claimed `missed` by the
    tick). Raise the existing soft alert (which reaches the control room feed
    and the tourist's own channel) and tell the selected guardians. Caller commits."""
    from app.services.monitoring import _create_alert

    now = utc_now()
    lat, lng, _ = _where(c, tourist)
    _create_alert(
        db, tourist.id, "missed_checkin", "high",
        f"{tourist.full_name} missed a Walk-Me-Home check-in on the way to \"{c.destination_name}\". "
        f"They have {_grace(c)} min to respond. {_loc_phrase(c, tourist, now)}",
        lat, lng,
    )
    notifications.notify_walk_home(db, c, tourist, "missed", shared_guardians(db, c))
    _push(db, c, "missed")


def on_escalated(db: Session, c: CheckIn, tourist: Tourist) -> Incident:
    """The grace period ran out (already claimed `escalated` by the tick). Open
    the incident through the existing `_open_incident` -- the same call an
    outing's escalation makes -- tied to this journey. Caller commits, so the
    claim and the incident are one transaction."""
    from app.services.monitoring import _open_incident

    now = utc_now()
    lat, lng, _ = _where(c, tourist)
    due = _aware(c.expected_return_at).strftime("%H:%M UTC")
    desc = (
        f"{tourist.full_name} did not respond to a Walk-Me-Home check-in for \"{c.destination_name}\" "
        f"within the {_grace(c)}-minute grace period (check-in was due {due}; "
        f"{c.checkin_count} check-in{'s' if c.checkin_count != 1 else ''} completed). "
        f"{_loc_phrase(c, tourist, now)} [Journey #{c.id}]"
    )
    inc = _open_incident(db, tourist, "missed_checkin", settings.WALKHOME_ESCALATION_SEVERITY, desc, lat, lng,
                         evidence={"no_response": True, "missed_checkins": 1,
                                   "gps_unavailable": c.last_lat is None and lat is None})
    # The same unattended-incident clock an SOS gets, so the existing escalation
    # job (services/escalation.py) takes over if nobody acknowledges it.
    inc.escalation_deadline = now + timedelta(seconds=settings.ESCALATION_STAGE_TIMEOUT_SECONDS)
    c.incident_id = inc.id
    notifications.notify_walk_home(db, c, tourist, "escalated", shared_guardians(db, c), inc=inc)
    _push(db, c, "escalated")
    return inc


# ------------------------------------------------------------------ guardian side
def guardian_journey(db: Session, token: str) -> dict:
    """What a guardian's link resolves to: their live shared journey, or a
    plain "no journey" -- never anything that was not shared with them."""
    g = db.query(TripGuardian).filter(TripGuardian.token == token).first()
    if not g or g.revoked:
        raise WalkHomeError("This share link is invalid or has been revoked", 404)
    tourist = db.get(Tourist, g.tourist_id)
    if not tourist:
        raise WalkHomeError("This share link is invalid or has been revoked", 404)
    live = (db.query(CheckIn)
            .filter(CheckIn.tourist_id == g.tourist_id, CheckIn.kind == KIND, CheckIn.status.in_(ACTIVE))
            .order_by(CheckIn.id.desc()).all())
    for c in live:
        if g.id in guardian_ids(c):
            return {"journey": guardian_view(db, c, g, tourist), "guardian_name": g.guardian_name,
                    "tourist_name": tourist.full_name}
    return {"journey": None, "guardian_name": g.guardian_name, "tourist_name": tourist.full_name}
