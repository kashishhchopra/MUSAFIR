"""Smart SOS escalation: advance an unattended incident through stages.

Stages: control_room -> emergency_contact -> responder_dispatch -> acknowledged.
An incident advances one stage every time its `escalation_deadline` passes
without a human acknowledging it (see `POST /incidents/{id}/acknowledge`).
Resolved or already-acknowledged incidents are left alone.

`tick_escalations` is plain, synchronous, DB-session-in/out code -- callable
directly in tests, and wrapped by a periodic APScheduler job (registered in
`app/main.py`'s lifespan) for the real background tick.
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.incident import Incident, IncidentEvent
from app.models.tourist import Tourist
from app.services import dispatch, notifications

logger = get_logger(__name__)

_NEXT_STAGE = {
    "control_room": "emergency_contact",
    "emergency_contact": "responder_dispatch",
    "responder_dispatch": "acknowledged",
}


def tick_escalations(db: Session) -> list[int]:
    """Advance every open, overdue incident one escalation stage.

    Returns the ids of incidents that were advanced (mainly useful for tests).
    """
    now = utc_now()
    open_incidents = (
        db.query(Incident)
        .filter(
            Incident.status != "resolved",
            Incident.escalation_stage != "acknowledged",
            Incident.escalation_deadline.isnot(None),
            Incident.escalation_deadline <= now,
        )
        .all()
    )

    advanced = []
    for inc in open_incidents:
        current = inc.escalation_stage
        nxt = _NEXT_STAGE.get(current)
        if nxt is None:
            continue

        if nxt == "responder_dispatch":
            _ensure_unit_assigned(db, inc)
        if nxt in ("emergency_contact", "responder_dispatch"):
            _notify_escalation(db, inc, nxt)

        inc.escalation_stage = nxt
        inc.escalation_deadline = (
            None if nxt == "acknowledged"
            else now + timedelta(seconds=settings.ESCALATION_STAGE_TIMEOUT_SECONDS)
        )
        db.add(IncidentEvent(
            incident_id=inc.id, status=f"escalated:{nxt}",
            note=f"Escalation advanced: {current} -> {nxt}",
        ))
        advanced.append(inc.id)
        logger.warning("escalation_advanced", incident_id=inc.id, from_stage=current, to_stage=nxt)

    if advanced:
        db.commit()
    return advanced


def _notify_escalation(db: Session, inc: Incident, stage: str) -> None:
    """Emergency contacts (SMS, at the emergency_contact stage) plus the control
    room / assigned responders (push) -- see services/notifications.py."""
    if not inc.tourist_id:
        return
    tourist = db.get(Tourist, inc.tourist_id)
    if tourist:
        notifications.notify_escalation(db, inc, tourist, stage)


def _ensure_unit_assigned(db: Session, inc: Incident) -> None:
    if inc.assigned_unit_id is not None or inc.lat is None or inc.lng is None:
        return
    ranked = dispatch.rank_units(db, inc.lat, inc.lng)
    if not ranked:
        return
    inc.assigned_unit_id = ranked[0]["unit_id"]
    inc.status = "dispatched"
    inc.dispatched_at = utc_now()
