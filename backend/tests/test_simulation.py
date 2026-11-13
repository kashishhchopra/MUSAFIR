"""Simulation Core (services/simulation.py + api/simulation.py).

The engine is driven synchronously here (spawn / trigger / _advance /
_process_pings); the async clock loop is a thin scheduler around those.
"""
import pytest
from sqlalchemy.orm import sessionmaker

from app.models.alert import Alert
from app.models.incident import Incident
from app.models.tourist import Tourist
from app.services.geo import haversine_m
from app.services.simulation import SIM_PREFIX, SimulationEngine, engine
from tests.conftest import make_tourist, make_zone


@pytest.fixture
def sim(db):
    """A fresh engine bound to the test database, with one risky zone to walk into."""
    make_zone(db, "Old Market", "high", 26.165, 91.75)
    eng = SimulationEngine()
    eng._sf = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
    eng._emit = lambda *_a, **_k: None          # no WebSocket clients in tests
    return eng


def _run_ping(eng, agent):
    ts = eng.sim_now
    res = eng._process_pings([(agent.tourist_id, agent.pos[0], agent.pos[1],
                               agent.force_kmh if agent.force_kmh is not None else agent.speed_kmh, ts)])
    assert res and res[0][1] is not None, res
    agent.force_ping, agent.force_kmh = False, None
    return res[0][1]


def test_spawn_creates_sim_tourists_and_is_idempotent(sim, db):
    out = sim.spawn(12)
    assert out == {"count": 12, "created": 12}
    assert db.query(Tourist).filter(Tourist.digital_id.like(f"{SIM_PREFIX}%")).count() == 12
    assert len({a.name for a in sim.agents.values()}) == 12          # unique names
    assert sim.spawn(12)["created"] == 0                              # reuses rows


def test_routes_are_dense_enough_not_to_trip_route_deviation(sim):
    sim.spawn(5)
    for a in sim.agents.values():
        legs = [haversine_m(*p, *q) for p, q in zip(a.route, a.route[1:], strict=False)]
        assert max(legs) <= 460, "route-deviation check measures distance to the nearest waypoint"


def test_normal_walking_raises_no_alerts(sim, db):
    sim.spawn(3)
    a = next(iter(sim.agents.values()))
    for _ in range(6):
        sim.elapsed += 150
        sim._advance(a, 150)
        res = _run_ping(sim, a)
    assert "anomaly" not in res["alerts_raised"]
    assert "route_deviation" not in res["alerts_raised"]


def test_abduction_jump_is_detected_by_the_real_model(sim, db):
    sim.spawn(3)
    a = next(iter(sim.agents.values()))
    sim.elapsed += 150
    _run_ping(sim, a)
    sim.elapsed += 150
    sim.trigger("abduction", a.tourist_id)
    assert a.status == "anomaly"
    res = _run_ping(sim, a)
    assert "anomaly" in res["alerts_raised"]
    assert db.query(Alert).filter(Alert.tourist_id == a.tourist_id, Alert.type == "anomaly").count() >= 1


def test_danger_zone_event_walks_into_zone_and_raises_geofence(sim, db):
    sim.spawn(3)
    a = next(iter(sim.agents.values()))
    sim.trigger("danger_zone", a.tourist_id)
    assert a.status == "danger_zone"
    for _ in range(400):                       # 30 km/h, a few km at most
        sim.elapsed += 30
        sim._advance(a, 30)
        if a.mode == "dwell":
            break
    assert a.mode == "dwell", "agent should have reached the zone"
    res = _run_ping(sim, a)
    assert "geofence" in res["alerts_raised"]


def test_sos_opens_a_critical_incident_and_resolve_clears_it(sim, db):
    sim.spawn(3)
    a = next(iter(sim.agents.values()))
    sim.trigger("sos", a.tourist_id)
    assert a.status == "sos"
    inc = db.query(Incident).filter(Incident.tourist_id == a.tourist_id, Incident.type == "sos").one()
    assert inc.severity == "critical"
    sim.trigger("resolve", a.tourist_id)
    db.expire_all()
    assert a.status != "sos"
    assert db.get(Tourist, a.tourist_id).status == "active"


def test_inactivity_goes_silent_then_reports_with_long_gap(sim, db):
    sim.spawn(3)
    a = next(iter(sim.agents.values()))
    sim.elapsed += 150
    _run_ping(sim, a)
    sim.trigger("inactivity", a.tourist_id, params={"minutes": 70})
    assert a.status == "inactive"
    before = sim.counts()["inactive"]
    assert before == 1
    for _ in range(3):                         # silent: no movement
        sim.elapsed += 1800
        sim._advance(a, 1800)
    assert a.force_ping is True
    res = _run_ping(sim, a)
    assert "anomaly" in res["alerts_raised"]


def test_unknown_event_and_target_are_rejected(sim):
    sim.spawn(3)
    with pytest.raises(ValueError):
        sim.trigger("meteor")
    with pytest.raises(KeyError):
        sim.trigger("sos", tourist_id=999999)


def test_scenario_schedules_five_distinct_steps(sim):
    sim.spawn(10)
    plan = sim.start_scenario("full_demo")["steps"]
    assert [s["event"] for s in plan] == ["off_route", "danger_zone", "inactivity", "abduction", "sos"]
    assert len({s["tourist_id"] for s in plan}) == 5
    sim.elapsed += 130
    sim._run_scenario()
    assert len(sim.scenario) == 4              # first step fired


def test_purge_deletes_all_simulated_data(sim, db):
    sim.spawn(4)
    a = next(iter(sim.agents.values()))
    sim.trigger("sos", a.tourist_id)
    assert sim.reset(purge=True) == {"purged": 4}
    db.expire_all()
    assert db.query(Tourist).filter(Tourist.digital_id.like(f"{SIM_PREFIX}%")).count() == 0
    assert db.query(Incident).count() == 0


def test_speed_must_be_a_supported_value(sim):
    sim.set_speed(60)
    assert sim.speed == 60
    with pytest.raises(ValueError):
        sim.set_speed(7)


# ------------------------------------------------------------------ API
@pytest.fixture
def api_engine(db):
    engine._sf = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)
    engine._emit = lambda *_a, **_k: None
    engine.agents.clear()
    engine.running = False
    yield engine
    engine.agents.clear()
    engine.running = False


def test_simulation_api_requires_admin(client, tourist_headers):
    assert client.get("/api/simulation/state").status_code == 401
    assert client.get("/api/simulation/state", headers=tourist_headers).status_code == 403


def test_simulation_api_flow(client, admin_headers, api_engine, db):
    r = client.post("/api/simulation/spawn", json={"count": 6}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["count"] == 6
    st = client.get("/api/simulation/state", headers=admin_headers).json()
    assert st["counts"]["total"] == 6 and st["running"] is False and st["speeds"] == [1, 2, 5, 10, 30, 60]

    assert client.post("/api/simulation/speed", json={"speed": 7}, headers=admin_headers).status_code == 422
    assert client.post("/api/simulation/speed", json={"speed": 30}, headers=admin_headers).status_code == 200

    tid = st["agents"][0]["id"]
    ev = client.post("/api/simulation/events", json={"event": "sos", "tourist_id": tid}, headers=admin_headers)
    assert ev.status_code == 200 and ev.json()["tourist"]["status"] == "sos"
    assert client.post("/api/simulation/events", json={"event": "nope"}, headers=admin_headers).status_code == 422
    assert client.post("/api/simulation/events", json={"event": "sos", "tourist_id": 424242},
                       headers=admin_headers).status_code == 409

    d = client.get(f"/api/simulation/tourists/{tid}", headers=admin_headers).json()
    assert d["status"] == "sos" and d["route"] and "alerts" in d
    real = make_tourist(db, "Real Person")           # not a SIM- tourist
    assert client.get(f"/api/simulation/tourists/{real.id}", headers=admin_headers).status_code == 404
    alerts = client.get("/api/simulation/alerts", headers=admin_headers).json()
    assert any(a["type"] == "sos" for a in alerts)
