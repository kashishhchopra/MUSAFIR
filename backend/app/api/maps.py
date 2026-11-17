"""Thin, authenticated read-only endpoint over services/maps.py -- lets the
frontend re-resolve a single place name on demand (e.g. the itinerary
review screen's "Locate" button for a destination the automatic parse
couldn't place), without duplicating the geocoding logic client-side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
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
