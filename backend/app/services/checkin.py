"""Tourist Check-in / Check-out: a missed check-in is treated as a soft
distress signal, well before an SOS is ever pressed.

`tick_checkins` mirrors app/services/escalation.py's tick pattern: plain,
synchronous, DB-session-in/out, callable directly in tests and wrapped by a
periodic APScheduler job for the real background tick (see app/main.py).

A Walk-Me-Home journey (services/walk_home.py) is the same row with
`kind == "walk_home"` and runs through this very tick -- there is no second
escalation engine; only the wording, grace length and notifications differ.

Lifecycle: planned -> checked_in (on time, the happy path)
           planned -> missed (deadline passed, no check-in yet -- an alert
                               is raised, this is the "gentle" first stage)
           missed  -> escalated (a grace period *also* passed -- an incident
                               is opened, same severity band as other soft
                               distress signals like a route deviation)
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.alert import Alert
from app.models.checkin import CheckIn
from app.models.tourist import Tourist
from app.services.monitoring import _create_alert, _open_incident

logger = get_logger(__name__)

# How soon before expiry to start warning, and how often to re-warn (rather
# than once per tick, which would spam an alert every 30s for a week).
VISA_EXPIRY_WARNING_DAYS = 7
_VISA_ALERT_REPEAT_HOURS = 20


def _tick_visa_expiry(db: Session) -> list[int]:
    """Folded into the same scheduler tick as check-ins rather than a sixth
    scheduled job -- both are "soft distress signal, well before an SOS"
    style checks on the tourist roster."""
    now = utc_now()
    horizon = now + timedelta(days=VISA_EXPIRY_WARNING_DAYS)
    expiring = (
        db.query(Tourist)
        .filter(
            Tourist.document_type == "passport",
            Tourist.visa_expiry.isnot(None),
            Tourist.visa_expiry <= horizon,
            Tourist.visa_expiry >= now,
            Tourist.status != "missing",
        )
        .all()
    )
    warned: list[int] = []
    recent_cutoff = now - timedelta(hours=_VISA_ALERT_REPEAT_HOURS)
    for tourist in expiring:
        already_warned = (
            db.query(Alert)
            .filter(Alert.tourist_id == tourist.id, Alert.type == "visa_expiry",
                    Alert.created_at >= recent_cutoff)
            .first()
        )
        if already_warned:
            continue
        days_left = (tourist.visa_expiry - now).days
        _create_alert(
            db, tourist.id, "visa_expiry", "medium",
            f"{tourist.full_name}'s visa expires in {days_left} day"
            f"{'s' if days_left != 1 else ''} ({tourist.visa_expiry.date().isoformat()})",
            tourist.last_lat, tourist.last_lng,
        )
        warned.append(tourist.id)
    if warned:
        db.commit()
    return warned


def tick_checkins(db: Session) -> dict[str, list[int]]:
    """Advance planned outings AND Walk-Me-Home journeys (CheckIn.kind) one stage.

    Each transition is claimed with a single conditional UPDATE that also pins
    the deadline we looked at, so a tourist checking in (which moves the
    deadline) or ending the journey while this tick runs makes the claim match
    zero rows and the tick simply skips it -- a check-in racing the escalation
    can never be overwritten. For an escalation the incident is opened in the
    same transaction as the claim: if anything fails the claim rolls back and
    the next tick retries, so a journey is never marked escalated with no
    incident, nor an incident opened twice."""
    from app.services import walk_home

    now = utc_now()
    newly_missed: list[int] = []
    newly_escalated: list[int] = []
    visa_warned = _tick_visa_expiry(db)

    overdue = (
        db.query(CheckIn)
        .filter(CheckIn.status == "planned", CheckIn.expected_return_at <= now)
        .all()
    )
    for c in overdue:
        claimed = (
            db.query(CheckIn)
            .filter(CheckIn.id == c.id, CheckIn.status == "planned",
                    CheckIn.expected_return_at == c.expected_return_at)
            .update({"status": "missed", "missed_at": now}, synchronize_session=False)
        )
        if not claimed:
            continue
        db.refresh(c)  # the bulk UPDATE bypassed the ORM; reload the new status
        tourist = db.get(Tourist, c.tourist_id)
        if tourist:
            if c.kind == walk_home.KIND:
                walk_home.on_missed(db, c, tourist)
            else:
                _create_alert(
                    db, tourist.id, "missed_checkin", "medium",
                    f"{tourist.full_name} missed their expected check-in for "
                    f"\"{c.destination_name}\"",
                    c.dest_lat, c.dest_lng,
                )
        newly_missed.append(c.id)
        logger.warning("checkin_missed", checkin_id=c.id, tourist_id=c.tourist_id, kind=c.kind)

    # The grace period is per journey (a walk is shorter than an outing); NULL
    # means the global CHECKIN_GRACE_MINUTES, so outings behave exactly as before.
    for c in db.query(CheckIn).filter(CheckIn.status == "missed").all():
        grace = c.grace_minutes if c.grace_minutes is not None else settings.CHECKIN_GRACE_MINUTES
        if c.expected_return_at > now - timedelta(minutes=grace):
            continue
        claimed = (
            db.query(CheckIn)
            .filter(CheckIn.id == c.id, CheckIn.status == "missed",
                    CheckIn.expected_return_at == c.expected_return_at)
            .update({"status": "escalated"}, synchronize_session=False)
        )
        if not claimed:
            continue
        db.refresh(c)  # the bulk UPDATE bypassed the ORM; reload the new status
        tourist = db.get(Tourist, c.tourist_id)
        if tourist:
            if c.kind == walk_home.KIND:
                walk_home.on_escalated(db, c, tourist)
            else:
                _open_incident(
                    db, tourist, "missed_checkin", "high",
                    f"{tourist.full_name} has not checked in for \"{c.destination_name}\" "
                    f"since {settings.CHECKIN_GRACE_MINUTES} minutes past the expected return time",
                    c.dest_lat, c.dest_lng,
                )
        newly_escalated.append(c.id)
        logger.warning("checkin_escalated", checkin_id=c.id, tourist_id=c.tourist_id, kind=c.kind)

    if newly_missed or newly_escalated:
        db.commit()
    return {"missed": newly_missed, "escalated": newly_escalated, "visa_warned": visa_warned}
