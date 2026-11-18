"""Saved routes: persist a route the tourist wants to keep, list them, mark one
opened, delete one. The tourist's own routes only (self or admin)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_self_or_admin
from app.core.time import utc_now
from app.db.session import get_db
from app.models.saved_route import SavedRoute
from app.models.tourist import Tourist
from app.models.user import User
from app.schemas.saved_route import SavedRouteCreate, SavedRouteOut

router = APIRouter(prefix="/tourists/{tourist_id}/saved-routes", tags=["saved-routes"])

MAX_SAVED_ROUTES = 100


def _out(r: SavedRoute) -> dict:
    return {
        "id": r.id, "name": r.name, "mode": r.mode,
        "origin": {"name": r.origin_name, "lat": r.origin_lat, "lng": r.origin_lng},
        "destination": {"name": r.dest_name, "lat": r.dest_lat, "lng": r.dest_lng},
        "stops": json.loads(r.stops_json or "[]"),
        "distance_km": r.distance_km, "duration_min": r.duration_min,
        "geometry": json.loads(r.geometry_json) if r.geometry_json else None,
        "risk_level": r.risk_level, "created_at": r.created_at, "last_opened_at": r.last_opened_at,
    }


def _own_or_404(tourist_id: int, route_id: int, db: Session) -> SavedRoute:
    r = db.get(SavedRoute, route_id)
    if r is None or r.tourist_id != tourist_id:
        raise HTTPException(status_code=404, detail="Saved route not found")
    return r


@router.post("", response_model=SavedRouteOut, status_code=201)
def save_route(tourist_id: int, payload: SavedRouteCreate, db: Session = Depends(get_db),
               _: User = Depends(require_self_or_admin)):
    if not db.get(Tourist, tourist_id):
        raise HTTPException(status_code=404, detail="Tourist not found")
    if db.query(SavedRoute).filter(SavedRoute.tourist_id == tourist_id).count() >= MAX_SAVED_ROUTES:
        raise HTTPException(status_code=409, detail="Too many saved routes -- delete one first.")
    route = SavedRoute(
        tourist_id=tourist_id, name=payload.name.strip(), mode=payload.mode,
        origin_name=payload.origin.name, origin_lat=payload.origin.lat, origin_lng=payload.origin.lng,
        dest_name=payload.destination.name, dest_lat=payload.destination.lat, dest_lng=payload.destination.lng,
        stops_json=json.dumps([s.model_dump() for s in payload.stops]),
        distance_km=payload.distance_km, duration_min=payload.duration_min,
        geometry_json=json.dumps(payload.geometry) if payload.geometry else "",
        risk_level=payload.risk_level,
    )
    try:
        db.add(route)
        db.commit()
    except SQLAlchemyError as e:  # surfaced as a clear 500 instead of a dead button
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not save the route. Please try again.") from e
    db.refresh(route)
    return _out(route)


@router.get("", response_model=list[SavedRouteOut])
def list_saved_routes(tourist_id: int, db: Session = Depends(get_db),
                      _: User = Depends(require_self_or_admin)):
    rows = (db.query(SavedRoute).filter(SavedRoute.tourist_id == tourist_id)
            .order_by(SavedRoute.created_at.desc()).all())
    return [_out(r) for r in rows]


@router.post("/{route_id}/open", response_model=SavedRouteOut)
def mark_opened(tourist_id: int, route_id: int, db: Session = Depends(get_db),
                _: User = Depends(require_self_or_admin)):
    route = _own_or_404(tourist_id, route_id, db)
    route.last_opened_at = utc_now()
    db.commit()
    db.refresh(route)
    return _out(route)


@router.delete("/{route_id}", status_code=204)
def delete_saved_route(tourist_id: int, route_id: int, db: Session = Depends(get_db),
                       _: User = Depends(require_self_or_admin)):
    db.delete(_own_or_404(tourist_id, route_id, db))
    db.commit()
