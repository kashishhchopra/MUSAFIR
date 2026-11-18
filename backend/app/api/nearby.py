"""Nearby API: real hospitals, pharmacies, police and transport around a position.
See services/nearby.py. The position comes from the caller (the phone's GPS or a place the
tourist chose) -- never from a stored profile coordinate.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.nearby import NearbyResponse
from app.services import nearby

router = APIRouter(prefix="/nearby", tags=["nearby"])


@router.get("", response_model=NearbyResponse)
def get_nearby(
    lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
    category: str = Query(..., description="hospital | pharmacy | police | transport"),
    radius_km: float = Query(5.0, gt=0, le=nearby.MAX_RADIUS_KM),
    refresh: bool = Query(False, description="Skip the cache and ask the map services again"),
    tz_offset_min: int | None = Query(None, ge=-840, le=840,
                                      description="Minutes the user's clock is ahead of UTC; lets 'open now' be worked out"),
    db: Session = Depends(get_db), _: User = Depends(get_current_user),
):
    if category not in nearby.NEARBY_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be one of: " + ", ".join(nearby.NEARBY_CATEGORIES))
    try:
        return nearby.nearby(db, lat, lng, category, radius_km, refresh, tz_offset_min)
    except nearby.NearbyUnavailable as e:
        # Not "nothing nearby": the data sources could not be reached, and the app must say so.
        raise HTTPException(status_code=503, detail="Nearby search is unavailable right now. The free map services are "
                            "busy or unreachable -- please try again in a minute.") from e
