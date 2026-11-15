"""Intelligent response management: severity classification -> response target
-> staged escalation -> global timeout -> acknowledgment -> SLO.

Time-dependent behaviour is driven with an explicit `now` (the services accept
one) instead of sleeping, so deadlines are exercised exactly and instantly.
"""
import json
from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.config import settings
from app.core.time import utc_now
from app.models.incident import Incident, IncidentEvent
from app.models.notification import NotificationLog, PushSubscription
from app.models.response import EscalationEvent, ResponseSetting, SeverityClassification
from app.services import (
    escalation,
    monitoring,
    notifications,
    response_metrics,
    response_policy,
    severity_classifier as sc,
)
from app.services.monitoring import trigger_sos
from tests.conftest import make_tourist, make_unit, make_zone


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # notification rows are written (and asserted on) but never delivered
    monkeypatch.setattr(notifications, "_submit", lambda *a, **k: None)


@pytest.fixture
def tourist(db):
    return make_tourist(db, name="Asha")


def _sub(db, user, name):
    s = PushSubscription(endpoint=f"https://push.test/{name}", p256dh="k", auth="a", user_id=user.id,
                         role=user.role)
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def subs(db, admin_user, responder_user):
    return {"admin": _sub(db, admin_user, "admin"), "responder": _sub(db, responder_user, "responder")}


def _open(db, tourist, itype="anomaly", severity="medium", evidence=None, lat=26.1445, lng=91.7362):
    inc = monitoring._open_incident(db, tourist, itype, severity, f"{itype} test", lat, lng, evidence=evidence)
    db.commit()
    db.refresh(inc)
    return inc


def _events(db, inc, kind=None):
    q = db.query(EscalationEvent).filter_by(incident_id=inc.id)
    if kind:
        q = q.filter_by(kind=kind)
    return q.order_by(EscalationEvent.id).all()


def _resp_logs(db, inc):
    return db.query(NotificationLog).filter(NotificationLog.incident_id == inc.id,
                                            NotificationLog.event_type == "response").all()


def _ctx(**over):
    base = dict(type="missed_checkin", declared=None, explicit_sos=False, silent=False, user_urgency=None,
                no_response=False, sensor_source=None, simulated=False, missed_checkins=0,
                gps_unavailable=False, distress=False, sensor_strength=None, prior_incidents=0,
                repeated_miss=False, in_high_risk_zone=False, risk_score=None, low_safety_score=False,
                tourist_status_sos=False, safe_signal=False)
    base.update(over)
    return base


# =========================================================================
# severity classification
# =========================================================================
def test_explicit_sos_is_critical_without_waiting_for_evidence():
    r = sc.classify(_ctx(type="sos", explicit_sos=True))
    assert r.severity == "critical" and r.baseline == "critical"
    assert r.rules[0]["id"] == "baseline"


def test_sos_stays_critical_even_if_baseline_config_lowered(monkeypatch):
    monkeypatch.setattr(settings, "SEVERITY_BASELINE_BY_TYPE", '{"sos":"low"}')
    r = sc.classify(_ctx(type="sos", explicit_sos=True))
    assert r.severity == "critical" and any(x["id"] == "explicit_sos" for x in r.rules)


def test_unknown_type_uses_configured_default_and_never_critical():
    r = sc.classify(_ctx(type="something_new"))
    assert r.severity == settings.SEVERITY_DEFAULT_BASELINE == "medium"


def test_suspected_fall_awaiting_response_is_high_and_not_called_confirmed():
    r = sc.classify(_ctx(type="fall_detected", no_response=True, sensor_strength="unknown"))
    assert r.severity == "high"
    assert "suspected" in r.reason and "confirmed" not in r.reason.replace("not confirmed", "")


def test_crash_with_strong_sensor_and_no_response_is_critical():
    r = sc.classify(_ctx(type="crash_detected", no_response=True, sensor_strength="strong"))
    assert r.severity == "critical" and r.points == 2
    assert "did not respond" not in r.reason  # wording is "has not responded"
    assert "has not responded" in r.reason


def test_low_confidence_sensor_event_is_flagged_for_review_not_critical():
    r = sc.classify(_ctx(type="fall_detected", no_response=True, sensor_strength="weak"))
    assert r.severity == "high" and r.needs_review
    assert any(x["id"] == "low_confidence_sensor" for x in r.rules)


def test_motion_sensor_alone_can_never_make_critical(monkeypatch):
    monkeypatch.setattr(settings, "SEVERITY_POINTS_PER_LEVEL", 1)   # one indicator would bump
    r = sc.classify(_ctx(type="crash_detected", sensor_strength="strong"))
    assert r.severity == "high" and r.needs_review
    assert any(x["id"] == "sensor_alone_cap" for x in r.rules)


def test_missing_data_is_not_danger():
    r = sc.classify(_ctx(type="missed_checkin"))
    assert r.severity == "medium" and r.points == 0 and not r.needs_review


def test_single_missed_checkin_is_medium_repeated_with_no_response_is_high():
    assert sc.classify(_ctx(type="missed_checkin", missed_checkins=1)).severity == "medium"
    r = sc.classify(_ctx(type="missed_checkin", missed_checkins=2, repeated_miss=True, no_response=True))
    assert r.severity == "high"


def test_temporary_gps_loss_does_not_raise_severity():
    r = sc.classify(_ctx(type="route_deviation", gps_unavailable=True))
    assert r.severity == "low"
    assert any(x["id"] == "gps_unavailable" and x["effect"] == "none" for x in r.rules)


def test_minor_route_deviation_is_low_and_geofence_medium():
    assert sc.classify(_ctx(type="route_deviation")).severity == "low"
    assert sc.classify(_ctx(type="geofence")).severity == "medium"


def test_user_reported_urgency_raises_baseline():
    r = sc.classify(_ctx(type="safety_report", user_urgency="high"))
    assert r.severity == "high" and any(x["id"] == "user_urgency" for x in r.rules)


def test_conflicting_evidence_flags_review_and_does_not_lower():
    r = sc.classify(_ctx(type="fall_detected", no_response=True, safe_signal=True))
    assert r.needs_review and r.severity == "high"
    assert any(x["id"] == "conflicting_evidence" for x in r.rules)


def test_declared_severity_is_a_floor_and_can_be_switched_off(monkeypatch):
    r = sc.classify(_ctx(type="missed_checkin", declared="critical"))
    assert r.severity == "critical" and any(x["id"] == "source_floor" for x in r.rules)
    assert r.needs_review          # rules alone are two levels lower: a human should look
    monkeypatch.setattr(settings, "SEVERITY_SOURCE_FLOOR_ENABLED", False)
    assert sc.classify(_ctx(type="missed_checkin", declared="critical")).severity == "medium"


def test_corroborating_indicators_raise_one_level_per_group():
    r = sc.classify(_ctx(type="anomaly", prior_incidents=1, in_high_risk_zone=True))
    assert r.severity == "critical" and r.points == 2


def test_existing_risk_score_is_an_input_with_documented_threshold():
    low = sc.classify(_ctx(type="geofence", low_safety_score=True, risk_score=10.0))
    assert low.risk_score == 10.0 and "low_safety_score" in low.evidence["indicators"]
    assert sc.classify(_ctx(type="geofence", risk_score=None)).risk_score is None


def test_classification_is_persisted_with_rules_evidence_and_reason(db, tourist):
    inc = _open(db, tourist, "anomaly", "high")
    row = db.query(SeverityClassification).filter_by(incident_id=inc.id).one()
    assert row.trigger == "initial" and row.incident_type == "anomaly"
    assert json.loads(row.rules_json)[0]["id"] == "baseline"
    assert "indicators" in json.loads(row.evidence_json)
    assert row.reason == inc.classification_reason and inc.baseline_severity == "high"
    assert inc.classified_at is not None and not row.manual_override


def test_context_reads_real_database_evidence(db, tourist):
    make_zone(db, risk="high", lat=26.1445, lng=91.7362)
    _open(db, tourist, "anomaly", "high")                      # a prior incident
    inc = Incident(tourist_id=tourist.id, type="anomaly", severity="high", status="detected",
                   description="x", lat=26.1445, lng=91.7362)
    db.add(inc)
    db.flush()
    ctx = sc.build_context(db, inc, tourist)
    assert ctx["prior_incidents"] == 1 and ctx["in_high_risk_zone"] and ctx["risk_score"] == 90.0


def test_reevaluate_upgrades_but_never_downgrades(db, tourist):
    inc = _open(db, tourist, "geofence", "medium")
    assert inc.severity == "medium"
    make_zone(db, risk="high", lat=26.1445, lng=91.7362)
    _open(db, tourist, "anomaly", "high")           # new related incident -> evidence for `inc`
    db.refresh(inc)
    assert inc.severity == "high"                   # prior_incident + high_risk_zone: +1
    changed, _ = sc.reevaluate(db, inc)
    assert not changed
    inc.severity = "critical"
    db.commit()
    sc.reevaluate(db, inc)
    assert inc.severity == "critical"               # never lowered automatically


def test_manual_override_requires_reason_is_audited_and_sticks(db, tourist, admin_user):
    inc = _open(db, tourist, "anomaly", "high")
    with pytest.raises(sc.OverrideError):
        sc.override(db, inc, "low", "  ", admin_user.id)
    with pytest.raises(sc.OverrideError):
        sc.override(db, inc, "urgent", "valid reason", admin_user.id)
    escalation.override_severity(db, inc, "low", "verified false alarm by phone", admin_user)
    db.commit()
    row = sc.latest(db, inc.id)
    assert inc.severity == "low" and row.manual_override and row.actor_id == admin_user.id
    assert row.override_reason == "verified false alarm by phone" and row.previous_severity == "high"
    assert not sc.reevaluate(db, inc)[0] and inc.severity == "low"   # a human decision stands


# =========================================================================
# response policy / configuration
# =========================================================================
def test_default_targets_are_the_illustrative_values(db):
    assert response_policy.targets(db) == {"critical": 60, "high": 180, "medium": 300, "low": 600}
    assert response_policy.config(db)["illustrative"] is True


@pytest.mark.parametrize("sev,seconds", [("critical", 60), ("high", 180), ("medium", 300), ("low", 600)])
def test_response_deadline_is_detection_plus_severity_target(db, tourist, sev, seconds):
    inc = _open(db, tourist, "route_deviation", sev)
    assert inc.severity == sev
    assert inc.response_target_seconds == seconds
    assert inc.response_deadline == inc.detected_at + timedelta(seconds=seconds)
    assert inc.escalation_status == "active" and inc.response_stage == 1
    assert inc.response_stage_deadline == inc.response_deadline


def test_targets_come_from_configuration_not_code(db, tourist, monkeypatch):
    monkeypatch.setattr(settings, "RESPONSE_TARGET_HIGH_SECONDS", 45)
    inc = _open(db, tourist, "anomaly", "high")
    assert inc.response_target_seconds == 45


def test_admin_override_of_targets_wins_and_is_validated(db, admin_user):
    cfg = response_policy.update(db, {"response_targets_seconds": {"critical": 30}}, admin_user.id)
    db.commit()
    assert cfg["response_targets_seconds"]["critical"] == 30 and "response_target.critical" in cfg["overridden"]
    with pytest.raises(response_policy.PolicyError):
        response_policy.update(db, {"response_targets_seconds": {"critical": 400}}, admin_user.id)  # > high
    with pytest.raises(response_policy.PolicyError):
        response_policy.update(db, {"response_targets_seconds": {"bogus": 10}}, admin_user.id)
    with pytest.raises(response_policy.PolicyError):
        response_policy.update(db, {"global_timeout_seconds": 100}, admin_user.id)  # < longest target
    with pytest.raises(response_policy.PolicyError):
        response_policy.update(db, {"stage2_seconds": 1}, admin_user.id)            # below minimum
    assert response_policy.target_seconds(db, "critical") == 30       # failures changed nothing


def test_override_applies_to_new_incidents_only(db, tourist, admin_user):
    old = _open(db, tourist, "anomaly", "high")
    response_policy.update(db, {"response_targets_seconds": {"high": 90}}, admin_user.id)
    db.commit()
    new = _open(db, tourist, "anomaly", "high")
    assert old.response_target_seconds == 180 and new.response_target_seconds == 90


# =========================================================================
# workflow entry, integration with every incident source
# =========================================================================
def test_open_workflow_is_idempotent_for_duplicate_events(db, tourist):
    inc = _open(db, tourist, "anomaly", "high")
    escalation.open_workflow(db, inc, tourist)
    escalation.open_workflow(db, inc, tourist)
    db.commit()
    assert db.query(SeverityClassification).filter_by(incident_id=inc.id).count() == 1
    assert len(_events(db, inc, "assigned")) == 1


def test_sos_enters_the_common_workflow_critical_with_shortest_target(db, tourist):
    make_unit(db)
    inc = db.get(Incident, trigger_sos(db, tourist, 26.1445, 91.7362, "help")["incident_id"])
    assert inc.severity == "critical" and inc.response_target_seconds == 60
    assert inc.assigned_at is not None and inc.escalation_status == "active"
    assert inc.acknowledged_at is None            # auto-dispatch is assignment, not acknowledgment
    assert inc.escalation_stage == "control_room" and inc.escalation_deadline is not None   # legacy intact


def test_motion_incident_carries_no_response_and_sensor_strength(db, tourist):
    inc = monitoring.raise_motion_incident(db, tourist, "crash", 26.1, 91.7, source="phone_sensor",
                                           evidence={"peak_g": 9.0})
    db.commit()
    row = sc.latest(db, inc.id)
    ev = json.loads(row.evidence_json)
    assert inc.severity == "critical" and ev["no_response"] is True and ev["sensor_strength"] == "strong"
    assert row.needs_review is False


def test_motion_incident_with_weak_sensor_is_kept_critical_but_flagged(db, tourist):
    inc = monitoring.raise_motion_incident(db, tourist, "fall", 26.1, 91.7, source="phone_sensor",
                                           evidence={"peak_g": 0.5})
    db.commit()
    assert inc.severity == "critical"             # declared by the source: never silently lowered
    assert inc.needs_review is True               # ...but a human is asked to look


def test_walk_home_incident_uses_the_same_pipeline(db, tourist):
    inc = _open(db, tourist, "missed_checkin", "critical", evidence={"no_response": True, "missed_checkins": 1})
    ev = json.loads(sc.latest(db, inc.id).evidence_json)
    assert ev["no_response"] and inc.response_target_seconds == 60 and inc.escalation_status == "active"


def test_mark_missing_and_field_report_enter_the_workflow(client, db, tourist, admin_headers):
    r = client.post(f"/api/tourists/{tourist.id}/mark-missing", headers=admin_headers)
    assert r.status_code == 200
    inc = db.get(Incident, r.json()["incident_id"])
    assert inc.severity == "critical" and inc.response_deadline is not None
    r = client.post("/api/tourist-id/report-incident", headers=admin_headers,
                    json={"digital_id": tourist.digital_id, "severity": "low", "description": "lost wallet"})
    inc2 = db.get(Incident, r.json()["incident_id"])
    assert inc2.response_target_seconds == 300 and inc2.severity == "medium"   # type baseline raised the declared low


def test_disabled_feature_leaves_incidents_exactly_as_before(db, tourist, monkeypatch):
    monkeypatch.setattr(settings, "RESPONSE_MANAGEMENT_ENABLED", False)
    inc = _open(db, tourist, "anomaly", "high")
    assert inc.severity == "high" and inc.response_deadline is None and inc.escalation_status == "none"
    assert escalation.tick_response_stages(db) == {"advanced": [], "timed_out": [], "warned": []}


def test_assigned_stage1_notification_skips_sos_but_reaches_responders_otherwise(db, tourist, subs):
    make_unit(db)
    sos = db.get(Incident, trigger_sos(db, tourist, 26.1445, 91.7362, "help")["incident_id"])
    assert not [n for n in _resp_logs(db, sos) if "assigned" in n.payload]
    other = _open(db, make_tourist(db, name="Someone Else", doc="XXXX-XXXX-0002"), "anomaly", "high")
    assigned = [n for n in _resp_logs(db, other) if json.loads(n.payload)["kind"] == "assigned"]
    assert [n.recipient_type for n in assigned] == ["responder"]
    assert "respond by" in assigned[0].body and "HIGH" in assigned[0].body


# =========================================================================
# staged escalation, global timeout
# =========================================================================
def test_stage1_to_stage2_at_the_response_deadline(db, tourist, subs):
    inc = _open(db, tourist, "anomaly", "high")
    before = inc.response_deadline
    assert escalation.tick_response_stages(db, before - timedelta(seconds=1))["advanced"] == []
    out = escalation.tick_response_stages(db, before + timedelta(seconds=1))
    db.refresh(inc)
    assert out["advanced"] == [inc.id] and inc.response_stage == 2
    assert inc.response_stage_deadline == before + timedelta(seconds=settings.RESPONSE_STAGE2_SECONDS)
    assert [e.kind for e in _events(db, inc)] == ["assigned", "deadline_missed", "stage_advanced"]
    assert db.query(IncidentEvent).filter_by(incident_id=inc.id, status="response_stage:2").count() == 1
    logs = [n for n in _resp_logs(db, inc) if json.loads(n.payload)["kind"] == "stage"]
    assert logs and "Supervisor" in logs[0].body and "response deadline missed" in logs[0].body


def test_stage2_to_stage3_then_waits_for_global_timeout(db, tourist, subs):
    inc = _open(db, tourist, "anomaly", "high")
    t1 = inc.response_deadline + timedelta(seconds=1)
    escalation.tick_response_stages(db, t1)
    db.refresh(inc)
    t2 = inc.response_stage_deadline + timedelta(seconds=1)
    escalation.tick_response_stages(db, t2)
    db.refresh(inc)
    assert inc.response_stage == 3 and inc.response_stage_deadline == inc.global_timeout_deadline
    assert inc.escalation_status == "active"
    kinds = [n.recipient_type for n in _resp_logs(db, inc)
             if json.loads(n.payload)["kind"] in ("stage", "urgent")]
    assert "admin" in kinds                       # stage 3 reaches the higher authority


def test_global_timeout_stops_escalation_keeps_incident_open_and_notifies(db, tourist, subs):
    inc = _open(db, tourist, "anomaly", "high")
    t = inc.global_timeout_deadline + timedelta(seconds=1)
    out = escalation.tick_response_stages(db, t)
    db.refresh(inc)
    assert out["timed_out"] == [inc.id]
    assert inc.escalation_status == "exhausted" and inc.global_timeout_at == t
    assert inc.status == "detected" and inc.resolved_at is None          # NOT closed or resolved
    assert len(_events(db, inc, "global_timeout")) == 1
    assert [n for n in _resp_logs(db, inc) if json.loads(n.payload)["kind"] == "timeout"]
    assert escalation.tick_response_stages(db, t + timedelta(hours=5)) == {"advanced": [], "timed_out": [], "warned": []}
    assert len(_events(db, inc, "global_timeout")) == 1


def test_global_deadline_is_fixed_and_stages_never_pass_it(db, tourist, admin_user):
    response_policy.update(db, {"response_targets_seconds": {"medium": 190, "low": 195},
                                "global_timeout_seconds": 200, "stage2_seconds": 150}, admin_user.id)
    db.commit()
    inc = _open(db, tourist, "anomaly", "high")                           # target 180, global 200
    glob = inc.global_timeout_deadline
    assert glob == inc.detected_at + timedelta(seconds=200)
    escalation.tick_response_stages(db, inc.response_deadline + timedelta(seconds=1))
    db.refresh(inc)
    assert inc.response_stage == 2 and inc.response_stage_deadline == glob   # 180+150 capped at 200
    assert inc.global_timeout_deadline == glob                               # never reset by a stage


def test_duplicate_ticks_and_workers_do_not_duplicate_escalation(db, tourist, subs):
    inc = _open(db, tourist, "anomaly", "high")
    t = inc.response_deadline + timedelta(seconds=1)
    escalation.tick_response_stages(db, t)
    escalation.tick_response_stages(db, t)
    escalation.tick_response_stages(db, t)
    assert len(_events(db, inc, "stage_advanced")) == 1
    assert len([n for n in _resp_logs(db, inc) if json.loads(n.payload)["kind"] == "stage"]) == 1


def test_stale_worker_claim_loses_to_an_acknowledgment(db, tourist, responder_user):
    inc = _open(db, tourist, "anomaly", "high")
    db.execute(update(Incident).where(Incident.id == inc.id)
               .values(acknowledged_at=utc_now(), acknowledged_by=responder_user.id, slo_met=True))
    db.commit()                                                   # `inc` below is now stale
    assert escalation._advance(db, inc, inc.response_deadline + timedelta(seconds=5)) is False
    db.refresh(inc)
    assert inc.response_stage == 1 and not _events(db, inc, "stage_advanced")


def test_stale_worker_claim_loses_to_a_competing_worker(db, tourist):
    inc = _open(db, tourist, "anomaly", "high")
    from sqlalchemy.orm import Session
    other_worker = Session(bind=db.get_bind())       # a second worker commits its claim first
    other_worker.execute(update(Incident).where(Incident.id == inc.id).values(response_stage=2))
    other_worker.commit()
    other_worker.close()                              # `inc` here is still the stale, stage-1 view
    assert escalation._advance(db, inc, inc.response_deadline + timedelta(seconds=5)) is False
    db.refresh(inc)
    assert inc.response_stage == 2 and not _events(db, inc, "stage_advanced")


def test_pending_escalation_survives_restart_and_resumes_from_stored_deadlines(db, tourist):
    inc = _open(db, tourist, "anomaly", "high")
    glob = inc.global_timeout_deadline
    db.expire_all()                                               # "new process": nothing cached
    late = inc.response_deadline + timedelta(minutes=10)          # backend was down for ten minutes
    assert late < glob
    out = escalation.tick_response_stages(db, late)
    db.refresh(inc)
    assert out["advanced"] == [inc.id] and inc.response_stage == 2
    assert inc.response_stage_deadline == late + timedelta(seconds=settings.RESPONSE_STAGE2_SECONDS)


def test_restart_after_global_timeout_goes_straight_to_exhausted(db, tourist):
    inc = _open(db, tourist, "anomaly", "high")
    out = escalation.tick_response_stages(db, inc.global_timeout_deadline + timedelta(hours=2))
    db.refresh(inc)
    assert out["timed_out"] == [inc.id] and inc.escalation_status == "exhausted"


def test_notification_failure_never_loses_the_escalation_event(db, tourist, monkeypatch):
    inc = _open(db, tourist, "anomaly", "high")

    def boom(*a, **k):
        raise RuntimeError("push provider down")
    monkeypatch.setattr(notifications, "notify_response", boom)
    escalation.tick_response_stages(db, inc.response_deadline + timedelta(seconds=1))
    db.refresh(inc)
    assert inc.response_stage == 2 and len(_events(db, inc, "stage_advanced")) == 1


def test_deadline_warning_is_optional_and_sent_once(db, tourist, admin_user, subs):
    response_policy.update(db, {"warning_seconds": 30}, admin_user.id)
    db.commit()
    inc = _open(db, tourist, "anomaly", "high")
    t = inc.response_deadline - timedelta(seconds=20)
    assert escalation.tick_response_stages(db, t)["warned"] == [inc.id]
    assert escalation.tick_response_stages(db, t)["warned"] == []
    assert len(_events(db, inc, "warning")) == 1
    assert escalation.tick_response_stages(db, inc.response_deadline - timedelta(seconds=100))["warned"] == []


def test_warnings_are_off_by_default(db, tourist):
    inc = _open(db, tourist, "anomaly", "high")
    assert escalation.tick_response_stages(db, inc.response_deadline - timedelta(seconds=1))["warned"] == []


def test_tick_escalations_runs_both_chains_and_old_chain_is_unchanged(db, tourist):
    make_unit(db)
    sos = db.get(Incident, trigger_sos(db, tourist, 26.1445, 91.7362, "help")["incident_id"])
    sos.escalation_deadline = utc_now() - timedelta(seconds=1)
    sos.response_stage_deadline = utc_now() - timedelta(seconds=1)
    db.commit()
    advanced = escalation.tick_escalations(db)
    db.refresh(sos)
    assert advanced == [sos.id] and sos.escalation_stage == "emergency_contact"   # legacy chain
    assert sos.response_stage == 2                                                # new chain


def test_scheduler_job_lock_ttl_is_shorter_than_one_tick(monkeypatch):
    import app.main as main
    seen = {}
    monkeypatch.setattr(main, "run_locked", lambda f, job, ttl, fn: seen.update(job=job, ttl=ttl))
    main._escalation_tick_job()
    assert seen == {"job": "escalation_tick", "ttl": settings.ESCALATION_TICK_SECONDS - 1}


# =========================================================================
# acknowledgment
# =========================================================================
def test_ack_before_deadline_records_who_when_and_slo_success(db, tourist, responder_user, subs):
    inc = _open(db, tourist, "anomaly", "high")
    now = inc.detected_at + timedelta(seconds=40)
    inc, first = escalation.acknowledge(db, inc, responder_user, now=now)
    db.commit()
    assert first and inc.acknowledged_by == responder_user.id and inc.acknowledged_at == now
    assert inc.acknowledgment_seconds == pytest.approx(40) and inc.slo_met is True
    assert inc.escalation_status == "acknowledged" and inc.response_stage_deadline is None
    assert len(_events(db, inc, "acknowledged")) == 1
    assert escalation.tick_response_stages(db, inc.response_deadline + timedelta(hours=1))["advanced"] == []


def test_ack_after_deadline_is_an_slo_failure(db, tourist, responder_user):
    inc = _open(db, tourist, "anomaly", "high")
    inc, _ = escalation.acknowledge(db, inc, responder_user, now=inc.response_deadline + timedelta(seconds=1))
    assert inc.slo_met is False and inc.acknowledgment_seconds == pytest.approx(181)


def test_ack_exactly_at_the_deadline_counts_as_within_target(db, tourist, responder_user):
    inc = _open(db, tourist, "anomaly", "high")
    inc, _ = escalation.acknowledge(db, inc, responder_user, now=inc.response_deadline)
    assert inc.slo_met is True


def test_ack_during_escalation_transition_is_recorded_late_and_stops_further_stages(db, tourist, responder_user):
    inc = _open(db, tourist, "anomaly", "high")
    escalation.tick_response_stages(db, inc.response_deadline + timedelta(seconds=1))   # now stage 2
    db.refresh(inc)
    inc, first = escalation.acknowledge(db, inc, responder_user, now=inc.response_deadline + timedelta(seconds=5))
    db.commit()
    assert first and inc.slo_met is False and inc.response_stage == 2
    assert escalation.tick_response_stages(db, inc.global_timeout_deadline + timedelta(hours=1))["timed_out"] == []
    assert inc.escalation_status == "acknowledged"


def test_ack_after_global_timeout_keeps_exhausted_status_and_fails_slo(db, tourist, responder_user):
    inc = _open(db, tourist, "anomaly", "high")
    escalation.tick_response_stages(db, inc.global_timeout_deadline + timedelta(seconds=1))
    inc, first = escalation.acknowledge(db, inc, responder_user, now=inc.global_timeout_deadline + timedelta(seconds=60))
    assert first and inc.escalation_status == "exhausted" and inc.slo_met is False
    assert inc.status == "detected"        # acknowledging is not resolving


def test_two_responders_acknowledging_at_once_record_exactly_one(db, tourist, responder_user, admin_user, monkeypatch):
    inc = _open(db, tourist, "anomaly", "high")
    real = response_metrics.ack_outcome
    state = {"raced": False}

    def racing(i, now):          # the other responder's UPDATE lands just before ours
        if not state["raced"]:
            state["raced"] = True
            db.execute(update(Incident).where(Incident.id == i.id)
                       .values(acknowledged_at=now - timedelta(seconds=1), acknowledged_by=admin_user.id,
                               acknowledgment_seconds=10.0, slo_met=True))
        return real(i, now)
    monkeypatch.setattr(response_metrics, "ack_outcome", racing)
    inc, first = escalation.acknowledge(db, inc, responder_user, now=utc_now())
    db.commit()
    assert first is False and inc.acknowledged_by == admin_user.id and inc.acknowledgment_seconds == 10.0
    assert len(_events(db, inc, "acknowledged")) == 0     # the winner records it; the loser adds nothing


def test_repeated_ack_is_idempotent(db, tourist, responder_user, admin_user):
    inc = _open(db, tourist, "anomaly", "high")
    inc, a = escalation.acknowledge(db, inc, responder_user)
    inc, b = escalation.acknowledge(db, inc, admin_user)
    db.commit()
    assert a and not b and inc.acknowledged_by == responder_user.id
    assert len(_events(db, inc, "acknowledged")) == 1


def test_acknowledged_but_unresolved_incident_stays_open_and_visible(db, tourist, responder_user):
    inc = _open(db, tourist, "anomaly", "high")
    escalation.acknowledge(db, inc, responder_user)
    db.commit()
    board = response_metrics.board(db)
    item = next(i for i in board["items"] if i["incident_id"] == inc.id)
    assert item["acknowledged"] and item["resolved_at"] is None and item["status"] != "resolved"
    assert inc.resolved_at is None and inc.dispatched_at is None      # separate lifecycle stamps


def test_ack_through_api_sets_acknowledgment_and_keeps_legacy_behaviour(client, db, tourist, admin_headers, admin_user):
    inc = _open(db, tourist, "anomaly", "high")
    r = client.post(f"/api/incidents/{inc.id}/acknowledge", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "acknowledged" and body["escalation_stage"] == "acknowledged"
    assert body["acknowledged_by"] == admin_user.id and body["slo_met"] is True
    assert body["escalation_status"] == "acknowledged" and body["server_time"]
    assert body["acknowledged_at"] is not None
    db.refresh(inc)
    assert inc.acknowledgment_seconds == pytest.approx((inc.acknowledged_at - inc.detected_at).total_seconds())


def test_patch_to_dispatched_counts_as_acknowledgment_and_resolve_closes_workflow(client, db, tourist, admin_headers):
    inc = _open(db, tourist, "anomaly", "high")
    r = client.patch(f"/api/incidents/{inc.id}", headers=admin_headers, json={"status": "dispatched"})
    assert r.json()["acknowledged_at"] is not None and r.json()["slo_met"] is True
    r = client.patch(f"/api/incidents/{inc.id}", headers=admin_headers, json={"status": "resolved"})
    assert r.status_code == 200 and r.json()["escalation_status"] == "closed"
    assert len(_events(db, inc, "resolved")) == 1


def test_manually_resolved_incident_is_never_escalated(db, tourist, admin_user):
    inc = _open(db, tourist, "anomaly", "high")
    inc.status, inc.resolved_at = "resolved", utc_now()
    escalation.close_workflow(db, inc)
    db.commit()
    assert escalation.tick_response_stages(db, inc.global_timeout_deadline + timedelta(hours=1)) == \
        {"advanced": [], "timed_out": [], "warned": []}


# =========================================================================
# severity change during an active escalation
# =========================================================================
def test_upgrade_tightens_the_deadline_and_audits(db, tourist, admin_user, subs):
    inc = _open(db, tourist, "geofence", "medium")                         # 300 s
    assert inc.response_target_seconds == 300
    when = inc.detected_at + timedelta(seconds=10)
    escalation.override_severity(db, inc, "critical", "witness confirms danger on the phone", admin_user, now=when)
    db.commit()
    assert inc.response_target_seconds == 60 and inc.response_deadline == when + timedelta(seconds=60)
    assert inc.response_stage_deadline == inc.response_deadline
    assert inc.detected_at == inc.escalation_started_at                    # originals preserved
    note = _events(db, inc, "severity_changed")[0].note
    assert "MEDIUM -> CRITICAL" in note and "deadline" in note
    assert [n for n in _resp_logs(db, inc) if json.loads(n.payload)["kind"] == "severity"]


def test_upgrade_never_gives_more_time(db, tourist, admin_user):
    inc = _open(db, tourist, "route_deviation", "low")                     # 600 s
    near = inc.response_deadline
    # upgrade late: min(old, now + 180) keeps the earlier one
    when = inc.detected_at + timedelta(seconds=580)
    escalation.override_severity(db, inc, "high", "now looks serious indeed", admin_user, now=when)
    assert inc.response_deadline == near


def test_downgrade_by_admin_recomputes_from_detection_and_is_audited(db, tourist, admin_user):
    inc = _open(db, tourist, "anomaly", "high")                            # 180 s
    escalation.override_severity(db, inc, "low", "confirmed false alarm by officer", admin_user)
    db.commit()
    assert inc.response_deadline == inc.detected_at + timedelta(seconds=600)
    assert sc.latest(db, inc.id).previous_severity == "high"


def test_severity_change_after_ack_leaves_the_decided_deadline(db, tourist, admin_user, responder_user):
    inc = _open(db, tourist, "anomaly", "high")
    escalation.acknowledge(db, inc, responder_user, now=inc.detected_at + timedelta(seconds=30))
    deadline = inc.response_deadline
    escalation.override_severity(db, inc, "critical", "escalating by decision", admin_user)
    assert inc.response_deadline == deadline and inc.slo_met is True


def test_reclassify_endpoint_and_new_related_incident_upgrade(client, db, tourist, admin_headers):
    inc = _open(db, tourist, "geofence", "medium")
    make_zone(db, risk="high", lat=26.1445, lng=91.7362)
    other = _open(db, tourist, "anomaly", "high")
    db.refresh(inc)
    assert inc.severity == "high"                                         # sibling upgrade at creation
    assert _events(db, inc, "severity_changed")
    r = client.post(f"/api/incidents/{other.id}/reclassify", headers=admin_headers)
    assert r.status_code == 200 and r.json()["severity"] in ("high", "critical")


# =========================================================================
# SLO
# =========================================================================
def _managed(db, tourist, sev, ack_after=None, user=None, itype="route_deviation", at=None):
    inc = _open(db, tourist, itype, sev)
    if at is not None:
        shift = at - inc.detected_at
        inc.detected_at = at
        inc.response_deadline += shift
        inc.response_stage_deadline += shift
        inc.escalation_started_at = at
        inc.global_timeout_deadline += shift
        db.commit()
    if ack_after is not None:
        escalation.acknowledge(db, inc, user, now=inc.detected_at + timedelta(seconds=ack_after))
        db.commit()
    return inc


def test_slo_overall_and_denominator_exclude_unacknowledged(db, tourist, responder_user):
    _managed(db, tourist, "high", 60, responder_user)       # within 180
    _managed(db, tourist, "high", 100, responder_user)      # within
    _managed(db, tourist, "high", 400, responder_user)      # late
    _managed(db, tourist, "critical")                       # never acknowledged
    r = response_metrics.slo(db)
    o = r["overall"]
    assert (o["received"], o["acknowledged"], o["within_target"], o["after_target"]) == (4, 3, 2, 1)
    assert o["compliance_pct"] == pytest.approx(66.7)       # 2 / 3, NOT 2 / 4
    assert o["unacknowledged"] == 1
    assert r["methodology"]["targets_illustrative"] is True


def test_slo_is_none_not_zero_or_hundred_when_nothing_acknowledged(db, tourist):
    _managed(db, tourist, "high")
    o = response_metrics.slo(db)["overall"]
    assert o["compliance_pct"] is None and o["avg_ack_seconds"] is None and o["median_ack_seconds"] is None


def test_slo_by_severity_average_and_median(db, tourist, responder_user):
    _managed(db, tourist, "critical", 30, responder_user)   # within 60
    _managed(db, tourist, "critical", 90, responder_user)   # late
    _managed(db, tourist, "low", 100, responder_user)
    _managed(db, tourist, "low", 300, responder_user)
    _managed(db, tourist, "low", 200, responder_user)
    r = response_metrics.slo(db)
    assert r["by_severity"]["critical"]["compliance_pct"] == 50.0
    assert r["by_severity"]["low"]["compliance_pct"] == 100.0
    assert r["by_severity"]["low"]["avg_ack_seconds"] == pytest.approx(200.0)
    assert r["by_severity"]["low"]["median_ack_seconds"] == pytest.approx(200.0)
    assert r["by_severity"]["medium"]["compliance_pct"] is None
    assert r["overall"]["avg_ack_seconds"] == pytest.approx(144.0)


def test_slo_overdue_and_active_are_reported_separately(db, tourist, responder_user):
    unack = _managed(db, tourist, "critical")
    _managed(db, tourist, "high", 10, responder_user)
    later = unack.response_deadline + timedelta(minutes=5)
    r = response_metrics.slo(db, now=later, end=later)
    assert r["overall"]["overdue"] == 1 and r["active_overdue"] == 1
    assert r["active_incidents"] == 2 and r["active_unacknowledged"] == 1


def test_slo_counts_escalations_and_global_timeouts(db, tourist, responder_user):
    a = _managed(db, tourist, "high")
    b = _managed(db, tourist, "high")
    _managed(db, tourist, "high", 5, responder_user)
    escalation.tick_response_stages(db, a.response_deadline + timedelta(seconds=1))
    escalation.tick_response_stages(db, b.global_timeout_deadline + timedelta(seconds=1))
    o = response_metrics.slo(db)["overall"]
    assert o["escalated"] == 2 and o["global_timeouts"] == 2   # the late tick also exhausts `a`
    assert response_metrics.slo(db)["trend"][0]["escalations"] >= 1


def test_slo_date_range_filter(db, tourist, responder_user):
    now = utc_now()
    _managed(db, tourist, "high", 10, responder_user, at=now - timedelta(days=10))
    _managed(db, tourist, "high", 500, responder_user, at=now - timedelta(days=1))
    all_ = response_metrics.slo(db, start=now - timedelta(days=30), end=now)["overall"]
    recent = response_metrics.slo(db, start=now - timedelta(days=3), end=now)["overall"]
    assert all_["received"] == 2 and recent["received"] == 1 and recent["compliance_pct"] == 0.0
    with pytest.raises(response_metrics.SloError):
        response_metrics.slo(db, start=now, end=now - timedelta(days=1))
    with pytest.raises(response_metrics.SloError):
        response_metrics.slo(db, start=now - timedelta(days=settings.SLO_MAX_RANGE_DAYS + 5), end=now)


def test_slo_filters_by_severity_type_and_unit(db, tourist, responder_user):
    _managed(db, tourist, "high", 10, responder_user, itype="anomaly")
    _managed(db, tourist, "low", 10, responder_user, itype="route_deviation")
    assert response_metrics.slo(db, severity="high")["overall"]["received"] == 1
    assert response_metrics.slo(db, incident_type="route_deviation")["overall"]["received"] == 1
    assert response_metrics.slo(db, unit_id=999)["overall"]["received"] == 0
    types = {t["type"] for t in response_metrics.slo(db)["by_type"]}
    assert types == {"anomaly", "route_deviation"}
    with pytest.raises(response_metrics.SloError):
        response_metrics.slo(db, severity="urgent")


def test_incidents_without_a_response_deadline_are_not_eligible(db, tourist):
    db.add(Incident(tourist_id=tourist.id, type="sos", severity="critical", status="resolved", description="old"))
    db.commit()
    assert response_metrics.slo(db)["overall"]["received"] == 0


def test_distribution_buckets_come_from_configuration_and_stored_times(db, tourist, responder_user, monkeypatch):
    monkeypatch.setattr(settings, "SLO_DISTRIBUTION_BUCKETS_SECONDS", "30,120")
    _managed(db, tourist, "high", 20, responder_user)
    _managed(db, tourist, "high", 100, responder_user)
    _managed(db, tourist, "high", 400, responder_user)
    dist = response_metrics.slo(db)["distribution"]
    assert [(b["label"], b["count"]) for b in dist] == [("≤30s", 1), ("≤120s", 1), (">120s", 1)]


# =========================================================================
# live board / API / permissions
# =========================================================================
def test_board_sorts_overdue_first_and_is_server_computed(db, tourist):
    a = _open(db, tourist, "route_deviation", "low")
    b = _open(db, tourist, "anomaly", "critical")
    now = b.response_deadline + timedelta(seconds=30)
    board = response_metrics.board(db, now=now)
    first = board["items"][0]
    assert first["incident_id"] == b.id and first["overdue"] and first["remaining_seconds"] == pytest.approx(-30)
    assert board["overdue"] == 1 and board["unacknowledged"] == 2
    assert first["next_stage_label"] == "Supervisor" and first["stage_label"] == "Primary responder"
    assert first["response_deadline"].endswith("+00:00") and board["server_time"].endswith("+00:00")
    assert a.id in [i["incident_id"] for i in board["items"]]


def test_refresh_returns_the_correct_remaining_time_from_stored_deadline(db, tourist):
    inc = _open(db, tourist, "anomaly", "high")
    t = inc.detected_at + timedelta(seconds=100)
    first = response_metrics.view(inc, t)["remaining_seconds"]
    db.expire_all()
    assert response_metrics.view(db.get(Incident, inc.id), t)["remaining_seconds"] == first == pytest.approx(80)


def test_api_board_is_scoped_for_responders(client, db, tourist, responder_unit, responder_headers, admin_headers):
    mine = db.get(Incident, trigger_sos(db, tourist, responder_unit.lat, responder_unit.lng, "help")["incident_id"])
    assert mine.assigned_unit_id == responder_unit.id
    other_unit = make_unit(db, name="Far Unit", lat=10.0, lng=10.0)
    other = _open(db, tourist, "anomaly", "high")
    other.assigned_unit_id = other_unit.id
    db.commit()
    ids = [i["incident_id"] for i in client.get("/api/response/board", headers=responder_headers).json()["items"]]
    assert ids == [mine.id]
    admin_ids = [i["incident_id"] for i in client.get("/api/response/board", headers=admin_headers).json()["items"]]
    assert set(admin_ids) == {mine.id, other.id}


def test_api_incident_response_detail_and_history(client, db, tourist, admin_headers, responder_headers):
    inc = _open(db, tourist, "anomaly", "high")
    r = client.get(f"/api/incidents/{inc.id}/response", headers=admin_headers)
    assert r.status_code == 200
    j = r.json()
    assert j["response"]["severity"] == "high" and j["classification"]["rules"]
    assert [e["kind"] for e in j["escalation_events"]] == ["assigned"]
    assert client.get(f"/api/incidents/{inc.id}/classification", headers=admin_headers).json()["history"]
    e = client.get(f"/api/incidents/{inc.id}/escalations", headers=admin_headers).json()
    assert e["response_stage"] == 1 and e["stage_label"] == "Primary responder"
    # a responder cannot read an incident that is not assigned to their unit
    assert client.get(f"/api/incidents/{inc.id}/response", headers=responder_headers).status_code == 403
    assert client.get("/api/incidents/9999/response", headers=admin_headers).status_code == 404


def test_api_override_requires_admin_reason_and_is_audited(client, db, tourist, admin_headers, responder_headers,
                                                          tourist_headers):
    from app.models.audit import AuditLog
    inc = _open(db, tourist, "anomaly", "high")
    url = f"/api/incidents/{inc.id}/severity"
    assert client.post(url, headers=responder_headers, json={"severity": "low", "reason": "no longer needed"}).status_code == 403
    assert client.post(url, headers=tourist_headers, json={"severity": "low", "reason": "no longer needed"}).status_code == 403
    assert client.post(url, headers=admin_headers, json={"severity": "low"}).status_code == 422
    assert client.post(url, headers=admin_headers, json={"severity": "low", "reason": "abc"}).status_code == 422
    assert client.post(url, headers=admin_headers, json={"severity": "urgent", "reason": "valid reason"}).status_code == 422
    r = client.post(url, headers=admin_headers, json={"severity": "critical", "reason": "supervisor decision"})
    assert r.status_code == 200 and r.json()["previous_severity"] == "high"
    assert r.json()["response"]["severity"] == "critical" and r.json()["response"]["response_target_seconds"] == 60
    assert db.query(AuditLog).filter_by(action="severity_override").count() == 1


def test_api_config_get_put_and_permissions(client, db, admin_headers, responder_headers, tourist_headers):
    from app.models.audit import AuditLog
    assert client.get("/api/response/config", headers=tourist_headers).status_code == 403
    cfg = client.get("/api/response/config", headers=responder_headers).json()
    assert cfg["response_targets_seconds"]["critical"] == 60 and cfg["stage_labels"][1] == "Supervisor"
    assert client.put("/api/response/config", headers=responder_headers,
                      json={"stage2_seconds": 90}).status_code == 403
    r = client.put("/api/response/config", headers=admin_headers,
                   json={"response_targets_seconds": {"critical": 45}, "stage2_seconds": 90})
    assert r.status_code == 200 and r.json()["response_targets_seconds"]["critical"] == 45
    assert r.json()["stage2_seconds"] == 90 and db.query(ResponseSetting).count() == 2
    bad = client.put("/api/response/config", headers=admin_headers,
                     json={"response_targets_seconds": {"critical": 9999}})
    assert bad.status_code == 422
    assert client.put("/api/response/config", headers=admin_headers, json={"nonsense": 1}).status_code == 422
    assert db.query(AuditLog).filter_by(action="response_config_update").count() == 1


def test_api_slo_admin_only_and_validates_range(client, db, tourist, responder_user, admin_headers, responder_headers):
    _managed(db, tourist, "high", 10, responder_user)
    assert client.get("/api/response/slo", headers=responder_headers).status_code == 403
    r = client.get("/api/response/slo?severity=high", headers=admin_headers)
    assert r.status_code == 200 and r.json()["overall"]["compliance_pct"] == 100.0
    assert client.get("/api/response/slo?severity=nope", headers=admin_headers).status_code == 422
    assert client.get("/api/response/slo?from=2030-01-02T00:00:00Z&to=2030-01-01T00:00:00Z",
                      headers=admin_headers).status_code == 422
    by = client.get("/api/response/slo/by-severity", headers=admin_headers).json()
    assert set(by["by_severity"]) == {"critical", "high", "medium", "low"}


def test_incident_list_exposes_response_fields_with_server_time(client, db, tourist, admin_headers):
    inc = _open(db, tourist, "anomaly", "high")
    row = next(i for i in client.get("/api/incidents", headers=admin_headers).json() if i["id"] == inc.id)
    assert row["response_deadline"] and row["response_target_seconds"] == 180 and row["server_time"]
    assert row["classification_reason"].startswith("Classified as HIGH")
    assert row["response_stage"] == 1 and row["escalation_status"] == "active"


def test_migration_head_is_single_and_creates_the_new_tables():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    # a single head, with this feature's migration somewhere in its history
    # (later migrations may legitimately sit on top of it)
    assert len(script.get_heads()) == 1
    assert "g4b5c6d7e8f9" in {r.revision for r in script.walk_revisions()}
