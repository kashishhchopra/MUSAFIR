"""Evaluation Dashboard backend: do MUSAFIR's AI/ML models actually work?

How it works
  1. GENERATE  labelled scenarios with the Simulation Core's own physics
               (services/simulation.py): each one is a short trajectory of
               GPS pings for a virtual tourist, with a known ground-truth
               label (safe / suspicious) fixed *by construction*.
  2. RUN       the production detectors over every ping with the exact same
               primitives monitoring.process_ping uses -- the IsolationForest
               (ml_service.score_anomaly_batch), the route-deviation rule and
               the geofence check -- plus the Random-Forest safety score.
  3. COMPARE   predictions with labels and compute detection rate, false
               alarm rate, precision, ROC/PR, calibration, detection delay,
               a per-group fairness audit (Fairlearn) and SHAP attributions.

Honesty notes (also surfaced in the report itself)
  * Labels come from synthetic scripted behaviour, so this measures how well
    the models detect *those* behaviours -- not real-world accuracy.
  * Scenario prevalence is set by the caller (default 40% suspicious); real
    prevalence is far lower, so `expected_precision_at_5pct` is reported too.
  * SOS is excluded: it is a human trigger, not something a model detects.
  * Nothing is written to the tourist/alert/incident tables.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    brier_score_loss,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.ml import registry
from app.models.incident import Incident
from app.models.tourist import Tourist
from app.models.zone import Zone
from app.services import ml_service, safety
from app.services.geo import haversine_m, min_distance_to_route, zones_containing_point
from app.services.simulation import Agent, SimulationEngine

logger = get_logger(__name__)

STEP_S = 30.0                  # physics step (simulated seconds)
PING_INTERVAL_S = 150.0        # ping cadence (simulated seconds), ~2.5 min
SAFETY_RISK_THRESHOLD = 0.5    # safety score < 50 ("risky"/"danger" band) counts as a flag
FAIRNESS_TOLERANCE = 0.10      # max tolerated TPR/FPR gap between groups
MIN_GROUP_N = 15               # per-class group size below which a gap is "low confidence"
MAX_HISTORY = 12

SAFE_KINDS = ("normal_walk", "taxi_ride", "short_gap", "near_zone_walk")
SUSPICIOUS_KINDS = ("danger_zone", "off_route", "inactivity", "abduction")
KIND_LABELS = {
    "normal_walk": "Normal walk", "taxi_ride": "Taxi ride (fast but safe)",
    "short_gap": "Short signal gap (safe)", "near_zone_walk": "Walk near a risky zone (safe)",
    "danger_zone": "Enters high-risk zone", "off_route": "Strays off route",
    "inactivity": "Goes silent", "abduction": "Abduction jump",
}
AGE_BANDS = (("18-30", 5.0), ("31-50", 4.5), ("51+", 3.5))   # (label, cruising km/h)

DETECTORS = [
    ("anomaly_model", "Isolation Forest (anomaly)", "ML"),
    ("route_deviation", "Route-deviation rule", "Rule"),
    ("geofence", "Geofence (risky zone)", "Rule"),
    ("safety_rf", "Random Forest (safety score)", "ML"),
    ("system", "Combined system", "System"),
]


@dataclass
class Scenario:
    id: int
    kind: str
    label: int                                   # 0 safe, 1 suspicious
    traveller_type: str                          # domestic | international
    age_band: str
    hour: int
    t_event: float | None
    route: list[tuple[float, float]]
    pings: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------ generation
def _quiet_engine(db: Session, rng: random.Random) -> SimulationEngine:
    """A throw-away SimulationEngine: same physics/event code as the live
    simulation, but silent (no log, no WebSocket) and read-only on the DB."""
    eng = SimulationEngine()
    eng._sf = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
    eng._log = lambda *a, **k: None
    eng._emit = lambda *a, **k: None
    eng._rng = rng
    return eng


def _make_scenario(sid: int, kind: str, eng: SimulationEngine, risky, rng: random.Random) -> Scenario:
    label = 1 if kind in SUSPICIOUS_KINDS else 0
    traveller = "international" if rng.random() < 0.4 else "domestic"
    age_label, walk_kmh = rng.choices(AGE_BANDS, weights=[0.4, 0.4, 0.2])[0]
    route = eng._make_route(risky, buffer_deg=0.0008 if kind == "near_zone_walk" else None)
    start = route[rng.randrange(len(route))]
    agent = Agent(0, "EVAL", "eval", "x", route, start, seg=rng.randrange(len(route)),
                  walk_kmh=walk_kmh * rng.uniform(0.9, 1.1))
    # International visitors take cabs more often -- realistic, and it gives the
    # fairness audit something real to measure (fast-but-safe movement).
    if kind == "normal_walk" and rng.random() < (0.12 if traveller == "domestic" else 0.22):
        kind = "taxi_ride"
    if kind == "taxi_ride":
        agent.walk_kmh = rng.uniform(28, 42)

    t_event = rng.uniform(10, 20) * 60 if label else None
    total = 3600.0
    gap = None
    event = None
    if kind == "danger_zone":
        event, total = (lambda: eng._ev_danger_zone(agent, None, 15.0)), t_event + 50 * 60
    elif kind == "off_route":
        event, total = (lambda: eng._ev_off_route(agent, 3.0)), t_event + 40 * 60
    elif kind == "inactivity":
        minutes = rng.uniform(60, 100)
        event, total = (lambda: eng._ev_inactivity(agent, minutes)), t_event + (minutes + 12) * 60
    elif kind == "abduction":
        event, total = (lambda: eng._ev_abduction(agent)), t_event + 15 * 60
    elif kind == "short_gap":
        g0 = rng.uniform(15, 25) * 60
        gap = (g0, g0 + rng.uniform(20, 40) * 60)           # phone dead / tunnel: 20-40 min
        total = gap[1] + 15 * 60

    sc = Scenario(sid, kind, label, traveller, age_label, rng.randint(9, 20), t_event, route)
    eng.elapsed = 0.0
    fired, t, next_ping = False, 0.0, 0.0
    while t <= total:
        eng.elapsed = t
        if event and not fired and t >= t_event:
            event()
            fired = True
        if t > 0:
            eng._advance(agent, STEP_S)
        skip = agent.mode in ("silent", "sos") or (gap and gap[0] <= t < gap[1])
        if not skip and (agent.force_ping or t >= next_ping):
            kmh = agent.force_kmh if agent.force_kmh is not None else agent.speed_kmh
            sc.pings.append({"t": t, "lat": agent.pos[0], "lng": agent.pos[1], "kmh": kmh})
            agent.force_ping, agent.force_kmh = False, None
            next_ping = t + PING_INTERVAL_S * rng.uniform(0.9, 1.1)
        t += STEP_S
    return sc


def generate_scenarios(db: Session, n: int, seed: int, suspicious_fraction: float = 0.4) -> list[Scenario]:
    rng = random.Random(seed)
    eng = _quiet_engine(db, rng)
    risky = eng._risky_zones(db)
    sus_kinds = list(SUSPICIOUS_KINDS) if risky else [k for k in SUSPICIOUS_KINDS if k not in ("danger_zone",)]
    n_sus = round(n * suspicious_fraction)
    kinds = [sus_kinds[i % len(sus_kinds)] for i in range(n_sus)]
    safe_pool = ["normal_walk"] * 6 + ["short_gap"] * 2 + ["near_zone_walk"] * 2
    kinds += [safe_pool[i % len(safe_pool)] for i in range(n - n_sus)]
    rng.shuffle(kinds)
    return [_make_scenario(i + 1, k, eng, risky, rng) for i, k in enumerate(kinds)]


# ------------------------------------------------------------------ scoring
def score_scenarios(db: Session, scenarios: list[Scenario]) -> dict:
    """Run the production detectors over every ping. Returns per-scenario result
    rows plus timing and SHAP aggregates."""
    zones = db.query(Zone).all()
    risky = [z for z in zones if z.risk_level in ("high", "restricted")]
    thr = settings.ROUTE_DEVIATION_THRESHOLD_M

    # ---- build every feature row, then score in ONE vectorised model call
    rows, index = [], []
    for sc in scenarios:
        itin = [{"lat": a, "lng": b} for a, b in sc.route]
        prev = None
        for i, p in enumerate(sc.pings):
            if prev:
                dist_prev = haversine_m(prev["lat"], prev["lng"], p["lat"], p["lng"])
                dt = max(p["t"] - prev["t"], 1.0)
            else:
                dist_prev, dt = 0.0, 1.0
            dist_route = min_distance_to_route(p["lat"], p["lng"], itin)
            p["dist_route"] = dist_route
            p["geofence"] = bool(zones_containing_point(p["lat"], p["lng"], risky))
            rows.append(ml_service.anomaly_features(p["kmh"], dist_prev, dt, dist_route))
            index.append((sc, i))
            prev = p
    t0 = time.perf_counter()
    scored = ml_service.score_anomaly_batch(rows)
    infer_s = time.perf_counter() - t0
    for (sc, i), r in zip(index, scored, strict=True):
        sc.pings[i]["anomaly"], sc.pings[i]["a_score"] = r["is_anomaly"], r["score"]

    results, shap_abs, shap_n = [], {}, 0
    for sc in scenarios:
        start = sc.t_event if sc.label else 0.0
        win = [p for p in sc.pings if p["t"] >= start]
        pre = [p for p in sc.pings if p["t"] < start]
        if not win:                       # degenerate (should not happen); treat as no flags
            win = sc.pings[-1:]

        def first_t(pred):
            hit = [p["t"] for p in win if pred(p)]
            return min(hit) if hit else None

        f_anom = first_t(lambda p: p["anomaly"])
        f_route = first_t(lambda p: p["dist_route"] > thr)
        f_geo = first_t(lambda p: p["geofence"])
        a_max = max(p["a_score"] for p in win)
        r_max = max(p["dist_route"] for p in win)
        g_max = 1.0 if any(p["geofence"] for p in win) else 0.0
        sys_score = max(a_max, g_max, min(1.0, r_max / (2 * thr)))

        # Safety-RF: evaluate at the most suspicious ping of the window.
        worst = max(win, key=lambda p: max(p["a_score"], 1.0 if p["geofence"] else 0.0,
                                           min(1.0, p["dist_route"] / (2 * thr))))
        s = safety.score_at(db, worst["lat"], worst["lng"], sc.hour, worst["a_score"])
        risk = round(1 - s["score"] / 100.0, 4)
        expl = s["breakdown"].get("explanation")
        if expl:
            shap_n += 1
            for k, v in expl["contributions"].items():
                shap_abs[k] = shap_abs.get(k, 0.0) + abs(v)

        flags_first = [t for t in (f_anom, f_route, f_geo) if t is not None]
        results.append({
            "id": sc.id, "kind": sc.kind, "label": sc.label, "traveller_type": sc.traveller_type,
            "age_band": sc.age_band, "t_event": sc.t_event,
            "n_pings": len(sc.pings),
            "pred": {"anomaly_model": f_anom is not None, "route_deviation": f_route is not None,
                     "geofence": f_geo is not None, "safety_rf": risk >= SAFETY_RISK_THRESHOLD,
                     "system": bool(flags_first)},
            "score": {"anomaly_model": a_max, "route_deviation": min(1.0, r_max / (2 * thr)),
                      "geofence": g_max, "safety_rf": risk, "system": sys_score},
            "delay_s": (min(flags_first) - sc.t_event) if (sc.label and flags_first) else None,
            "pre_event_pings": len(pre),
            "pre_event_flags": sum(1 for p in pre if p["anomaly"] or p["geofence"] or p["dist_route"] > thr),
            "win_pings": len(win),
            "win_flags": sum(1 for p in win if p["anomaly"] or p["geofence"] or p["dist_route"] > thr),
        })
    return {"results": results, "inference_ms_per_ping": 1000 * infer_s / max(1, len(rows)),
            "n_pings": len(rows), "shap_mean_abs": (
                {k: round(v / shap_n, 3) for k, v in shap_abs.items()} if shap_n else None),
            "shap_n": shap_n}


# ------------------------------------------------------------------ metrics
def _rate(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _confusion(y, p) -> dict:
    tn, fp, fn, tp = confusion_matrix(y, p, labels=[0, 1]).ravel()
    return {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}


def _basic(cm: dict) -> dict:
    tp, fp, fn, tn = cm["tp"], cm["fp"], cm["fn"], cm["tn"]
    prec, rec = _rate(tp, tp + fp), _rate(tp, tp + fn)
    f1 = round(2 * prec * rec / (prec + rec), 4) if prec and rec else (0.0 if (prec is not None and rec is not None) else None)
    return {"precision": prec, "recall": rec, "fpr": _rate(fp, fp + tn), "f1": f1,
            "accuracy": _rate(tp + tn, tp + fp + fn + tn)}


def _decimate(points: list, k: int = 70) -> list:
    if len(points) <= k:
        return points
    idx = np.linspace(0, len(points) - 1, k).round().astype(int)
    return [points[i] for i in sorted(set(idx.tolist()))]


def _auc(y, s) -> float | None:
    try:
        return round(float(roc_auc_score(y, s)), 4) if len(set(y)) == 2 else None
    except ValueError:
        return None


def _calibration(y, p, bins: int = 10) -> dict:
    y, p = np.asarray(y), np.asarray(p, dtype=float)
    if len(set(y.tolist())) < 2 or len(y) < 10:
        return {"points": [], "brier": None, "ece": None}
    obs, pred = calibration_curve(y, p, n_bins=bins, strategy="uniform")
    edges = np.linspace(0, 1, bins + 1)
    counts = np.histogram(p, bins=edges)[0]
    nonempty = [c for c in counts if c > 0]
    pts = [{"pred": round(float(a), 4), "obs": round(float(b), 4), "n": int(c)}
           for a, b, c in zip(pred, obs, nonempty, strict=False)]
    ece = sum(pt["n"] * abs(pt["pred"] - pt["obs"]) for pt in pts) / len(y)
    return {"points": pts, "brier": round(float(brier_score_loss(y, np.clip(p, 0, 1))), 4), "ece": round(float(ece), 4)}


def _fairness(results: list[dict], attr: str) -> dict:
    from fairlearn.metrics import (
        MetricFrame,
        demographic_parity_difference,
        equalized_odds_difference,
        false_negative_rate,
        false_positive_rate,
        selection_rate,
        true_positive_rate,
    )
    from sklearn.metrics import accuracy_score

    y = np.array([r["label"] for r in results])
    groups = np.array([r[attr] for r in results])
    out = {}
    for det in ("system", "anomaly_model"):
        pred = np.array([r["pred"][det] for r in results], dtype=int)
        mf = MetricFrame(
            metrics={"tpr": true_positive_rate, "fpr": false_positive_rate, "fnr": false_negative_rate,
                     "selection_rate": selection_rate, "accuracy": accuracy_score},
            y_true=y, y_pred=pred, sensitive_features=groups)
        by = []
        for g in sorted(set(groups.tolist())):
            m = groups == g
            row = {k: (None if (v := mf.by_group.loc[g, k]) != v else round(float(v), 4))
                   for k in ("tpr", "fpr", "fnr", "selection_rate", "accuracy")}
            n_pos, n_neg = int(((y == 1) & m).sum()), int(((y == 0) & m).sum())
            row.update({"group": g, "n": int(m.sum()), "n_suspicious": n_pos, "n_safe": n_neg,
                        "low_confidence": min(n_pos, n_neg) < MIN_GROUP_N})
            by.append(row)

        def safe(fn):
            try:
                v = float(fn(y, pred, sensitive_features=groups))
                return None if math.isnan(v) else round(v, 4)
            except Exception:  # noqa: BLE001 -- a group with no positives etc.
                return None

        eo = safe(equalized_odds_difference)
        dp = safe(demographic_parity_difference)
        tprs = [b["tpr"] for b in by if b["tpr"] is not None]
        fprs = [b["fpr"] for b in by if b["fpr"] is not None]
        tpr_gap = round(max(tprs) - min(tprs), 4) if len(tprs) > 1 else None
        fpr_gap = round(max(fprs) - min(fprs), 4) if len(fprs) > 1 else None
        gaps = [g for g in (tpr_gap, fpr_gap) if g is not None]
        low = any(b["low_confidence"] for b in by)
        verdict = ("insufficient_data" if not gaps else
                   "review" if max(gaps) > FAIRNESS_TOLERANCE else "within_tolerance")
        out[det] = {"groups": by, "equalized_odds_difference": eo, "demographic_parity_difference": dp,
                    "tpr_gap": tpr_gap, "fpr_gap": fpr_gap, "verdict": verdict, "low_confidence": low}
    return out


def response_times(db: Session) -> dict:
    """Detection -> response, from real incident-workflow timestamps."""
    rows = db.query(Incident).all()
    sim_ids = {i for (i,) in db.query(Tourist.id).filter(Tourist.digital_id.like("SIM-%")).all()}

    def stat(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0, "mean_s": None, "median_s": None, "p90_s": None}
        a = np.array(vals)
        return {"n": len(vals), "mean_s": round(float(a.mean()), 1),
                "median_s": round(float(np.median(a)), 1), "p90_s": round(float(np.percentile(a, 90)), 1)}

    def delta(a, b):
        return (b - a).total_seconds() if a and b else None

    def build(sel):
        ack = [d for i in sel if (d := delta(i.detected_at, i.acknowledged_at)) is not None]
        dis = [d for i in sel if (d := delta(i.detected_at, i.dispatched_at)) is not None]
        res = [d for i in sel if (d := delta(i.detected_at, i.resolved_at)) is not None]
        return {"incidents": len(sel), "acknowledge": stat(ack), "dispatch": stat(dis), "resolve": stat(res)}

    return {"all": build(rows), "simulated": build([i for i in rows if i.tourist_id in sim_ids]),
            "source": "incident workflow timestamps (detected -> acknowledged -> dispatched -> resolved)"}


def compute_report(scored: dict, scenarios: list[Scenario], meta: dict, db: Session) -> dict:
    res = scored["results"]
    y = [r["label"] for r in res]
    n_sus, n_safe = sum(y), len(y) - sum(y)

    detectors = []
    for key, name, typ in DETECTORS:
        pred = [int(r["pred"][key]) for r in res]
        cm = _confusion(y, pred)
        detectors.append({"key": key, "name": name, "type": typ, **cm, **_basic(cm),
                          "auc": _auc(y, [r["score"][key] for r in res])})
    sysd = next(d for d in detectors if d["key"] == "system")

    delays = [r["delay_s"] for r in res if r["delay_s"] is not None]
    rec, fpr = sysd["recall"], sysd["fpr"]
    exp_prec = (round(rec * 0.05 / (rec * 0.05 + fpr * 0.95), 4)
                if rec is not None and fpr is not None and (rec * 0.05 + fpr * 0.95) > 0 else None)

    by_kind = []
    for kind in list(SUSPICIOUS_KINDS) + list(SAFE_KINDS):
        rr = [r for r in res if r["kind"] == kind]
        if not rr:
            continue
        sus = rr[0]["label"] == 1
        flagged = sum(r["pred"]["system"] for r in rr)
        d = [r["delay_s"] for r in rr if r["delay_s"] is not None]
        by_kind.append({
            "kind": kind, "name": KIND_LABELS[kind], "label": "suspicious" if sus else "safe", "n": len(rr),
            "flagged": flagged, "rate": round(flagged / len(rr), 4),   # recall if suspicious, false-alarm rate if safe
            "avg_delay_min": round(float(np.mean(d)) / 60, 2) if d else None,
            "by_detector": {k: round(sum(r["pred"][k] for r in rr) / len(rr), 4) for k, _n, _t in DETECTORS},
        })

    sys_score = np.array([r["score"]["system"] for r in res])
    an_score = np.array([r["score"]["anomaly_model"] for r in res])
    curves = {"roc": {}, "pr": {}}
    if 0 < n_sus < len(y):
        for key, s in (("system", sys_score), ("anomaly_model", an_score)):
            f, t, _ = roc_curve(y, s)
            curves["roc"][key] = _decimate([[round(float(a), 4), round(float(b), 4)] for a, b in zip(f, t, strict=True)])
            pr, rc, _ = precision_recall_curve(y, s)
            curves["pr"][key] = _decimate([[round(float(a), 4), round(float(b), 4)] for a, b in zip(rc, pr, strict=True)])
    bins = np.linspace(0, 1, 11)
    curves["score_hist"] = [
        {"bin": f"{bins[i]:.1f}-{bins[i + 1]:.1f}",
         "safe": int(((an_score >= bins[i]) & (an_score < bins[i + 1] if i < 9 else an_score <= 1) & (np.array(y) == 0)).sum()),
         "suspicious": int(((an_score >= bins[i]) & (an_score < bins[i + 1] if i < 9 else an_score <= 1) & (np.array(y) == 1)).sum())}
        for i in range(10)]
    curves["calibration"] = {
        "anomaly_model": _calibration(y, an_score),
        "safety_rf": _calibration(y, [r["score"]["safety_rf"] for r in res]),
        "system": _calibration(y, sys_score),
    }

    reg = registry.load_registry(settings.ML_MODELS_DIR) or {}
    versions = {m: reg.get(m, {}).get("active_version") for m in ("anomaly", "safety", "zones")}
    inference_mode = "model" if ml_service._load("anomaly_isoforest.joblib") is not None else "rule-based fallback"

    # Ping-level alert fatigue: of all pings that are SAFE by ground truth (every ping
    # of a safe scenario + the pre-event pings of a suspicious one), how many raised a flag?
    safe_pings = (sum(r["win_pings"] for r in res if r["label"] == 0)
                  + sum(r["pre_event_pings"] for r in res if r["label"] == 1))
    false_flags = (sum(r["win_flags"] for r in res if r["label"] == 0)
                   + sum(r["pre_event_flags"] for r in res if r["label"] == 1))
    return {
        **meta,
        "models": {"active_versions": versions, "inference_mode": inference_mode},
        "totals": {"n_scenarios": len(res), "n_suspicious": n_sus, "n_safe": n_safe, "n_pings": scored["n_pings"]},
        "headline": {
            "detection_rate": sysd["recall"], "false_positive_rate": sysd["fpr"], "precision": sysd["precision"],
            "recall": sysd["recall"], "f1": sysd["f1"], "accuracy": sysd["accuracy"], "roc_auc": sysd["auc"],
            "avg_detection_delay_min": round(float(np.mean(delays)) / 60, 2) if delays else None,
            "median_detection_delay_min": round(float(np.median(delays)) / 60, 2) if delays else None,
            "p90_detection_delay_min": round(float(np.percentile(delays, 90)) / 60, 2) if delays else None,
            "inference_ms_per_ping": round(scored["inference_ms_per_ping"], 4),
            "expected_precision_at_5pct_prevalence": exp_prec,
            "ping_false_alarm_rate": _rate(false_flags, safe_pings), "safe_pings": safe_pings,
        },
        "confusion": {k: sysd[k] for k in ("tn", "fp", "fn", "tp")},
        "detectors": detectors,
        "by_scenario": by_kind,
        "curves": curves,
        "fairness": {"traveller_type": _fairness(res, "traveller_type"), "age_band": _fairness(res, "age_band"),
                     "tolerance": FAIRNESS_TOLERANCE, "min_group_n": MIN_GROUP_N,
                     "note": ("Groups are synthetic attributes assigned to simulated tourists (traveller type "
                              "and age band affect only cruising speed and cab usage). No real personal data "
                              "is used. Small groups give noisy gaps -- see low_confidence flags.")},
        "explainability": {"method": "SHAP TreeExplainer on the safety Random Forest",
                           "mean_abs_contribution": scored["shap_mean_abs"], "n_samples": scored["shap_n"]},
        "response": response_times(db),
        "notes": [
            "Ground-truth labels come from scripted simulated behaviour, so these figures measure detection of "
            "those behaviours -- not real-world accuracy.",
            "Scenario prevalence is set by the run config; precision depends on it (see expected precision at 5% prevalence).",
            "Detection delay is in simulated minutes and depends on the ping cadence; inference latency is real wall-clock.",
            "SOS is excluded from detection metrics: it is a human trigger, not a model output.",
        ],
    }


# ------------------------------------------------------------------ persistence + entry point
def _store_path() -> str:
    os.makedirs(settings.EVALUATION_DIR, exist_ok=True)
    return os.path.join(settings.EVALUATION_DIR, "evaluation_runs.json")


def load_runs() -> list[dict]:
    p = _store_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save_runs(runs: list[dict]) -> None:
    p = _store_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(runs[-MAX_HISTORY:], f)
    os.replace(tmp, p)


def summarise(report: dict) -> dict:
    h = report["headline"]
    return {"run_id": report["run_id"], "created_at": report["created_at"],
            "n_scenarios": report["totals"]["n_scenarios"], "seed": report["config"]["seed"],
            "detection_rate": h["detection_rate"], "false_positive_rate": h["false_positive_rate"],
            "precision": h["precision"], "f1": h["f1"], "roc_auc": h["roc_auc"],
            "avg_detection_delay_min": h["avg_detection_delay_min"]}


def run_evaluation(db: Session, n_scenarios: int = 100, seed: int | None = None,
                   suspicious_fraction: float = 0.4) -> dict:
    n_scenarios = max(20, min(int(n_scenarios), 1000))
    seed = int(seed) if seed is not None else random.randrange(1, 10**6)
    started = time.perf_counter()
    scenarios = generate_scenarios(db, n_scenarios, seed, suspicious_fraction)
    scored = score_scenarios(db, scenarios)
    meta = {
        "run_id": uuid.uuid4().hex[:12],
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": {"n_scenarios": n_scenarios, "seed": seed, "suspicious_fraction": suspicious_fraction,
                   "ping_interval_s": PING_INTERVAL_S,
                   "route_deviation_threshold_m": settings.ROUTE_DEVIATION_THRESHOLD_M,
                   "safety_risk_threshold": SAFETY_RISK_THRESHOLD,
                   "scenario_kinds": list(SAFE_KINDS) + list(SUSPICIOUS_KINDS)},
    }
    report = compute_report(scored, scenarios, meta, db)
    report["duration_s"] = round(time.perf_counter() - started, 2)
    runs = load_runs()
    runs.append(report)
    _save_runs(runs)
    logger.info("evaluation_run", run_id=report["run_id"], n=n_scenarios,
                detection_rate=report["headline"]["detection_rate"], fpr=report["headline"]["false_positive_rate"])
    return report
