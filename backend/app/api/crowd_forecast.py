"""Crowd & Queue Forecast API. See services/crowd_forecast.py."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.crowd_forecast import QueueEstimate, ZoneCrowdForecast
from app.schemas.crowd_outlook import CrowdOutlook
from app.services import crowd_forecast, crowd_outlook

router = APIRouter(prefix="/crowd", tags=["crowd-forecast"])


@router.get("/forecast", response_model=list[ZoneCrowdForecast])
def get_crowd_forecast(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return crowd_forecast.forecast_zone_crowds(db)


@router.get("/forecast/poi/{poi_id}", response_model=QueueEstimate)
def get_poi_queue(poi_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    result = crowd_forecast.estimate_queue_wait(db, poi_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Point of interest not found")
    return result


@router.get("/outlook", response_model=CrowdOutlook)
def get_crowd_outlook(
    lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
    tz_offset_min: int | None = Query(None, ge=-840, le=840,
                                      description="Minutes the caller's clock is ahead of UTC; used only if the weather service cannot give the local time"),
    _: User = Depends(get_current_user),
):
    """Estimated crowd levels for the next hours around a real position -- see services/crowd_outlook.py.
    An estimate from real inputs, never a live count."""
    try:
        return crowd_outlook.outlook(lat, lng, tz_offset_min)
    except crowd_outlook.OutlookUnavailable as e:
        raise HTTPException(status_code=503, detail=f"Crowd outlook is unavailable right now: {e}.") from e
