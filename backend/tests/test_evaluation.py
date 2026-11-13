"""AI/ML Evaluation Dashboard (services/evaluation.py + api/evaluation.py)."""
import json
from datetime import timedelta

import pytest

from app.core.config import settings
from app.core.time import utc_now
from app.services import evaluation
from app.services.monitoring import process_ping
from tests.conftest import make_tourist, make_zone


@pytest.fixture
def eval_env(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "EVALUATION_DIR", str(tmp_path / "eval"))
    make_zone(db, "Old Market", "high", 26.165, 91.75)
    return db


def test_scenarios_are_reproducible_and_labelled(eval_env):
    a = evaluation.generate_scenarios(eval_env, 40, seed=5)
    b = evaluation.generate_scenarios(eval_env, 40, seed=5)
    assert [(s.kind, s.label, len(s.pings)) for s in a] == [(s.kind, s.label, len(s.pings)) for s in b]
    assert sum(s.label for s in a) == 16                       # 40% suspicious
    assert all(s.t_event is not None for s in a if s.label)
    assert all(s.t_event is None for s in a if not s.label)
    assert all(len(s.pings) >= 5 for s in a)


def test_report_structure_and_sane_detection(eval_env):
    r = evaluation.run_evaluation(eval_env, 60, seed=11)
    h = r["headline"]
    assert r["totals"]["n_scenarios"] == 60 and r["totals"]["n_suspicious"] == 24
    assert set(r["confusion"]) == {"tn", "fp", "fn", "tp"} and sum(r["confusion"].values()) == 60
    for k in ("detection_rate", "false_positive_rate", "precision", "f1", "roc_auc",
              "avg_detection_delay_min", "inference_ms_per_ping", "expected_precision_at_5pct_prevalence"):
        assert k in h
    assert {d["key"] for d in r["detectors"]} == {"anomaly_model", "route_deviation", "geofence", "safety_rf", "system"}
    assert 0 <= h["detection_rate"] <= 1 and 0 <= h["false_positive_rate"] <= 1
    by = {k["kind"]: k for k in r["by_scenario"]}
    assert by["abduction"]["rate"] >= 0.9                       # an obvious 165 km/h jump must be caught
    assert by["normal_walk"]["rate"] <= 0.1                     # ordinary walking must not alarm
    assert r["curves"]["roc"]["system"][0] == [0.0, 0.0]
    assert r["curves"]["calibration"]["anomaly_model"]["points"]
    for attr in ("traveller_type", "age_band"):
        sysf = r["fairness"][attr]["system"]
        assert sysf["groups"] and sysf["verdict"] in ("within_tolerance", "review", "insufficient_data")
    assert evaluation.load_runs()[-1]["run_id"] == r["run_id"]


def test_history_keeps_only_recent_runs(eval_env):
    for i in range(evaluation.MAX_HISTORY + 2):
        evaluation.run_evaluation(eval_env, 20, seed=i)
    assert len(evaluation.load_runs()) == evaluation.MAX_HISTORY


def test_evaluation_verdicts_match_the_real_pipeline(eval_env):
    """The harness re-implements nothing: for sampled scenarios, its per-ping
    anomaly / route-deviation / geofence verdicts must equal the alerts the real
    monitoring.process_ping raises for the same pings."""
    db = eval_env
    scenarios = [s for s in evaluation.generate_scenarios(db, 30, seed=3)
                 if s.kind in ("abduction", "off_route", "danger_zone", "normal_walk")][:8]
    assert scenarios
    evaluation.score_scenarios(db, scenarios)
    base = utc_now() - timedelta(days=2)
    for sc in scenarios:
        itin = [{"name": f"w{i}", "lat": a, "lng": b} for i, (a, b) in enumerate(sc.route)]
        t = make_tourist(db, f"Eval {sc.id}", itinerary=itin)
        for p in sc.pings:
            res = process_ping(db, t, p["lat"], p["lng"], p["kmh"], now=base + timedelta(seconds=p["t"]))
            raised = set(res["alerts_raised"])
            assert ("anomaly" in raised) == p["anomaly"], (sc.kind, p["t"])
            assert ("route_deviation" in raised) == (p["dist_route"] > settings.ROUTE_DEVIATION_THRESHOLD_M)
            assert ("geofence" in raised) == p["geofence"], (sc.kind, p["t"])


def test_metric_helpers():
    cm = evaluation._confusion([1, 1, 0, 0], [1, 0, 0, 1])
    assert cm == {"tn": 1, "fp": 1, "fn": 1, "tp": 1}
    b = evaluation._basic(cm)
    assert b["precision"] == 0.5 and b["recall"] == 0.5 and b["fpr"] == 0.5 and b["accuracy"] == 0.5
    cal = evaluation._calibration([0] * 50 + [1] * 50, [0.1] * 50 + [0.9] * 50)
    assert cal["ece"] == pytest.approx(0.1, abs=1e-3)


def test_fairness_flags_a_biased_detector():
    res = []
    for i in range(80):                    # group A: perfect; group B: every safe case false-alarmed
        for grp, fp in (("A", False), ("B", True)):
            res.append({"label": 0, "traveller_type": grp, "pred": {"system": fp, "anomaly_model": fp}})
            res.append({"label": 1, "traveller_type": grp, "pred": {"system": True, "anomaly_model": True}})
    out = evaluation._fairness(res, "traveller_type")["system"]
    assert out["verdict"] == "review" and out["fpr_gap"] == 1.0 and out["low_confidence"] is False


def test_response_times_from_incident_timestamps(eval_env):
    from app.models.incident import Incident
    db = eval_env
    t = make_tourist(db)
    now = utc_now()
    db.add(Incident(tourist_id=t.id, type="sos", severity="critical", status="resolved", description="x",
                    detected_at=now, acknowledged_at=now + timedelta(seconds=30),
                    dispatched_at=now + timedelta(seconds=90), resolved_at=now + timedelta(seconds=600)))
    db.commit()
    r = evaluation.response_times(db)["all"]
    assert r["acknowledge"]["mean_s"] == 30 and r["dispatch"]["mean_s"] == 90 and r["resolve"]["mean_s"] == 600


# ------------------------------------------------------------------ API
def test_evaluation_api_requires_admin(client, tourist_headers):
    assert client.get("/api/ml/evaluation/latest").status_code == 401
    assert client.get("/api/ml/evaluation/latest", headers=tourist_headers).status_code == 403
    assert client.post("/api/ml/evaluation/run", json={}, headers=tourist_headers).status_code == 403


def test_evaluation_api_flow(client, admin_headers, eval_env):
    empty = client.get("/api/ml/evaluation/latest", headers=admin_headers).json()
    assert empty == {"latest": None, "history": []}
    assert client.post("/api/ml/evaluation/run", json={"n_scenarios": 5}, headers=admin_headers).status_code == 422
    r = client.post("/api/ml/evaluation/run", json={"n_scenarios": 30, "seed": 1}, headers=admin_headers)
    assert r.status_code == 200, r.text
    rid = r.json()["run_id"]
    latest = client.get("/api/ml/evaluation/latest", headers=admin_headers).json()
    assert latest["latest"]["run_id"] == rid and latest["history"][0]["run_id"] == rid
    assert client.get(f"/api/ml/evaluation/runs/{rid}", headers=admin_headers).status_code == 200
    assert client.get("/api/ml/evaluation/runs/nope", headers=admin_headers).status_code == 404
    json.dumps(r.json())                                       # fully JSON-serialisable
