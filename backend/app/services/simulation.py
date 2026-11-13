"""Simulation Core: virtual tourists, a controllable clock, and scripted events.

Drives the *real* monitoring pipeline (services/monitoring.py:process_ping and
trigger_sos) with simulated GPS, so a demo shows the genuine chain:

    simulated GPS -> ML anomaly / geofence / route-deviation -> alert ->
    incident -> control-room dashboard (WebSocket) -> response workflow.

Nothing here fakes a detection: an event only changes *where the tourist is and
how they move*; whether that raises an alert is decided by the existing code.

Design notes
  * Single in-process engine (module-level `engine`). Run with WEB_CONCURRENCY=1,
    like the WebSocket manager and rate limiter.
  * Simulated time = speed x real time (1x-60x). Pings are stamped with
    *simulated* time (see process_ping's `now`) so inactivity/speed/trajectory
    maths stay physically consistent at any speed.
  * Simulated tourists are ordinary Tourist rows (digital_id "SIM-xxxx") so the
    rest of the app (incidents, alerts, dispatch, E-FIR) works on them untouched.
    Movement between pings is in-memory; only the periodic ping hits the DB.
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.core.logging import get_logger
from app.core.time import utc_now
from app.db.session import SessionLocal
from app.models.tourist import Tourist
from app.models.zone import Zone
from app.services import hashchain, ml_service
from app.services.geo import haversine_m, zone_centroid, zones_containing_point
from app.websocket.manager import manager

logger = get_logger(__name__)

SPEEDS = (1, 2, 5, 10, 30, 60)
SIM_PREFIX = "SIM-"
TICK_REAL_S = 0.5            # real seconds between engine ticks
MAX_PINGS_PER_TICK = 8       # backpressure: DB work is bounded per tick
WALK_KMH = 4.5
CAB_KMH = 30.0
TRAIL_POINTS = 150
LOG_MAX = 400

# Real Guwahati landmarks (same coordinates the seed data uses) that routes are
# built from, all on the city's south bank of the Brahmaputra (so walkers never
# cross the river; detours and jumps below also head south/east, away from it). Chosen so straight legs between them stay clear of the seeded
# high-risk / restricted zones; routes are re-checked against live zones anyway.
ANCHORS = [
    ("City Center", 26.1445, 91.7362),
    ("Kamakhya Temple", 26.1665, 91.7055),
    ("Railway Station", 26.1809, 91.7378),
    ("Paltan Bazaar", 26.1833, 91.7458),
    ("Fancy Bazaar", 26.1865, 91.7515),
    ("Assamese Thali Spot", 26.1810, 91.7460),
    ("Hillside Trek Start", 26.1250, 91.7150),
]

_FIRST = ["Aarav", "Diya", "Vihaan", "Ananya", "Kabir", "Isha", "Arjun", "Meera", "Rohan", "Tara",
          "Emma", "Liam", "Sofia", "Noah", "Mia", "Lucas", "Yuki", "Kenji", "Hana", "Omar",
          "Zara", "Ethan", "Chloe", "Mateo", "Priya", "Sanjay", "Neha", "Rahul", "Aisha", "Dev"]
_LAST = ["Sharma", "Verma", "Das", "Baruah", "Nair", "Gupta", "Rao", "Khan", "Singh", "Bora",
         "Smith", "Brown", "Rossi", "Tanaka", "Müller", "Garcia", "Dubois", "Kim", "Silva", "Novak"]
_NATIONS = [("Indian", "IN", "aadhaar")] * 6 + [
    ("British", "GB", "passport"), ("Italian", "IT", "passport"), ("Japanese", "JP", "passport"),
    ("American", "US", "passport"), ("German", "DE", "passport"), ("French", "FR", "passport"),
]

EVENTS = {
    "danger_zone": "Enters a high-risk / restricted zone",
    "off_route": "Strays far from the planned itinerary",
    "inactivity": "Goes silent (no movement, no GPS) for a long period",
    "abduction": "Sudden high-speed jump (vehicle abduction pattern)",
    "sos": "Presses the SOS button",
    "resolve": "Tourist is safe again and resumes their route",
}


def _bearing_move(lat: float, lng: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    br = math.radians(bearing_deg)
    dlat = (dist_m * math.cos(br)) / 111_320.0
    dlng = (dist_m * math.sin(br)) / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat + dlat, lng + dlng


@dataclass
class Agent:
    tourist_id: int
    digital_id: str
    name: str
    nationality: str
    route: list[tuple[float, float]]            # looping waypoint route
    pos: tuple[float, float]
    seg: int = 0                                # index of the waypoint we are walking toward
    mode: str = "route"                         # route | goto | dwell | silent | sos
    tag: str | None = None                      # off_route | danger_zone | inactive | anomaly | sos
    target: tuple[float, float] | None = None
    goto_kmh: float = CAB_KMH
    after: str = "return"                       # what to do on arrival: dwell|return
    dwell_left: float = 0.0
    resume_at: float = 0.0                      # sim-seconds (silent mode)
    last_ping_at: float = 0.0
    next_ping_at: float = 0.0
    force_ping: bool = False
    force_kmh: float | None = None              # speed to report on the next ping
    score: float = 100.0
    flags: list[str] = field(default_factory=list)
    anomaly_score: float = 0.0
    last_ping_ok: bool = True
    trail: deque = field(default_factory=lambda: deque(maxlen=TRAIL_POINTS))
    speed_kmh: float = 0.0
    walk_kmh: float = WALK_KMH                  # per-tourist cruising speed

    @property
    def status(self) -> str:
        if self.mode == "sos":
            return "sos"
        return self.tag or "normal"

    def snapshot(self, with_route: bool = False) -> dict:
        d = {
            "id": self.tourist_id, "digital_id": self.digital_id, "name": self.name,
            "nationality": self.nationality,
            "lat": round(self.pos[0], 6), "lng": round(self.pos[1], 6),
            "status": self.status, "mode": self.mode, "score": round(self.score, 1),
            "flags": self.flags, "speed_kmh": round(self.speed_kmh, 1),
            "anomaly_score": round(self.anomaly_score, 3),
        }
        if with_route:
            d["route"] = [[round(a, 6), round(b, 6)] for a, b in self.route]
            d["trail"] = [[round(a, 6), round(b, 6)] for a, b in self.trail]
        return d


class SimulationEngine:
    def __init__(self) -> None:
        self.agents: dict[int, Agent] = {}
        self.running = False
        self.speed = 1
        self.elapsed = 0.0                       # simulated seconds since epoch
        self.epoch: datetime = utc_now()
        self.log: deque = deque(maxlen=LOG_MAX)
        self.scenario: list[dict] = []           # pending timeline steps
        self.scenario_name: str | None = None
        self._task: asyncio.Task | None = None
        self._rng = random.Random()
        self._seq = 0
        # Injectable so tests can point the engine at their in-memory database.
        self._sf = SessionLocal

    # ------------------------------------------------------------------ helpers
    @property
    def sim_now(self) -> datetime:
        return self.epoch + timedelta(seconds=self.elapsed)

    def _log(self, kind: str, text: str, tourist_id: int | None = None, level: str = "info") -> None:
        self._seq += 1
        agent = self.agents.get(tourist_id) if tourist_id else None
        entry = {
            "seq": self._seq, "sim_time": self.sim_now.isoformat(timespec="seconds"),
            "kind": kind, "level": level, "text": text,
            "tourist_id": tourist_id, "tourist": agent.name if agent else None,
        }
        self.log.append(entry)
        self._emit({"event": "sim_log", **entry})

    def _emit(self, message: dict) -> None:
        """Fire-and-forget WS push; safe from the loop thread or a worker thread."""
        loop = manager._loop
        if loop is None or not manager.active:
            return
        try:
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------ spawning
    def _risky_zones(self, db) -> list[Zone]:
        return [z for z in db.query(Zone).all() if z.risk_level in ("high", "restricted")]

    @staticmethod
    def _densify(points: list[tuple[float, float]], max_gap_m: float = 450.0) -> list[tuple[float, float]]:
        """Insert intermediate points so no leg exceeds `max_gap_m`.

        The pipeline's route-deviation check measures distance to the nearest
        *waypoint* (services/geo.py:min_distance_to_route), so a sparse route
        would flag a tourist walking mid-leg as "off route".
        """
        out: list[tuple[float, float]] = []
        for (a, b), (c, d) in zip(points, points[1:], strict=False):
            n = max(1, math.ceil(haversine_m(a, b, c, d) / max_gap_m))
            for i in range(n):
                out.append((a + (c - a) * i / n, b + (d - b) * i / n))
        out.append(points[-1])
        return out

    @staticmethod
    def _clear_of(points, risky: list[Zone], buffer_deg: float) -> bool:
        """True if no point (nor any of 4 points `buffer_deg` away) is inside a risky zone."""
        for la, ln in points:
            probes = [(la, ln), (la + buffer_deg, ln), (la - buffer_deg, ln),
                      (la, ln + buffer_deg), (la, ln - buffer_deg)]
            if any(zones_containing_point(pa, pb, risky) for pa, pb in probes):
                return False
        return True

    def _make_route(self, risky: list[Zone], buffer_deg: float | None = None) -> list[tuple[float, float]]:
        # Keep ~650 m clear of high-risk zones first (so ordinary walkers don't
        # constantly trip "predicted entry"), relaxing if the map is too crowded.
        for attempt in range(90):
            buf = (buffer_deg if buffer_deg is not None else 0.006) if attempt < 60 else 0.002
            picks = self._rng.sample(ANCHORS, k=self._rng.randint(3, 5))
            pts = [(la + self._rng.uniform(-0.0015, 0.0015), ln + self._rng.uniform(-0.0015, 0.0015))
                   for _n, la, ln in picks]
            pts.append(pts[0])  # loop back
            dense = self._densify(pts, 100.0)
            if self._clear_of(dense, risky, buf):
                return self._densify(pts)
        _n, la, ln = ANCHORS[0]
        return [(la, ln), (la + 0.004, ln + 0.004), (la, ln)]

    def spawn(self, count: int = 50) -> dict:
        """Create/reuse `count` SIM tourists and (re)build their agents. Stops the clock."""
        self.running = False
        count = max(1, min(int(count), 100))
        db = self._sf()
        try:
            risky = self._risky_zones(db)
            existing = (db.query(Tourist).filter(Tourist.digital_id.like(f"{SIM_PREFIX}%"))
                        .order_by(Tourist.id).all())
            created = 0
            rows = existing[:count]
            now = utc_now()
            used = {t.full_name for t in existing}
            for i in range(len(rows), count):
                nat, code, doc = self._rng.choice(_NATIONS)
                while True:
                    name = f"{self._rng.choice(_FIRST)} {self._rng.choice(_LAST)}"
                    if name not in used or len(used) >= len(_FIRST) * len(_LAST):
                        break
                used.add(name)
                t = Tourist(
                    digital_id=f"{SIM_PREFIX}{i + 1:04d}", full_name=name, nationality=nat,
                    nationality_code=code, document_type=doc,
                    document_number=f"SIM-{self._rng.randint(10**7, 10**8 - 1)}",
                    phone=f"+91-9{self._rng.randint(10**8, 10**9 - 1)}",
                    hotel="Simulated Hotel", itinerary="[]",
                    emergency_contacts=json.dumps([{"name": "Sim Contact", "phone": "+91-90000-00000",
                                                    "relation": "family"}]),
                    trip_start=now - timedelta(days=1), trip_end=now + timedelta(days=6),
                    safety_score=95.0, status="active", tracking_enabled=True,
                )
                db.add(t)
                db.flush()
                hashchain.append_block(db, t, "ID_ISSUED", {"digital_id": t.digital_id, "name": t.full_name,
                                                              "simulated": True})
                rows.append(t)
                created += 1
            self.agents.clear()
            self.scenario, self.scenario_name = [], None
            for t in rows:
                route = self._make_route(risky)
                start = route[self._rng.randrange(len(route))]
                t.itinerary = json.dumps([{"name": f"wp{k}", "lat": la, "lng": ln}
                                          for k, (la, ln) in enumerate(route)])
                t.last_lat, t.last_lng, t.last_seen = start[0], start[1], self.sim_now
                t.status, t.safety_score = "active", 95.0
                a = Agent(t.id, t.digital_id, t.full_name, t.nationality, route, start,
                          seg=self._rng.randrange(len(route)))
                a.next_ping_at = self.elapsed + self._rng.uniform(0, 120)  # staggered
                a.last_ping_at = self.elapsed
                a.trail.append(start)
                self.agents[t.id] = a
            db.commit()
        finally:
            db.close()
        self._log("system", f"Spawned {len(self.agents)} simulated tourists ({created} new).")
        self._emit(self.state_message())
        return {"count": len(self.agents), "created": created}

    # ------------------------------------------------------------------ control
    async def play(self) -> None:
        if not self.agents:
            await asyncio.to_thread(self.spawn, 50)
        if self.running:
            return
        self.running = True
        self._log("system", f"Simulation started at {self.speed}x.")
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    def pause(self) -> None:
        if self.running:
            self.running = False
            self._log("system", "Simulation paused.")

    def set_speed(self, speed: int) -> None:
        if speed not in SPEEDS:
            raise ValueError(f"speed must be one of {SPEEDS}")
        self.speed = speed
        self._log("system", f"Speed set to {speed}x.")
        self._emit(self.state_message())

    def reset(self, purge: bool = False) -> dict:
        """Stop and rebuild agents; with purge=True also delete every simulated tourist
        and all their alerts / incidents / pings."""
        self.running = False
        deleted = 0
        if purge:
            deleted = self._purge()
            self.agents.clear()
            self.log.clear()
            self.scenario, self.scenario_name = [], None
            self.elapsed, self.epoch = 0.0, utc_now()
            self._emit(self.state_message())
            return {"purged": deleted}
        n = len(self.agents) or 50
        self.log.clear()
        out = self.spawn(n)
        self._log("system", "Simulation reset (positions, scenario and log).")
        return out

    def _purge(self) -> int:
        from sqlalchemy import select

        from app.db.session import Base

        db = self._sf()
        try:
            ids = [i for (i,) in db.query(Tourist.id).filter(Tourist.digital_id.like(f"{SIM_PREFIX}%")).all()]
            if not ids:
                return 0
            tables = list(reversed(Base.metadata.sorted_tables))
            inc = Base.metadata.tables["incidents"]
            inc_ids = select(inc.c.id).where(inc.c.tourist_id.in_(ids))
            for t in tables:                      # rows hanging off incidents first
                if "incident_id" in t.c and t.name != "incidents":
                    db.execute(t.delete().where(t.c.incident_id.in_(inc_ids)))
            for t in tables:                      # then everything keyed by tourist
                if t.name != "tourists" and "tourist_id" in t.c:
                    db.execute(t.delete().where(t.c.tourist_id.in_(ids)))
            db.execute(Base.metadata.tables["tourists"].delete().where(Base.metadata.tables["tourists"].c.id.in_(ids)))
            db.commit()
            return len(ids)
        finally:
            db.close()

    # ------------------------------------------------------------------ events
    def _pick_agent(self, tourist_id: int | None) -> Agent:
        if tourist_id is not None:
            if tourist_id not in self.agents:
                raise KeyError(f"Tourist {tourist_id} is not part of the simulation")
            return self.agents[tourist_id]
        free = [a for a in self.agents.values() if a.mode == "route" and a.tag is None]
        if not free:
            raise KeyError("No free simulated tourist available")
        return self._rng.choice(free)

    def trigger(self, event: str, tourist_id: int | None = None, zone_id: int | None = None,
                params: dict | None = None) -> dict:
        if event not in EVENTS:
            raise ValueError(f"Unknown event '{event}'. Valid: {', '.join(EVENTS)}")
        if not self.agents:
            raise KeyError("No simulated tourists yet -- spawn them first")
        params = params or {}
        a = self._pick_agent(tourist_id)
        if event == "resolve":
            self._resolve(a)
        elif event == "danger_zone":
            self._ev_danger_zone(a, zone_id, float(params.get("dwell_min", 15)))
        elif event == "off_route":
            self._ev_off_route(a, float(params.get("distance_km", 3.0)))
        elif event == "inactivity":
            self._ev_inactivity(a, float(params.get("minutes", 70)))
        elif event == "abduction":
            self._ev_abduction(a)
        elif event == "sos":
            self._ev_sos(a, str(params.get("message") or "Help! I feel unsafe."))
        self._emit(self.state_message())
        return {"event": event, "tourist": a.snapshot()}

    def _resolve(self, a: Agent) -> None:
        was = a.status
        a.mode, a.tag, a.target = "route", None, None
        a.force_kmh = None
        a.flags = []
        db = self._sf()
        try:
            t = db.get(Tourist, a.tourist_id)
            if t and t.status != "active":
                t.status = "active"
                db.commit()
        finally:
            db.close()
        self._head_back(a)
        self._log("event", f"{a.name} is safe again (was: {was}); resuming route.", a.tourist_id)

    def _head_back(self, a: Agent) -> None:
        """Walk back to the nearest route waypoint, then carry on along the route."""
        i = min(range(len(a.route)), key=lambda k: haversine_m(a.pos[0], a.pos[1], *a.route[k]))
        if haversine_m(a.pos[0], a.pos[1], *a.route[i]) > 60:
            a.mode, a.target, a.goto_kmh, a.after = "goto", a.route[i], CAB_KMH, "return_done"
        else:
            a.mode, a.target = "route", None
        a.seg = i

    def _ev_danger_zone(self, a: Agent, zone_id: int | None, dwell_min: float) -> None:
        db = self._sf()
        try:
            zones = [z for z in db.query(Zone).all() if z.risk_level in ("high", "restricted")]
            if zone_id is not None:
                zones = [z for z in zones if z.id == zone_id] or zones
            if not zones:
                raise KeyError("No high-risk or restricted zone exists to walk into")
            z = self._rng.choice(zones)
            c = zone_centroid(z)
            zname = z.name
        finally:
            db.close()
        if not c:
            raise KeyError("Selected zone has no usable geometry")
        a.mode, a.tag, a.target, a.goto_kmh = "goto", "danger_zone", c, CAB_KMH
        a.after, a.dwell_left = "dwell", dwell_min * 60
        self._log("event", f"{a.name} is heading into '{zname}' (high-risk zone).", a.tourist_id, "warn")

    def _ev_off_route(self, a: Agent, distance_km: float) -> None:
        db = self._sf()
        try:
            risky = [z for z in db.query(Zone).all() if z.risk_level in ("high", "restricted")]
        finally:
            db.close()
        # Head directly away from every waypoint; pick a bearing that stays out of risky zones.
        for _ in range(16):
            br = self._rng.uniform(100, 260)
            tgt = _bearing_move(a.pos[0], a.pos[1], br, max(distance_km, 2.4) * 1000)
            if not zones_containing_point(tgt[0], tgt[1], risky):
                break
        a.mode, a.tag, a.target, a.goto_kmh = "goto", "off_route", tgt, CAB_KMH
        a.after, a.dwell_left = "dwell", 10 * 60
        self._log("event", f"{a.name} is leaving the planned route (~{distance_km:.1f} km away).",
                  a.tourist_id, "warn")

    def _ev_inactivity(self, a: Agent, minutes: float) -> None:
        a.mode, a.tag = "silent", "inactive"
        a.resume_at = self.elapsed + minutes * 60
        a.speed_kmh = 0.0
        self._log("event", f"{a.name} stopped moving and sending GPS (will report again in "
                           f"~{minutes:.0f} sim-min).", a.tourist_id, "warn")

    def _ev_abduction(self, a: Agent) -> None:
        a.pos = _bearing_move(a.pos[0], a.pos[1], self._rng.uniform(110, 170), 4600)
        a.trail.append(a.pos)
        # Report the jump immediately, linger briefly, then head back (see _finish_dwell).
        a.mode, a.tag, a.force_ping, a.force_kmh = "dwell", "anomaly", True, 165.0
        a.dwell_left, a.after = 300.0, "return"
        self._log("event", f"{a.name} jumped ~4.6 km at 165 km/h (abduction pattern).",
                  a.tourist_id, "critical")

    def _ev_sos(self, a: Agent, message: str) -> None:
        from app.services.monitoring import trigger_sos

        a.mode, a.tag = "sos", "sos"
        db = self._sf()
        try:
            t = db.get(Tourist, a.tourist_id)
            res = trigger_sos(db, t, a.pos[0], a.pos[1], message)
            unit = (res or {}).get("nearest_unit") or {}
            self._log("sos", f"SOS from {a.name}: dispatched {unit.get('name', 'n/a')} "
                             f"({unit.get('distance_km', '?')} km). Incident #{(res or {}).get('incident_id')}.",
                      a.tourist_id, "critical")
        finally:
            db.close()

    # ------------------------------------------------------------------ scenario
    def start_scenario(self, name: str = "full_demo") -> dict:
        if name != "full_demo":
            raise ValueError("Unknown scenario")
        if not self.agents:
            raise KeyError("Spawn simulated tourists first")
        free = [a.tourist_id for a in self.agents.values() if a.mode == "route" and a.tag is None]
        if len(free) < 5:
            raise KeyError("Need at least 5 free simulated tourists for the scripted scenario")
        picks = self._rng.sample(free, 5)
        t0 = self.elapsed
        steps = [(120, "off_route"), (300, "danger_zone"), (480, "inactivity"),
                 (660, "abduction"), (780, "sos")]
        self.scenario = [{"at": t0 + s, "event": ev, "tourist_id": tid, "offset": s}
                         for (s, ev), tid in zip(steps, picks, strict=True)]
        self.scenario_name = name
        self._log("system", "Scenario 'full_demo' armed: off-route -> danger zone -> inactivity -> "
                            "abduction -> SOS over ~13 simulated minutes.")
        return {"steps": [{"offset_s": s["offset"], "event": s["event"], "tourist_id": s["tourist_id"]}
                          for s in self.scenario]}

    # ------------------------------------------------------------------ physics
    def _advance(self, a: Agent, dt: float) -> None:
        """Move one agent by `dt` simulated seconds (pure in-memory)."""
        if a.mode in ("sos",):
            a.speed_kmh = 0.0
            return
        if a.mode == "silent":
            a.speed_kmh = 0.0
            if self.elapsed >= a.resume_at:
                a.force_ping, a.force_kmh = True, 0.0   # first report after a long silence
                a.mode, a.tag = "dwell", "inactive"
                a.dwell_left = 120
                a.after = "return"
            return
        if a.mode == "dwell":
            a.speed_kmh = 0.0
            a.dwell_left -= dt
            if a.dwell_left <= 0:
                a.tag = None if a.tag in ("inactive", "anomaly") else a.tag
                self._finish_dwell(a)
            return

        if a.mode == "goto" and a.target:
            kmh, target = a.goto_kmh, a.target
        else:
            target = a.route[a.seg % len(a.route)]
            kmh = a.walk_kmh
        a.speed_kmh = kmh
        step = kmh / 3.6 * dt
        while step > 0:
            d = haversine_m(a.pos[0], a.pos[1], *target)
            if d <= step:
                a.pos = target
                step -= d
                if a.mode == "goto":
                    self._arrived(a)
                    return
                a.seg = (a.seg + 1) % len(a.route)
                target = a.route[a.seg]
            else:
                f = step / d
                a.pos = (a.pos[0] + (target[0] - a.pos[0]) * f, a.pos[1] + (target[1] - a.pos[1]) * f)
                step = 0
        if not a.trail or haversine_m(a.pos[0], a.pos[1], *a.trail[-1]) > 25:
            a.trail.append(a.pos)

    def _arrived(self, a: Agent) -> None:
        a.force_ping = True            # report immediately on arrival
        a.force_kmh = a.goto_kmh
        a.trail.append(a.pos)
        if a.after == "dwell":
            a.mode = "dwell"
        else:
            a.mode, a.tag, a.target = "route", None, None
            self._head_back_if_needed(a)

    def _head_back_if_needed(self, a: Agent) -> None:
        a.seg = min(range(len(a.route)), key=lambda k: haversine_m(a.pos[0], a.pos[1], *a.route[k]))

    def _finish_dwell(self, a: Agent) -> None:
        was = a.tag
        a.tag, a.target = None, None
        a.mode = "route"
        self._head_back(a)
        if was:
            self._log("event", f"{a.name} returns to the planned route.", a.tourist_id)

    # ------------------------------------------------------------------ pings (DB)
    def _ping_interval(self) -> float:
        return min(300.0, max(120.0, self.speed * 5.0))

    def _process_pings(self, batch: list[tuple[int, float, float, float, datetime]]) -> list[tuple]:
        from app.services.monitoring import process_ping

        results = []
        db = self._sf()
        try:
            for tid, lat, lng, kmh, ts in batch:
                t = db.get(Tourist, tid)
                if t is None:
                    continue
                try:
                    r = process_ping(db, t, lat, lng, kmh, now=ts)
                    results.append((tid, r, None))
                except Exception as e:  # noqa: BLE001 -- one bad ping must not stop the clock
                    db.rollback()
                    logger.warning("sim_ping_failed", tourist_id=tid, error=str(e))
                    results.append((tid, None, str(e)))
        finally:
            db.close()
        return results

    # ------------------------------------------------------------------ main loop
    async def _run(self) -> None:
        interval = TICK_REAL_S
        try:
            while True:
                t0 = time.monotonic()
                if self.running:
                    dt = self.speed * interval
                    self.elapsed += dt
                    self._run_scenario()
                    due: list[Agent] = []
                    for a in self.agents.values():
                        self._advance(a, dt)
                        if a.mode == "silent" or a.mode == "sos":
                            continue
                        if a.force_ping or self.elapsed >= a.next_ping_at:
                            due.append(a)
                    due.sort(key=lambda x: (not x.force_ping, x.next_ping_at))
                    due = due[:MAX_PINGS_PER_TICK]
                    if due:
                        ts = self.sim_now
                        batch = [(a.tourist_id, a.pos[0], a.pos[1],
                                  a.force_kmh if a.force_kmh is not None else a.speed_kmh, ts) for a in due]
                        results = await asyncio.to_thread(self._process_pings, batch)
                        for (tid, r, err), a in zip(results, due, strict=False):
                            a.force_ping, a.force_kmh = False, None
                            a.last_ping_at = self.elapsed
                            a.next_ping_at = self.elapsed + self._ping_interval() * self._rng.uniform(0.9, 1.1)
                            if r:
                                a.score, a.flags = r["safety_score"], r["alerts_raised"]
                                a.anomaly_score = r["anomaly"]["score"]
                                if r["alerts_raised"]:
                                    self._log("detect", f"{a.name}: {', '.join(r['alerts_raised'])} "
                                                        f"(score {r['safety_score']})", a.tourist_id, "warn")
                            else:
                                a.flags = ["ping_failed"]
                    await manager.broadcast(self.state_message())
                await asyncio.sleep(max(0.05, interval - (time.monotonic() - t0)))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("simulation_loop_crashed")
            self.running = False

    def _run_scenario(self) -> None:
        while self.scenario and self.scenario[0]["at"] <= self.elapsed:
            step = self.scenario.pop(0)
            try:
                self._log("scenario", f"Scenario step: {step['event']}.", step["tourist_id"])
                self.trigger(step["event"], step["tourist_id"])
            except Exception as e:  # noqa: BLE001
                self._log("scenario", f"Scenario step '{step['event']}' skipped: {e}", level="warn")
        if not self.scenario and self.scenario_name:
            self._log("scenario", "Scenario complete.")
            self.scenario_name = None

    # ------------------------------------------------------------------ snapshots
    def counts(self) -> dict:
        c = {"total": len(self.agents), "normal": 0, "off_route": 0, "danger_zone": 0,
             "inactive": 0, "anomaly": 0, "sos": 0}
        for a in self.agents.values():
            c[a.status] = c.get(a.status, 0) + 1
        c["active"] = c["total"] - c["inactive"]
        return c

    def state_message(self) -> dict:
        return {
            "event": "sim_state", "running": self.running, "speed": self.speed,
            "sim_time": self.sim_now.isoformat(timespec="seconds"), "elapsed_s": round(self.elapsed),
            "counts": self.counts(), "scenario": self.scenario_name,
            "scenario_pending": len(self.scenario),
            "agents": [a.snapshot() for a in self.agents.values()],
        }

    def full_state(self) -> dict:
        s = self.state_message()
        s.pop("event")
        s["speeds"] = list(SPEEDS)
        s["events"] = EVENTS
        s["log"] = list(self.log)[-150:]
        s["model_ready"] = ml_service._load("anomaly_isoforest.joblib") is not None
        return s


engine = SimulationEngine()
