"""Simulation Core API (admin-only). See services/simulation.py.

Virtual tourists + a controllable clock + scripted events that drive the real
monitoring pipeline, for demos and testing without real tourists.
"""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.alert import Alert
from app.models.tourist import LocationPing, Tourist
from app.models.user import User
from app.services.simulation import SIM_PREFIX, SPEEDS, engine


def _enabled() -> None:
    if not settings.SIMULATION_ENABLED:
        raise HTTPException(status_code=404, detail="Simulation is disabled (SIMULATION_ENABLED=false)")


router = APIRouter(prefix="/simulation", tags=["simulation"],
                   dependencies=[Depends(_enabled), Depends(require_admin)])


class SpawnRequest(BaseModel):
    count: int = Field(50, ge=1, le=100)


class SpeedRequest(BaseModel):
    speed: int


class ResetRequest(BaseModel):
    purge: bool = False


class EventRequest(BaseModel):
    event: str
    tourist_id: int | None = None
    zone_id: int | None = None
    params: dict = Field(default_factory=dict)


class ScenarioRequest(BaseModel):
    name: str = "full_demo"


@router.get("/state")
def get_state(user: User = Depends(require_admin)):
    return engine.full_state()


@router.post("/spawn")
async def spawn(payload: SpawnRequest):
    return await asyncio.to_thread(engine.spawn, payload.count)


@router.post("/play")
async def play():
    await engine.play()
    return {"running": engine.running, "speed": engine.speed}


@router.post("/pause")
def pause():
    engine.pause()
    return {"running": engine.running, "speed": engine.speed}


@router.post("/speed")
def set_speed(payload: SpeedRequest):
    try:
        engine.set_speed(payload.speed)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"speed must be one of {list(SPEEDS)}")
    return {"speed": engine.speed}


@router.post("/reset")
async def reset(payload: ResetRequest):
    return await asyncio.to_thread(engine.reset, payload.purge)


@router.post("/events")
async def trigger_event(payload: EventRequest):
    try:
        return await asyncio.to_thread(engine.trigger, payload.event, payload.tourist_id,
                                       payload.zone_id, payload.params)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e).strip("'\""))


@router.post("/scenario")
def start_scenario(payload: ScenarioRequest):
    try:
        return engine.start_scenario(payload.name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e).strip("'\""))


@router.get("/tourists/{tourist_id}")
def tourist_detail(tourist_id: int, db: Session = Depends(get_db)):
    agent = engine.agents.get(tourist_id)
    t = db.get(Tourist, tourist_id)
    if agent is None or t is None or not t.digital_id.startswith(SIM_PREFIX):
        raise HTTPException(status_code=404, detail="Not a simulated tourist")
    pings = (db.query(LocationPing).filter(LocationPing.tourist_id == tourist_id)
             .order_by(LocationPing.timestamp.desc()).limit(40).all())
    alerts = (db.query(Alert).filter(Alert.tourist_id == tourist_id)
              .order_by(Alert.created_at.desc()).limit(25).all())
    return {
        **agent.snapshot(with_route=True),
        "phone": t.phone, "document_type": t.document_type,
        "tourist_status": t.status,
        "itinerary_points": len(json.loads(t.itinerary or "[]")),
        "pings": [{"ts": p.timestamp.isoformat(timespec="seconds"), "lat": p.lat, "lng": p.lng,
                   "speed_kmh": round(p.speed_kmh, 1), "anomaly": bool(p.is_anomaly),
                   "anomaly_score": round(p.anomaly_score or 0, 3)} for p in pings],
        "alerts": [{"id": a.id, "type": a.type, "severity": a.severity, "message": a.message,
                    "created_at": a.created_at.isoformat(timespec="seconds")} for a in alerts],
    }


@router.get("/alerts")
def recent_alerts(limit: int = 60, db: Session = Depends(get_db)):
    ids = [i for (i,) in db.query(Tourist.id).filter(Tourist.digital_id.like(f"{SIM_PREFIX}%")).all()]
    if not ids:
        return []
    rows = (db.query(Alert).filter(Alert.tourist_id.in_(ids))
            .order_by(Alert.created_at.desc()).limit(min(limit, 200)).all())
    names = {t.id: t.full_name for t in db.query(Tourist).filter(Tourist.id.in_({r.tourist_id for r in rows})).all()}
    return [{"id": r.id, "tourist_id": r.tourist_id, "tourist": names.get(r.tourist_id), "type": r.type,
             "severity": r.severity, "message": r.message, "lat": r.lat, "lng": r.lng,
             "created_at": r.created_at.isoformat()} for r in rows]
