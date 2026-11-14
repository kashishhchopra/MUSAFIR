"""Intelligent response management API: severity, deadlines, escalation, SLO.

All business logic lives in services/severity_classifier.py,
services/response_policy.py, services/escalation.py and
services/response_metrics.py; this module only authorises and shapes responses.
Acknowledgment itself reuses the existing POST /incidents/{id}/acknowledge
(and PATCH) so there is one path, not two.
"""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_admin_or_responder
from app.api.incidents import _assert_can_update, _get_incident_or_404
from app.core.time import utc_now
from app.db.session import get_db
from app.models.response import EscalationEvent
from app.models.user import User
from app.schemas.response import ResponseConfigUpdate, SeverityOverride
from app.services import (
    audit,
    escalation,
    response_metrics,
    response_policy,
    severity_classifier,
)

router = APIRouter(tags=["response"])


def _naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def _events(db: Session, incident_id: int) -> list[dict]:
    rows = (db.query(EscalationEvent).filter(EscalationEvent.incident_id == incident_id)
            .order_by(EscalationEvent.id).all())
    return [{"id": e.id, "kind": e.kind, "from_stage": e.from_stage, "to_stage": e.to_stage,
             "note": e.note, "actor_id": e.actor_id, "created_at": response_metrics.tz(e.created_at)}
            for e in rows]


def _classifications(db: Session, incident_id: int) -> list[dict]:
    out = []
    for row in severity_classifier.history(db, incident_id):
        d = severity_classifier.serialize(row)
        d["classified_at"] = response_metrics.tz(row.created_at)
        out.append(d)
    return out


# ------------------------------------------------------------------ configuration
@router.get("/response/config")
def get_config(db: Session = Depends(get_db), _: User = Depends(require_admin_or_responder)):
    cfg = response_policy.config(db)
    cfg["updated_at"] = response_metrics.tz(cfg["updated_at"])
    return cfg


@router.put("/response/config")
def put_config(payload: ResponseConfigUpdate, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_admin)):
    """Admin: change response targets / stage-2 duration / global timeout. Applies to
    incidents opened from now on; open incidents keep the deadlines they were given."""
    changes = payload.model_dump(exclude_none=True)
    try:
        cfg = response_policy.update(db, changes, user.id)
    except response_policy.PolicyError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    audit.record(db, "response_config_update", actor=user.email, target="response_settings",
                 detail=str(changes), request=request)
    cfg["updated_at"] = response_metrics.tz(cfg["updated_at"])
    return cfg


# ------------------------------------------------------------------ live board
@router.get("/response/board")
def get_board(include_acknowledged: bool = True, db: Session = Depends(get_db),
              user: User = Depends(require_admin_or_responder)):
    """Managed, unresolved incidents with server-computed remaining time. A
    responder sees only the incidents assigned to their unit."""
    unit = user.unit_id if user.role == "responder" else None
    if user.role == "responder" and unit is None:
        return {"server_time": response_metrics.tz(utc_now()), "items": [], "unacknowledged": 0, "overdue": 0}
    return response_metrics.board(db, unit_id=unit, include_acknowledged=include_acknowledged)


# ------------------------------------------------------------------ per-incident
@router.get("/incidents/{incident_id}/response")
def incident_response(incident_id: int, db: Session = Depends(get_db),
                      user: User = Depends(require_admin_or_responder)):
    inc = _get_incident_or_404(incident_id, db)
    _assert_can_update(inc, user)
    units, users = response_metrics.lookups(db, [inc])
    hist = _classifications(db, inc.id)
    return {"response": response_metrics.view(inc, unit_names=units, user_names=users),
            "classification": hist[-1] if hist else None,
            "escalation_events": _events(db, inc.id)}


@router.get("/incidents/{incident_id}/classification")
def incident_classification(incident_id: int, db: Session = Depends(get_db),
                            user: User = Depends(require_admin_or_responder)):
    inc = _get_incident_or_404(incident_id, db)
    _assert_can_update(inc, user)
    hist = _classifications(db, inc.id)
    return {"incident_id": inc.id, "severity": inc.severity, "baseline_severity": inc.baseline_severity,
            "reason": inc.classification_reason, "needs_review": inc.needs_review,
            "current": hist[-1] if hist else None, "history": hist}


@router.get("/incidents/{incident_id}/escalations")
def incident_escalations(incident_id: int, db: Session = Depends(get_db),
                         user: User = Depends(require_admin_or_responder)):
    inc = _get_incident_or_404(incident_id, db)
    _assert_can_update(inc, user)
    stage = max(1, min(3, inc.response_stage or 1))
    return {"incident_id": inc.id, "response_stage": stage,
            "stage_label": response_policy.stage_labels()[stage - 1],
            "escalation_status": inc.escalation_status,
            "next_escalation_deadline": response_metrics.tz(inc.response_stage_deadline),
            "global_timeout_deadline": response_metrics.tz(inc.global_timeout_deadline),
            "global_timeout_at": response_metrics.tz(inc.global_timeout_at),
            "events": _events(db, inc.id)}


@router.post("/incidents/{incident_id}/severity")
def override_severity(incident_id: int, payload: SeverityOverride, request: Request,
                      db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Admin manual override. The reason is mandatory and the change is audited."""
    inc = _get_incident_or_404(incident_id, db)
    try:
        previous = escalation.override_severity(db, inc, payload.severity, payload.reason, user)
    except severity_classifier.OverrideError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    db.commit()
    audit.record(db, "severity_override", actor=user.email, target=f"incident:{inc.id}",
                 detail=f"{previous} -> {inc.severity}: {payload.reason}", request=request)
    units, users = response_metrics.lookups(db, [inc])
    return {"previous_severity": previous,
            "response": response_metrics.view(inc, unit_names=units, user_names=users)}


@router.post("/incidents/{incident_id}/reclassify")
def reclassify(incident_id: int, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_admin)):
    """Admin: re-run the rules with the evidence available now. Severity can only go up."""
    inc = _get_incident_or_404(incident_id, db)
    changed = escalation.reclassify(db, inc)
    db.commit()
    audit.record(db, "severity_reclassify", actor=user.email, target=f"incident:{inc.id}",
                 detail=f"changed={changed} severity={inc.severity}", request=request)
    return {"changed": changed, "severity": inc.severity, "reason": inc.classification_reason}


# ------------------------------------------------------------------ SLO
def _slo(db: Session, start, end, severity, incident_type, unit_id):
    try:
        return response_metrics.slo(db, start=_naive_utc(start), end=_naive_utc(end), severity=severity,
                                    incident_type=incident_type, unit_id=unit_id)
    except response_metrics.SloError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/response/slo")
def get_slo(start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
            severity: str | None = None, incident_type: str | None = None, unit_id: int | None = None,
            db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _slo(db, start, end, severity, incident_type, unit_id)


@router.get("/response/slo/by-severity")
def get_slo_by_severity(start: datetime | None = Query(None, alias="from"),
                        end: datetime | None = Query(None, alias="to"),
                        incident_type: str | None = None, unit_id: int | None = None,
                        db: Session = Depends(get_db), _: User = Depends(require_admin)):
    r = _slo(db, start, end, None, incident_type, unit_id)
    return {"period": r["period"], "by_severity": r["by_severity"], "methodology": r["methodology"]}
