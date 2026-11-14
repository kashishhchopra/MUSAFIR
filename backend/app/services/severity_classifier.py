"""Intelligent incident severity classification (CRITICAL / HIGH / MEDIUM / LOW).

This is step one of the response pipeline and runs BEFORE any response target
is assigned:

    classify (this module) -> response_policy / escalation.assign -> escalation
    -> response_metrics (SLO)

It is a transparent, deterministic, rule-based engine. Nothing is random and
nothing is learned; every decision is stored (severity_classifications) with the
rules that fired, the evidence that was actually available, and a plain-English
reason. The numbers it uses live in app/core/config.py (SEVERITY_*), not here.

Order of evaluation (matches the spec):

 1. identify the incident type
 2. gather the evidence that actually exists (DB + what the producer passed)
 3. baseline severity from the type, raised to any tourist-reported urgency
 4. count corroborating risk indicators
 5. every SEVERITY_POINTS_PER_LEVEL indicators raise the baseline one level
    (capped by SEVERITY_MAX_LEVEL_BUMP)
 6. explicit safety rules: an SOS is always CRITICAL and is never held back
    waiting for more evidence; a motion sensor alone can never make an incident
    CRITICAL; a producer's declared severity is a floor (never silently lowered)
 7. final severity + needs_review flag
 8. human-readable reason
 9. caller stores it and hands the severity to the escalation engine

Honesty rules, enforced in code and covered by tests:

 * Missing data is NOT danger. An unknown sensor reading, absent GPS or an
   empty history adds no indicator and never raises severity.
 * A suspected event is described as suspected. The reason text never claims
   an incident is "confirmed".
 * Low-confidence / conflicting evidence sets `needs_review` for a human; it
   does not quietly downgrade anything.
 * Automatic re-evaluation can raise severity but never lowers it. Only an
   administrator, with a written reason, can lower it (`override`).

The only numeric "risk score" used is the existing `Tourist.safety_score`
(0-100, higher = safer; bands in services/safety.py: >=75 safe, >=50 caution,
>=25 risky, below 25 critical-risk). It is read, never recomputed here; a score
at or below SEVERITY_LOW_SAFETY_SCORE adds ONE indicator. It is a coarse input:
it describes the tourist's recent context, not this incident, and a missing
score adds nothing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.checkin import CheckIn
from app.models.incident import Incident
from app.models.response import SeverityClassification
from app.models.tourist import Tourist
from app.models.zone import Zone
from app.services.geo import zones_containing_point

logger = get_logger(__name__)

LEVELS = ("low", "medium", "high", "critical")
MIN_OVERRIDE_REASON = 5


def rank(severity: str | None) -> int:
    try:
        return LEVELS.index((severity or "").lower())
    except ValueError:
        return -1


def _level(i: int) -> str:
    return LEVELS[max(0, min(len(LEVELS) - 1, i))]


def _csv(value: str) -> set[str]:
    return {p.strip() for p in (value or "").split(",") if p.strip()}


def _json_map(raw: str, fallback: dict) -> dict:
    try:
        data = json.loads(raw)
        return {str(k): str(v).lower() for k, v in data.items() if str(v).lower() in LEVELS}
    except (ValueError, AttributeError, TypeError):
        logger.warning("severity_setting_unparseable", raw=raw[:80])
        return fallback


def baseline_for(itype: str) -> tuple[str, str]:
    """(severity, rule detail) for an incident type. Unlisted types use the default."""
    table = _json_map(settings.SEVERITY_BASELINE_BY_TYPE, {})
    if itype in table:
        return table[itype], f"type '{itype}' baseline"
    default = settings.SEVERITY_DEFAULT_BASELINE.lower()
    return (default if default in LEVELS else "medium"), f"type '{itype}' has no configured baseline; default"


# --------------------------------------------------------------------------- context
def _sensor_strength(itype: str, peak_g) -> str:
    """strong | weak | unknown, from the stored peak acceleration and the SAME
    detector thresholds the on-device detector used (FALL_IMPACT_G /
    FALL_CRASH_IMPACT_G). No peak reading -> 'unknown' (not weak, not strong)."""
    if peak_g is None:
        return "unknown"
    threshold = settings.FALL_CRASH_IMPACT_G if itype == "crash_detected" else settings.FALL_IMPACT_G
    return "strong" if float(peak_g) >= threshold else "weak"


def build_context(db: Session, inc: Incident, tourist: Tourist | None = None, *,
                  evidence: dict | None = None, declared: str | None = None,
                  now: datetime | None = None) -> dict:
    """Collect only information that really exists. `evidence` is what the
    producing module knows (e.g. no_response, peak_g, missed_checkins)."""
    now = now or utc_now()
    ev = dict(evidence or {})
    tourist = tourist or (db.get(Tourist, inc.tourist_id) if inc.tourist_id else None)
    itype = inc.type
    ctx: dict = {
        "type": itype,
        "declared": declared.lower() if declared and declared.lower() in LEVELS else None,
        "explicit_sos": itype == "sos" or bool(ev.get("explicit_sos")),
        "silent": bool(inc.silent),
        "user_urgency": (str(ev["user_urgency"]).lower() if ev.get("user_urgency") else None),
        "no_response": bool(ev.get("no_response")) and itype in _csv(settings.SEVERITY_UNRESPONSIVE_TYPES),
        "sensor_source": ev.get("source"),
        "simulated": bool(ev.get("simulated")),
        "missed_checkins": int(ev.get("missed_checkins") or 0),
        "gps_unavailable": bool(ev.get("gps_unavailable")) or (inc.lat is None and inc.lng is None),
        "distress": bool(inc.distress_detected or ev.get("distress")),
    }
    ctx["sensor_strength"] = (
        _sensor_strength(itype, ev.get("peak_g")) if itype in _csv(settings.SEVERITY_SENSOR_TYPES) else None
    )

    window = now - timedelta(minutes=settings.SEVERITY_PRIOR_INCIDENT_WINDOW_MINUTES)
    prior = prior_missed = 0
    if inc.tourist_id:
        q = db.query(Incident).filter(Incident.tourist_id == inc.tourist_id, Incident.id != inc.id,
                                      Incident.detected_at >= window)
        prior = q.with_entities(func.count(Incident.id)).scalar() or 0
        prior_missed = q.filter(Incident.type == "missed_checkin").with_entities(func.count(Incident.id)).scalar() or 0
    ctx["prior_incidents"] = prior
    ctx["repeated_miss"] = itype == "missed_checkin" and (ctx["missed_checkins"] >= 2 or prior_missed >= 1)

    ctx["in_high_risk_zone"] = False
    if inc.lat is not None and inc.lng is not None:
        zones = zones_containing_point(inc.lat, inc.lng, db.query(Zone).all())
        ctx["in_high_risk_zone"] = any(z.risk_level in ("high", "restricted") for z in zones)

    score = getattr(tourist, "safety_score", None)
    ctx["risk_score"] = float(score) if score is not None else None
    ctx["low_safety_score"] = score is not None and float(score) <= settings.SEVERITY_LOW_SAFETY_SCORE
    ctx["tourist_status_sos"] = bool(tourist and tourist.status == "sos" and itype != "sos")

    # a very recent safe check-in is evidence AGAINST danger (conflicting evidence)
    ctx["safe_signal"] = False
    if inc.tourist_id:
        since = now - timedelta(seconds=settings.SEVERITY_SAFE_SIGNAL_WINDOW_SECONDS)
        recent = (db.query(CheckIn).filter(CheckIn.tourist_id == inc.tourist_id)
                  .filter((CheckIn.last_checkin_at >= since) | (CheckIn.checked_in_at >= since)).first())
        ctx["safe_signal"] = recent is not None
    return ctx


# --------------------------------------------------------------------------- engine
@dataclass
class Classification:
    severity: str
    baseline: str
    rules: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    reason: str = ""
    risk_score: float | None = None
    needs_review: bool = False
    points: int = 0


_TYPE_PHRASE = {
    "sos": "the tourist triggered an SOS",
    "fall_detected": "a possible fall was detected (suspected, not confirmed)",
    "crash_detected": "a possible crash was detected (suspected, not confirmed)",
    "missed_checkin": "a Walk-Me-Home / check-in deadline was missed",
    "anomaly": "an unusual movement pattern was detected",
    "geofence": "the tourist entered a risk zone",
    "safety_report": "the tourist filed a safety report",
    "field_report": "a field report was filed",
    "missing_person": "the tourist was reported missing",
}

_INDICATOR_TEXT = {
    "no_response": "the tourist has not responded to the safety confirmation request",
    "strong_sensor": "the sensor reading is strong",
    "repeated_miss": "check-ins have been missed repeatedly",
    "prior_incident": "the tourist has other recent incidents",
    "high_risk_zone": "the location is in a high-risk zone",
    "low_safety_score": "the tourist's safety score is low",
    "distress": "distress language was detected in the tourist's own words",
    "tourist_status_sos": "the tourist is already in SOS status",
}


def _indicators(ctx: dict) -> list[str]:
    out = []
    if ctx["no_response"]:
        out.append("no_response")
    if ctx["sensor_strength"] == "strong":
        out.append("strong_sensor")
    if ctx["repeated_miss"]:
        out.append("repeated_miss")
    if ctx["prior_incidents"] > 0:
        out.append("prior_incident")
    if ctx["in_high_risk_zone"]:
        out.append("high_risk_zone")
    if ctx["low_safety_score"]:
        out.append("low_safety_score")
    if ctx["distress"]:
        out.append("distress")
    if ctx["tourist_status_sos"]:
        out.append("tourist_status_sos")
    return out


def classify(ctx: dict) -> Classification:
    """Pure function: context in, decision out. No database access."""
    itype = ctx["type"]
    rules: list[dict] = []
    review = False

    # 3. baseline from type, raised to reported urgency
    baseline, why = baseline_for(itype)
    rules.append({"id": "baseline", "effect": baseline, "detail": why})
    sev_i = rank(baseline)
    urgency = _json_map(settings.SEVERITY_URGENCY_MAP, {}).get(ctx.get("user_urgency") or "")
    if urgency and rank(urgency) > sev_i:
        sev_i = rank(urgency)
        rules.append({"id": "user_urgency", "effect": urgency,
                      "detail": f"tourist-reported urgency '{ctx['user_urgency']}' is higher than the type baseline"})
    base_after_urgency = _level(sev_i)

    # 4/5. corroborating indicators raise the baseline
    inds = _indicators(ctx)
    per = max(1, settings.SEVERITY_POINTS_PER_LEVEL)
    bump = min(settings.SEVERITY_MAX_LEVEL_BUMP, len(inds) // per)
    if inds:
        rules.append({"id": "indicators", "effect": f"+{bump}" if bump else "+0",
                      "detail": f"{len(inds)} corroborating indicator(s): {', '.join(inds)}; "
                                f"{per} needed per level"})
    sev_i += bump

    # explicit safety rules --------------------------------------------------
    if ctx["explicit_sos"] and sev_i < rank("critical"):
        sev_i = rank("critical")
        rules.append({"id": "explicit_sos", "effect": "critical",
                      "detail": "an explicit SOS is always critical and is not delayed for more evidence"})
    sensor_type = itype in _csv(settings.SEVERITY_SENSOR_TYPES)
    if sensor_type:
        non_sensor = [i for i in inds if i != "strong_sensor"]
        if sev_i >= rank("critical") and not non_sensor:
            sev_i = rank("high")
            review = True
            rules.append({"id": "sensor_alone_cap", "effect": "high",
                          "detail": "a motion sensor alone cannot make an incident critical; held at high for review"})
        if ctx["sensor_strength"] == "weak":
            review = True
            rules.append({"id": "low_confidence_sensor", "effect": "review",
                          "detail": "the stored sensor reading is below the detector threshold; flagged for human review"})
    if ctx["safe_signal"] and not ctx["explicit_sos"]:
        review = True
        rules.append({"id": "conflicting_evidence", "effect": "review",
                      "detail": "the tourist checked in safely very recently, which conflicts with this alert"})
    if ctx["gps_unavailable"]:
        rules.append({"id": "gps_unavailable", "effect": "none",
                      "detail": "no location available; this does not raise severity"})

    computed = _level(sev_i)

    # source floor: never silently lower what the producing module declared
    final = computed
    declared = ctx.get("declared")
    if settings.SEVERITY_SOURCE_FLOOR_ENABLED and declared and rank(declared) > rank(computed):
        final = declared
        rules.append({"id": "source_floor", "effect": declared,
                      "detail": f"the module that raised this incident declared {declared}; the rules alone "
                                f"give {computed}. Kept (never lowered automatically)"})
        if rank(declared) - rank(computed) >= 2 and not ctx["explicit_sos"]:
            review = True

    evidence = {k: ctx[k] for k in (
        "type", "declared", "user_urgency", "no_response", "sensor_source", "sensor_strength", "simulated",
        "missed_checkins", "gps_unavailable", "prior_incidents", "repeated_miss", "in_high_risk_zone",
        "low_safety_score", "distress", "tourist_status_sos", "safe_signal", "risk_score",
    ) if k in ctx}
    evidence["indicators"] = inds

    result = Classification(severity=final, baseline=base_after_urgency, rules=rules, evidence=evidence,
                            risk_score=ctx.get("risk_score"), needs_review=review, points=len(inds))
    result.reason = _reason(ctx, result, inds, computed)
    return result


def _reason(ctx: dict, r: Classification, inds: list[str], computed: str) -> str:
    phrase = _TYPE_PHRASE.get(ctx["type"], f"a {ctx['type'].replace('_', ' ')} incident was raised")
    parts = [f"Classified as {r.severity.upper()} because {phrase}"]
    if inds:
        parts.append("and " + _join([_INDICATOR_TEXT[i] for i in inds]))
    text = " ".join(parts) + "."
    ids = {x["id"] for x in r.rules}
    notes = []
    if "source_floor" in ids:
        notes.append(f"The rules alone give {computed.upper()}; the severity declared by the module that raised "
                     f"it was kept.")
    if "sensor_alone_cap" in ids:
        notes.append("A motion sensor alone cannot make an incident critical, so it is held at HIGH.")
    if "low_confidence_sensor" in ids:
        notes.append("The sensor reading was weak; flagged for human review.")
    if "conflicting_evidence" in ids:
        notes.append("The tourist checked in safely very recently; evidence conflicts, flagged for human review.")
    if "gps_unavailable" in ids:
        notes.append("No location was available (not treated as danger).")
    return " ".join([text, *notes])


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# --------------------------------------------------------------------------- persistence
def latest(db: Session, incident_id: int) -> SeverityClassification | None:
    return (db.query(SeverityClassification).filter(SeverityClassification.incident_id == incident_id)
            .order_by(SeverityClassification.id.desc()).first())


def history(db: Session, incident_id: int) -> list[SeverityClassification]:
    return (db.query(SeverityClassification).filter(SeverityClassification.incident_id == incident_id)
            .order_by(SeverityClassification.id).all())


def serialize(row: SeverityClassification) -> dict:
    return {
        "id": row.id, "incident_id": row.incident_id, "incident_type": row.incident_type,
        "severity": row.severity, "previous_severity": row.previous_severity,
        "baseline_severity": row.baseline_severity, "trigger": row.trigger,
        "rules": json.loads(row.rules_json or "[]"), "evidence": json.loads(row.evidence_json or "{}"),
        "reason": row.reason, "risk_score": row.risk_score, "needs_review": row.needs_review,
        "manual_override": row.manual_override, "actor_id": row.actor_id,
        "override_reason": row.override_reason, "classified_at": row.created_at,
    }


def _store(db: Session, inc: Incident, r: Classification, trigger: str, previous: str | None,
           now: datetime, *, actor_id: int | None = None, override_reason: str | None = None) -> None:
    db.add(SeverityClassification(
        incident_id=inc.id, incident_type=inc.type, severity=r.severity, previous_severity=previous,
        baseline_severity=r.baseline, trigger=trigger, rules_json=json.dumps(r.rules),
        evidence_json=json.dumps(r.evidence, default=str), reason=r.reason, risk_score=r.risk_score,
        needs_review=r.needs_review, manual_override=trigger == "override", actor_id=actor_id,
        override_reason=override_reason, created_at=now,
    ))


def classify_incident(db: Session, inc: Incident, tourist: Tourist | None = None, *,
                      evidence: dict | None = None, now: datetime | None = None) -> bool:
    """First classification of a freshly created incident. Writes severity,
    baseline, reason, needs_review and the history row. Returns True when the
    severity differs from what the producer declared. Caller commits."""
    now = now or utc_now()
    declared = inc.severity
    r = classify(build_context(db, inc, tourist, evidence=evidence, declared=declared, now=now))
    inc.baseline_severity = r.baseline
    inc.severity = r.severity
    inc.classification_reason = r.reason
    inc.classified_at = now
    inc.needs_review = r.needs_review
    _store(db, inc, r, "initial", declared, now)
    return r.severity != declared


def reevaluate(db: Session, inc: Incident, *, evidence: dict | None = None,
               now: datetime | None = None) -> tuple[bool, str | None]:
    """Re-run the rules with the evidence available NOW (new related incident,
    new sensor data ...). Returns (changed, previous_severity).

    Never lowers severity, and does nothing after a manual override (a human
    decision stands until a human changes it). Records a history row only when
    something actually changed."""
    now = now or utc_now()
    last = latest(db, inc.id)
    if last is not None and last.manual_override:
        return False, None
    previous = inc.severity
    r = classify(build_context(db, inc, evidence=evidence, declared=previous, now=now))
    if rank(r.severity) < rank(previous):
        r.severity = previous
        r.rules.append({"id": "downgrade_blocked", "effect": previous,
                        "detail": "re-evaluation never lowers severity automatically"})
    changed = r.severity != previous
    if not changed and r.reason == inc.classification_reason and r.needs_review == inc.needs_review:
        return False, None
    inc.severity = r.severity
    inc.classification_reason = r.reason
    inc.classified_at = now
    inc.needs_review = r.needs_review
    _store(db, inc, r, "reevaluation", previous, now)
    return changed, previous if changed else None


class OverrideError(ValueError):
    pass


def override(db: Session, inc: Incident, severity: str, reason: str, actor_id: int, *,
             now: datetime | None = None) -> str:
    """Administrator sets the severity by hand. A written reason is mandatory.
    Returns the previous severity. Caller commits and runs escalation.retarget."""
    now = now or utc_now()
    severity = (severity or "").lower()
    if severity not in LEVELS:
        raise OverrideError(f"severity must be one of {', '.join(LEVELS)}")
    reason = (reason or "").strip()
    if len(reason) < MIN_OVERRIDE_REASON:
        raise OverrideError(f"a reason of at least {MIN_OVERRIDE_REASON} characters is required")
    previous = inc.severity
    text = (f"Severity set to {severity.upper()} by an administrator"
            f"{'' if previous == severity else f' (was {previous.upper()})'}: {reason}")
    r = Classification(severity=severity, baseline=inc.baseline_severity or previous,
                       rules=[{"id": "manual_override", "effect": severity, "detail": reason}],
                       evidence={"type": inc.type}, reason=text, risk_score=None, needs_review=False)
    inc.severity = severity
    inc.classification_reason = text
    inc.classified_at = now
    inc.needs_review = False
    _store(db, inc, r, "override", previous, now, actor_id=actor_id, override_reason=reason)
    return previous
