"""Thin, authenticated read-only endpoint over services/maps.py -- lets the
frontend re-resolve a single place name on demand (e.g. the itinerary
review screen's "Locate" button for a destination the automatic parse
couldn't place), without duplicating the geocoding logic client-side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.tourist import Tourist
from app.models.user import User
from app.services import maps, place_search

router = APIRouter(prefix="/maps", tags=["maps"])


@router.get("/geocode")
def geocode_place(place: str, _: User = Depends(get_current_user)):
    """Resolve a place name to coordinates. Returns `{lat, lng, demo}` on a
    match, or `{lat: null, lng: null}` if the place can't be resolved --
    never a guessed location."""
    result = maps.geocode(place)
    if result is None:
        return {"lat": None, "lng": None, "demo": True}
    return result


@router.get("/search")
def search_places(
    q: str = Query(..., min_length=2, max_length=120),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    limit: int = Query(6, ge=1, le=10),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Several ranked matches for a typed place name, for the tourist to pick
    from -- Google Places when a key is configured, else OpenStreetMap.
    Ranked near `lat`/`lng`, or near the signed-in tourist's last known
    position when those are omitted."""
    if (lat is None or lng is None) and user.tourist_id:
        t = db.get(Tourist, user.tourist_id)
        if t is not None and t.last_lat is not None and t.last_lng is not None:
            lat, lng = t.last_lat, t.last_lng
    out = place_search.search(q, lat if lng is not None else None, lng if lat is not None else None, limit)
    out["near"] = {"lat": lat, "lng": lng} if lat is not None and lng is not None else None
    return out


@router.get("/suggest")
def suggest_places(q: str, limit: int = 6, lat: float | None = None, lng: float | None = None,
                   _: User = Depends(get_current_user)):
    """Type-ahead: live suggestions for a partial place name (debounced by the
    client). `lat`/`lng` (the user's position) only bias the ranking."""
    near = (lat, lng) if lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180 else None
    return {"results": maps.suggest_places(q, max(1, min(limit, 10)), near)}


@router.get("/reverse")
def reverse(lat: float = Query(ge=-90, le=90), lng: float = Query(ge=-180, le=180),
            _: User = Depends(get_current_user)):
    """The area/city/state/country a coordinate is in, or `{"place": null}` when
    OpenStreetMap cannot say (never a guess)."""
    return {"place": maps.reverse_geocode(lat, lng)}


@router.get("/modes")
def travel_modes(_: User = Depends(get_current_user)):
    """Travel modes that can return a real routed answer in this deployment
    (public transport only appears when a Google Maps key is configured)."""
    return {"modes": maps.available_modes()}


@router.get("/route")
def route(
    olat: float = Query(ge=-90, le=90), olng: float = Query(ge=-180, le=180),
    dlat: float = Query(ge=-90, le=90), dlng: float = Query(ge=-180, le=180),
    mode: str = "driving", _: User = Depends(get_current_user),
):
    """Real distance/ETA/geometry between two coordinates for one mode."""
    if mode not in maps.TRAVEL_MODES:
        raise HTTPException(status_code=422, detail=f"Unknown travel mode: {mode}")
    if mode not in maps.available_modes():
        raise HTTPException(status_code=422, detail=f"'{mode}' routing is not available here.")
    return maps.directions(olat, olng, dlat, dlng, mode)


class TripPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class TripRequest(BaseModel):
    points: list[TripPoint] = Field(min_length=2, max_length=15)
    mode: str = "driving"


@router.post("/trip")
def trip(payload: TripRequest, _: User = Depends(get_current_user)):
    """Leg-by-leg time/distance through an ordered list of stops, with totals."""
    if payload.mode not in maps.available_modes():
        raise HTTPException(status_code=422, detail=f"'{payload.mode}' routing is not available here.")
    return maps.trip_legs([(p.lat, p.lng) for p in payload.points], payload.mode)
