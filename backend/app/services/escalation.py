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
    # Responder-authority chain (severity-based targets, stages 1-3, global
    # timeout): same job, same transaction discipline -- see the section below.
    try:
        tick_response_stages(db, now)
    except Exception:  # noqa: BLE001 -- must never break the original chain
        db.rollback()
        logger.exception("response_tick_failed")
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


# =============================================================================
# Intelligent response management: staged responder escalation + global timeout
# =============================================================================
# The functions above are the ORIGINAL outreach chain (control room -> tourist's
# emergency contacts -> unit dispatch) and are unchanged. What follows is the
# responder-authority chain driven by the severity-based response target:
#
#   stage 1  primary responder   from detection until `response_deadline`
#   stage 2  supervisor          for RESPONSE_STAGE2_SECONDS after that
#   stage 3  higher authority    until the global timeout
#   global timeout               one fixed bound; the incident STAYS OPEN
#
# Both chains run from the same scheduler job (`tick_escalations`) and both are
# stopped by the same human acknowledgement. Every state change here is a
# conditional UPDATE (claim) followed by an idempotent EscalationEvent, so two
# workers, a replayed tick, or an acknowledgement racing a stage change can
# never produce a duplicate event, a duplicate notification, or a lost ack.
from sqlalchemy import or_, update  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from app.models.response import EscalationEvent  # noqa: E402
from app.services import response_metrics, response_policy, severity_classifier  # noqa: E402
from app.websocket.manager import broadcast_sync  # noqa: E402


def response_enabled() -> bool:
    return settings.RESPONSE_MANAGEMENT_ENABLED


def _record(db: Session, incident_id: int, kind: str, dedupe_key: str, *, from_stage: int | None = None,
            to_stage: int | None = None, note: str = "", actor_id: int | None = None,
            now=None) -> bool:
    """Insert one EscalationEvent. False when this exact event already exists."""
    try:
        with db.begin_nested():
            db.add(EscalationEvent(
                incident_id=incident_id, kind=kind, dedupe_key=dedupe_key, from_stage=from_stage,
                to_stage=to_stage, note=note, actor_id=actor_id, created_at=now or utc_now()))
            db.flush()
        return True
    except IntegrityError:
        return False


def _iso(dt) -> str:
    return dt.strftime("%H:%M:%S UTC") if dt else "n/a"


def _notify(db: Session, inc: Incident, kind: str, key: str, audience: list[str], headline: str,
            why: str, exclude_user_id: int | None = None) -> None:
    tourist = db.get(Tourist, inc.tourist_id) if inc.tourist_id else None
    labels = response_policy.stage_labels()
    stage = max(1, min(3, inc.response_stage or 1))
    place = notifications._place(db, inc.lat, inc.lng) if inc.lat is not None else "location unavailable"
    body = (f"#{inc.id} {inc.type.replace('_', ' ')} · {inc.severity.upper()} · stage {stage} "
            f"({labels[stage - 1]}) · respond by {_iso(inc.response_deadline)} · {why} · "
            f"{(inc.classification_reason or '')[:160]} · {place}")
    try:
        notifications.notify_response(db, inc, tourist, kind=kind, key=key, audience=audience,
                                      title=headline, body=body, exclude_user_id=exclude_user_id)
    except Exception:  # noqa: BLE001 -- a notification problem must never lose the escalation event
        logger.exception("response_notify_failed", incident_id=inc.id, kind=kind)


def _broadcast(inc: Incident, kind: str) -> None:
    broadcast_sync({"event": "incident_response", "id": inc.id, "kind": kind, "severity": inc.severity,
                    "stage": inc.response_stage, "escalation_status": inc.escalation_status,
                    "status": inc.status})


# ------------------------------------------------------------------ opening
def _assign_targets(db: Session, inc: Incident, now) -> None:
    from datetime import timedelta as _td

    target = response_policy.target_seconds(db, inc.severity)
    detected = inc.detected_at or now
    inc.detected_at = detected
    inc.response_target_seconds = target
    inc.response_deadline = detected + _td(seconds=target)
    inc.escalation_started_at = detected
    inc.global_timeout_deadline = detected + _td(seconds=response_policy.global_timeout_seconds(db))
    inc.response_stage = 1
    inc.response_stage_deadline = min(inc.response_deadline, inc.global_timeout_deadline)
    inc.escalation_status = "active"
    if inc.assigned_unit_id or inc.station_id:
        inc.assigned_at = inc.assigned_at or now


def open_workflow(db: Session, inc: Incident, tourist: Tourist | None = None, *,
                  evidence: dict | None = None, now=None) -> Incident:
    """The single entry point every incident passes through, in this order:
    classify severity -> assign response target/deadlines -> record assignment
    -> notify the stage-1 audience. Idempotent (a classified incident is left
    alone). Caller commits; the incident must already be flushed."""
    if not response_enabled() or inc.classified_at is not None:
        return inc
    now = now or utc_now()
    severity_classifier.classify_incident(db, inc, tourist, evidence=evidence, now=now)
    _assign_targets(db, inc, now)
    _record(db, inc.id, "assigned", "assigned", to_stage=1, now=now,
            note=(f"Classified {inc.severity.upper()}; respond within {inc.response_target_seconds}s "
                  f"(by {_iso(inc.response_deadline)}). {inc.classification_reason}"))
    if inc.type not in {p.strip() for p in settings.RESPONSE_SKIP_ASSIGNED_NOTIFY_TYPES.split(",")}:
        _notify(db, inc, "assigned", "1", response_policy.audience(1),
                f"New {inc.severity.upper()} incident #{inc.id}", "new incident assigned")
    _reevaluate_siblings(db, inc, now)
    return inc


def mark_assigned(inc: Incident, now=None) -> None:
    """Record when a unit/station was first assigned (assignment != acknowledgment)."""
    if inc.assigned_at is None:
        inc.assigned_at = now or utc_now()


# ------------------------------------------------------------------ severity changes
def retarget(db: Session, inc: Incident, previous: str, *, trigger: str, actor_id: int | None = None,
             now=None) -> None:
    """A severity change moves the response deadline by documented rules:

    * upgrade   -> deadline = min(old deadline, now + new target): it can only
                   tighten, never give a responder more time.
    * downgrade -> (admin only) detected_at + new target.
    * Already acknowledged: the deadline is left alone (the SLO was decided).
    The original detected_at / escalation_started_at / global deadline never move.
    """
    from datetime import timedelta as _td

    now = now or utc_now()
    last = severity_classifier.latest(db, inc.id)
    key = f"severity:{last.id if last else now.isoformat()}"
    note = f"Severity {previous.upper()} -> {inc.severity.upper()} ({trigger})."
    if (inc.escalation_status == "active" and inc.acknowledged_at is None
            and inc.response_deadline is not None):
        new_target = response_policy.target_seconds(db, inc.severity)
        if severity_classifier.rank(inc.severity) > severity_classifier.rank(previous):
            deadline = min(inc.response_deadline, now + _td(seconds=new_target))
        else:
            deadline = (inc.detected_at or now) + _td(seconds=new_target)
        old = inc.response_deadline
        inc.response_target_seconds = new_target
        inc.response_deadline = deadline
        if inc.response_stage == 1:
            inc.response_stage_deadline = min(deadline, inc.global_timeout_deadline or deadline)
        note += f" Response deadline {_iso(old)} -> {_iso(deadline)}."
    if _record(db, inc.id, "severity_changed", key, note=note, actor_id=actor_id, now=now):
        db.add(IncidentEvent(incident_id=inc.id, status="severity_changed", note=note))
        stage = max(1, min(3, inc.response_stage or 1))
        _notify(db, inc, "severity", key, response_policy.audience(stage) + ["admins"],
                f"Incident #{inc.id} is now {inc.severity.upper()}", f"severity changed ({trigger})")
        _broadcast(inc, "severity_changed")


def _reevaluate_siblings(db: Session, new_inc: Incident, now) -> None:
    """A new incident for a tourist is new evidence about their other open
    incidents. Re-run their rules; severity can go up, never down."""
    if not new_inc.tourist_id:
        return
    siblings = (db.query(Incident).filter(Incident.tourist_id == new_inc.tourist_id,
                                          Incident.id != new_inc.id, Incident.status != "resolved",
                                          Incident.classified_at.isnot(None)).all())
    for sib in siblings:
        changed, previous = severity_classifier.reevaluate(db, sib, now=now)
        if changed and previous:
            retarget(db, sib, previous, trigger="new related incident", now=now)


def reclassify(db: Session, inc: Incident, *, now=None) -> bool:
    """Re-run the rules on demand (admin) with the evidence available now."""
    changed, previous = severity_classifier.reevaluate(db, inc, now=now)
    if changed and previous:
        retarget(db, inc, previous, trigger="re-evaluation", now=now)
    return changed


def override_severity(db: Session, inc: Incident, severity: str, reason: str, actor, *, now=None) -> str:
    """Admin override with mandatory reason. Returns the previous severity."""
    now = now or utc_now()
    previous = severity_classifier.override(db, inc, severity, reason, actor.id, now=now)
    if previous != inc.severity:
        retarget(db, inc, previous, trigger=f"manual override by {actor.email}", actor_id=actor.id, now=now)
    else:
        _record(db, inc.id, "severity_changed", f"override-noop:{now.isoformat()}", actor_id=actor.id, now=now,
                note=f"Severity {inc.severity.upper()} confirmed by {actor.email}: {reason}")
    return previous


# ------------------------------------------------------------------ acknowledgment / closure
def acknowledge(db: Session, inc: Incident, user, *, now=None) -> tuple[Incident, bool]:
    """Record a human acknowledgement exactly once. Returns (incident, first).

    The claim is `UPDATE ... WHERE acknowledged_at IS NULL`: of two responders
    acknowledging at the same instant exactly one wins; the other gets the
    winner's record back (first=False) and nothing is duplicated. Acknowledging
    does NOT resolve the incident -- status, dispatch and resolution have their
    own timestamps."""
    now = now or utc_now()
    for _ in range(3):
        db.refresh(inc)
        if inc.acknowledged_at is not None:
            return inc, False
        seconds, met = response_metrics.ack_outcome(inc, now)
        q = update(Incident).where(Incident.id == inc.id, Incident.acknowledged_at.is_(None))
        if inc.response_deadline is not None:
            q = q.where(Incident.response_deadline == inc.response_deadline)
        won = db.execute(q.values(acknowledged_at=now, acknowledged_by=user.id,
                                  acknowledgment_seconds=seconds, slo_met=met),
                         execution_options={"synchronize_session": False}).rowcount
        if won:
            break
    else:
        db.refresh(inc)
        return inc, False
    db.execute(update(Incident).where(Incident.id == inc.id, Incident.escalation_status == "active")
               .values(escalation_status="acknowledged", response_stage_deadline=None),
               execution_options={"synchronize_session": False})
    db.refresh(inc)
    verdict = ("within target" if met else "AFTER the target") if met is not None else "no target set"
    note = (f"Acknowledged by {user.email} after {seconds:.0f}s ({verdict}); "
            f"stage {inc.response_stage}.")
    _record(db, inc.id, "acknowledged", "acknowledged", from_stage=inc.response_stage, actor_id=user.id,
            note=note, now=now)
    if inc.escalation_status in ("acknowledged", "exhausted") and inc.response_deadline is not None:
        _notify(db, inc, "ack", "ack", ["admins"], f"Incident #{inc.id} acknowledged", note,
                exclude_user_id=user.id)
    _broadcast(inc, "acknowledged")
    return inc, True


def close_workflow(db: Session, inc: Incident, now=None) -> None:
    """The incident was resolved by a human: stop tracking. Caller commits."""
    if inc.escalation_status in ("active", "acknowledged", "exhausted"):
        inc.escalation_status = "closed"
        inc.response_stage_deadline = None
        _record(db, inc.id, "resolved", "resolved", note="Incident resolved.", now=now)
        _broadcast(inc, "resolved")


# ------------------------------------------------------------------ the tick
def tick_response_stages(db: Session, now=None) -> dict:
    """Advance every overdue, unacknowledged incident; enforce the global
    timeout. Safe to run concurrently or repeatedly. Pending work lives in the
    database (stage/global deadlines), so a restarted backend simply continues."""
    result = {"advanced": [], "timed_out": [], "warned": []}
    if not response_enabled():
        return result
    now = now or utc_now()
    open_q = db.query(Incident).filter(Incident.escalation_status == "active",
                                       Incident.status != "resolved", Incident.acknowledged_at.is_(None))
    warn = response_policy.warning_seconds(db)
    if warn:
        for inc in open_q.filter(Incident.response_stage == 1, Incident.response_deadline > now).all():
            if (inc.response_deadline - now).total_seconds() <= warn and _record(
                    db, inc.id, "warning", "warning", from_stage=1, now=now,
                    note=f"Response deadline {_iso(inc.response_deadline)} is approaching."):
                _notify(db, inc, "warning", "warn", response_policy.audience(1),
                        f"Incident #{inc.id}: respond soon", "response deadline approaching")
                db.commit()
                result["warned"].append(inc.id)
    ids = [i for (i,) in open_q.filter(or_(Incident.response_stage_deadline <= now,
                                           Incident.global_timeout_deadline <= now))
           .with_entities(Incident.id).order_by(Incident.id).all()]
    for iid in ids:
        try:
            _process(db, iid, now, result)
        except Exception:  # noqa: BLE001 -- one bad incident must not stall the rest
            db.rollback()
            logger.exception("response_escalation_failed", incident_id=iid)
    return result


def _process(db: Session, iid: int, now, result: dict) -> None:
    for _ in range(4):  # at most stage 1->2, 2->3, timeout, with one spare
        inc = db.get(Incident, iid)
        db.refresh(inc)
        if (inc.escalation_status != "active" or inc.acknowledged_at is not None
                or inc.status == "resolved"):
            return
        if inc.global_timeout_deadline is not None and inc.global_timeout_deadline <= now:
            if _exhaust(db, inc, now):
                result["timed_out"].append(iid)
            return
        if inc.response_stage_deadline is None or inc.response_stage_deadline > now:
            return
        if not _advance(db, inc, now):
            return
        result["advanced"].append(iid)


def _advance(db: Session, inc: Incident, now) -> bool:
    from datetime import timedelta as _td

    seen = inc.response_stage
    if seen >= 3:
        return False
    new = seen + 1
    ceiling = inc.global_timeout_deadline
    if new == 2:
        nxt = inc.response_stage_deadline + _td(seconds=response_policy.stage2_seconds(db))
        if nxt <= now:  # backend was down: give the new authority a real window
            nxt = now + _td(seconds=response_policy.stage2_seconds(db))
        nxt = min(nxt, ceiling) if ceiling else nxt
    else:
        nxt = ceiling  # stage 3 lasts until the global timeout
    missed_deadline = inc.response_deadline
    claimed = db.execute(
        update(Incident).where(Incident.id == inc.id, Incident.response_stage == seen,
                               Incident.escalation_status == "active", Incident.acknowledged_at.is_(None),
                               Incident.status != "resolved")
        .values(response_stage=new, response_stage_deadline=nxt),
        execution_options={"synchronize_session": False}).rowcount
    if not claimed:
        db.rollback()
        return False
    db.refresh(inc)
    label = response_policy.stage_labels()[new - 1]
    if seen == 1:
        _record(db, inc.id, "deadline_missed", "deadline_missed", from_stage=1, now=now,
                note=f"No acknowledgment by the response deadline {_iso(missed_deadline)}.")
    note = f"Escalated stage {seen} -> {new} ({label}); next deadline {_iso(nxt)}."
    if _record(db, inc.id, "stage_advanced", f"stage:{new}", from_stage=seen, to_stage=new, note=note, now=now):
        db.add(IncidentEvent(incident_id=inc.id, status=f"response_stage:{new}", note=note))
        why = ("response deadline missed" if seen == 1 else f"stage {seen} not acknowledged in time")
        _notify(db, inc, "stage", str(new), response_policy.audience(new),
                f"⚠ Incident #{inc.id} escalated to {label}", why)
        if new == 3:
            _notify(db, inc, "urgent", "3", ["admins"], f"URGENT: incident #{inc.id} needs attention",
                    "stage 3 reached")
    db.commit()
    _broadcast(inc, "stage_advanced")
    logger.warning("response_stage_advanced", incident_id=inc.id, from_stage=seen, to_stage=new)
    return True


def _exhaust(db: Session, inc: Incident, now) -> bool:
    claimed = db.execute(
        update(Incident).where(Incident.id == inc.id, Incident.escalation_status == "active",
                               Incident.acknowledged_at.is_(None), Incident.status != "resolved")
        .values(escalation_status="exhausted", global_timeout_at=now, response_stage_deadline=None),
        execution_options={"synchronize_session": False}).rowcount
    if not claimed:
        db.rollback()
        return False
    db.refresh(inc)
    if inc.response_deadline is not None and inc.response_deadline <= now:
        _record(db, inc.id, "deadline_missed", "deadline_missed", from_stage=inc.response_stage, now=now,
                note=f"No acknowledgment by the response deadline {_iso(inc.response_deadline)}.")
    note = (f"Global escalation timeout reached at {_iso(now)} (limit {_iso(inc.global_timeout_deadline)}). "
            f"The incident remains OPEN for manual intervention.")
    if _record(db, inc.id, "global_timeout", "global_timeout", from_stage=inc.response_stage, note=note, now=now):
        db.add(IncidentEvent(incident_id=inc.id, status="response_global_timeout", note=note))
        _notify(db, inc, "timeout", "global", response_policy.audience("global"),
                f"🚨 Incident #{inc.id}: escalation exhausted, still unacknowledged",
                "global timeout reached")
    db.commit()
    _broadcast(inc, "global_timeout")
    logger.warning("response_global_timeout", incident_id=inc.id)
    return True
