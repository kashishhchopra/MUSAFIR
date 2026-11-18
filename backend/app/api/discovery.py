"""Discovery API: real nearby hidden gems, regional food and homestays -- and an offline
bundle for low-connectivity caching. See services/discovery.py.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.discovery import DiscoveryResponse, OfflineBundle
from app.services import discovery

router = APIRouter(prefix="/discovery", tags=["discovery"])


def _unavailable(e: discovery.DiscoveryUnavailable) -> HTTPException:
    # Not "no places": the data sources could not be reached, and the app must say so.
    return HTTPException(status_code=503, detail="Place search is unavailable right now. "
                         "The free map services are busy or unreachable -- please try again in a minute.")


@router.get("", response_model=DiscoveryResponse)
def get_discovery(
    lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(10.0, gt=0, le=discovery.MAX_RADIUS_KM),
    categories: str | None = Query(None, description="Comma-separated: hidden_gem,regional_food,homestay"),
    refresh: bool = Query(False, description="Skip the cache and ask the map services again"),
    _: User = Depends(get_current_user),
):
    cat_list = [c.strip() for c in categories.split(",") if c.strip()] if categories else None
    try:
        return discovery.discover(lat, lng, radius_km, cat_list, refresh)
    except discovery.DiscoveryUnavailable as e:
        raise _unavailable(e) from e


@router.get("/offline-bundle", response_model=OfflineBundle)
def get_offline_bundle(
    lat: float = Query(..., ge=-90, le=90), lng: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(25.0, gt=0, le=discovery.MAX_RADIUS_KM),
    _: User = Depends(get_current_user),
):
    try:
        return discovery.offline_bundle(lat, lng, radius_km)
    except discovery.DiscoveryUnavailable as e:
        raise _unavailable(e) from e
