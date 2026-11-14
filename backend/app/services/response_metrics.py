"""Response metrics: acknowledgment timing, the dashboard countdown view and SLO.

Everything here is computed from STORED incident and acknowledgment timestamps
(`detected_at`, `acknowledged_at`, `response_deadline`, `slo_met` ...). There
are no simulated or hard-coded figures.

SLO definition (also in the README):

    RESPONSE SLO COMPLIANCE (%) =
        incidents acknowledged within their response deadline
        -----------------------------------------------------  x 100
        eligible incidents that have been acknowledged

* Eligible population: incidents that were given a response deadline by the
  response workflow and were DETECTED inside the reporting period (and match
  the filters). Incidents created before the feature, or imported by the seed
  scripts, have no deadline and are not eligible.
* The denominator is ACKNOWLEDGED eligible incidents only. An incident nobody
  has acknowledged is never counted as a success -- and not as a failure of
  the percentage either; it is reported separately as `unacknowledged`
  (and `overdue` once its deadline passed), so the percentage cannot be
  flattered by leaving incidents open.
* "Within deadline" means acknowledged_at <= response_deadline, evaluated with
  the deadline stored on the incident at acknowledgment time (so a severity
  change before acknowledgment is honoured). Acknowledgment is not resolution.
* Compliance is None (not 0%, not 100%) when nothing eligible has been
  acknowledged yet.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.incident import Incident
from app.models.police import PoliceUnit
from app.models.response import EscalationEvent
from app.models.user import User
from app.services import response_policy

SEVERITIES = response_policy.SEVERITIES


def tz(dt: datetime | None) -> str | None:
    """Naive-UTC column value -> unambiguous ISO-8601 with an explicit offset."""
    return dt.replace(tzinfo=UTC).isoformat() if dt else None


def ack_outcome(inc: Incident, now: datetime) -> tuple[float, bool | None]:
    """(seconds from detection to `now`, within-deadline?) -- met is None when
    the incident has no response deadline (not part of the SLO)."""
    seconds = max(0.0, (now - inc.detected_at).total_seconds())
    if inc.response_deadline is None:
        return seconds, None
    return seconds, now <= inc.response_deadline


# --------------------------------------------------------------------------- countdown view
def view(inc: Incident, now: datetime | None = None, *, unit_names: dict | None = None,
         user_names: dict | None = None) -> dict:
    """One incident as the responder/admin dashboards show it. `remaining_seconds`
    is computed here from the stored deadline; the browser only animates it."""
    now = now or utc_now()
    labels = response_policy.stage_labels()
    stage = max(1, min(3, inc.response_stage or 1))
    acked = inc.acknowledged_at is not None
    remaining = None
    if inc.response_deadline is not None and not acked:
        remaining = (inc.response_deadline - now).total_seconds()
    active = inc.escalation_status == "active"
    return {
        "incident_id": inc.id, "type": inc.type, "severity": inc.severity,
        "baseline_severity": inc.baseline_severity, "classification_reason": inc.classification_reason,
        "needs_review": bool(inc.needs_review), "status": inc.status,
        "tourist_id": inc.tourist_id,
        "assigned_unit_id": inc.assigned_unit_id,
        "assigned_unit_name": (unit_names or {}).get(inc.assigned_unit_id),
        "detected_at": tz(inc.detected_at), "assigned_at": tz(inc.assigned_at),
        "acknowledged_at": tz(inc.acknowledged_at), "acknowledged_by": inc.acknowledged_by,
        "acknowledged_by_name": (user_names or {}).get(inc.acknowledged_by),
        "dispatched_at": tz(inc.dispatched_at), "resolved_at": tz(inc.resolved_at),
        "acknowledged": acked, "acknowledgment_seconds": inc.acknowledgment_seconds, "slo_met": inc.slo_met,
        "response_target_seconds": inc.response_target_seconds,
        "response_deadline": tz(inc.response_deadline),
        "remaining_seconds": remaining,
        "overdue": remaining is not None and remaining < 0,
        "overdue_seconds": -remaining if remaining is not None and remaining < 0 else 0,
        "response_stage": stage, "stage_label": labels[stage - 1],
        "next_stage_label": labels[stage] if stage < 3 and active else None,
        "next_escalation_deadline": tz(inc.response_stage_deadline) if active else None,
        "global_timeout_deadline": tz(inc.global_timeout_deadline),
        "global_timeout_at": tz(inc.global_timeout_at),
        "escalation_status": inc.escalation_status,
        "managed": inc.response_deadline is not None,
        "server_time": tz(now),
    }


def lookups(db: Session, incidents: list[Incident]) -> tuple[dict, dict]:
    unit_ids = {i.assigned_unit_id for i in incidents if i.assigned_unit_id}
    user_ids = {i.acknowledged_by for i in incidents if i.acknowledged_by}
    units = {u.id: u.name for u in db.query(PoliceUnit).filter(PoliceUnit.id.in_(unit_ids)).all()} if unit_ids else {}
    users = {u.id: u.full_name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return units, users


def board(db: Session, *, unit_id: int | None = None, now: datetime | None = None,
          include_acknowledged: bool = True) -> dict:
    """Live board: every managed, unresolved incident, most urgent first.
    Unacknowledged incidents sort by remaining time (overdue first); critical
    before high before medium before low on ties."""
    now = now or utc_now()
    q = db.query(Incident).filter(Incident.response_deadline.isnot(None), Incident.status != "resolved")
    if unit_id is not None:
        q = q.filter(Incident.assigned_unit_id == unit_id)
    rows = q.all()
    if not include_acknowledged:
        rows = [r for r in rows if r.acknowledged_at is None]
    units, users = lookups(db, rows)
    items = [view(r, now, unit_names=units, user_names=users) for r in rows]
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    items.sort(key=lambda v: (v["acknowledged"],
                              v["remaining_seconds"] if v["remaining_seconds"] is not None else 1e12,
                              sev_rank.get(v["severity"], 9), v["incident_id"]))
    return {"server_time": tz(now), "items": items,
            "unacknowledged": sum(1 for v in items if not v["acknowledged"]),
            "overdue": sum(1 for v in items if v["overdue"])}


# --------------------------------------------------------------------------- SLO
class SloError(ValueError):
    pass


def _pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 1) if den else None


def _bucket_edges() -> list[int]:
    edges = []
    for p in settings.SLO_DISTRIBUTION_BUCKETS_SECONDS.split(","):
        p = p.strip()
        if p.isdigit():
            edges.append(int(p))
    return sorted(set(edges)) or [60, 300]


def _summary(rows: list[Incident], now: datetime) -> dict:
    acked = [r for r in rows if r.acknowledged_at is not None]
    within = [r for r in acked if r.slo_met is True]
    after = [r for r in acked if r.slo_met is False]
    unack = [r for r in rows if r.acknowledged_at is None]
    secs = [r.acknowledgment_seconds for r in acked if r.acknowledgment_seconds is not None]
    return {
        "received": len(rows), "acknowledged": len(acked),
        "within_target": len(within), "after_target": len(after),
        "unacknowledged": len(unack),
        "overdue": sum(1 for r in unack if r.response_deadline and r.response_deadline < now
                       and r.status != "resolved"),
        "escalated": sum(1 for r in rows if (r.response_stage or 1) >= 2 or r.escalation_status == "exhausted"),
        "global_timeouts": sum(1 for r in rows if r.global_timeout_at is not None),
        "compliance_pct": _pct(len(within), len(within) + len(after)),
        "avg_ack_seconds": round(sum(secs) / len(secs), 1) if secs else None,
        "median_ack_seconds": round(statistics.median(secs), 1) if secs else None,
    }


def slo(db: Session, *, start: datetime | None = None, end: datetime | None = None,
        severity: str | None = None, incident_type: str | None = None, unit_id: int | None = None,
        now: datetime | None = None) -> dict:
    """SLO report for [start, end] (naive UTC) with optional filters."""
    now = now or utc_now()
    end = end or now
    start = start or end - timedelta(days=settings.SLO_DEFAULT_RANGE_DAYS)
    if start > end:
        raise SloError("start must not be after end")
    if (end - start).days > settings.SLO_MAX_RANGE_DAYS:
        raise SloError(f"range must not exceed {settings.SLO_MAX_RANGE_DAYS} days")
    if severity and severity not in SEVERITIES:
        raise SloError(f"severity must be one of {', '.join(SEVERITIES)}")

    def base():
        q = db.query(Incident).filter(Incident.response_deadline.isnot(None))
        if severity:
            q = q.filter(Incident.severity == severity)
        if incident_type:
            q = q.filter(Incident.type == incident_type)
        if unit_id is not None:
            q = q.filter(Incident.assigned_unit_id == unit_id)
        return q

    rows = base().filter(Incident.detected_at >= start, Incident.detected_at <= end).all()
    active = base().filter(Incident.status != "resolved").all()
    acked = [r for r in rows if r.acknowledged_at is not None and r.acknowledgment_seconds is not None]

    by_severity = {s: _summary([r for r in rows if r.severity == s], now) for s in SEVERITIES}
    types = sorted({r.type for r in rows})
    by_type = [{"type": t, **_summary([r for r in rows if r.type == t], now)} for t in types]

    per_day: dict[str, list[Incident]] = defaultdict(list)
    for r in rows:
        per_day[r.detected_at.date().isoformat()].append(r)
    ids = [r.id for r in rows]
    esc_by_day: dict[str, int] = defaultdict(int)
    if ids:
        for ev in (db.query(EscalationEvent).filter(EscalationEvent.incident_id.in_(ids),
                                                    EscalationEvent.kind == "stage_advanced").all()):
            esc_by_day[ev.created_at.date().isoformat()] += 1
    days = sorted(set(per_day) | set(esc_by_day))
    trend = []
    for d in days:
        s = _summary(per_day.get(d, []), now)
        trend.append({"date": d, "received": s["received"], "acknowledged": s["acknowledged"],
                      "avg_ack_seconds": s["avg_ack_seconds"], "compliance_pct": s["compliance_pct"],
                      "escalations": esc_by_day.get(d, 0)})

    edges = _bucket_edges()
    dist = [{"label": f"≤{e}s", "max_seconds": e, "count": 0} for e in edges]
    dist.append({"label": f">{edges[-1]}s", "max_seconds": None, "count": 0})
    for r in acked:
        for b in dist:
            if b["max_seconds"] is None or r.acknowledgment_seconds <= b["max_seconds"]:
                b["count"] += 1
                break

    overall = _summary(rows, now)
    return {
        "period": {"start": tz(start), "end": tz(end)},
        "filters": {"severity": severity, "incident_type": incident_type, "unit_id": unit_id},
        "overall": overall,
        "active_incidents": len(active),
        "active_unacknowledged": sum(1 for r in active if r.acknowledged_at is None),
        "active_overdue": sum(1 for r in active if r.acknowledged_at is None and r.response_deadline < now),
        "by_severity": by_severity,
        "by_type": by_type,
        "trend": trend,
        "distribution": dist,
        "methodology": {
            "formula": "within_target / (within_target + after_target) x 100",
            "eligible": "incidents with a stored response deadline detected within the period and filters",
            "denominator": "eligible incidents that were acknowledged; unacknowledged incidents are reported "
                           "separately and never counted as successes",
            "within_target": "acknowledged_at <= response_deadline",
            "targets_illustrative": True,
        },
        "server_time": tz(now),
    }
