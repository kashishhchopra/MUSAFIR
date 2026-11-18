"""Schemas for the Nearby feature (app/api/nearby.py)."""
from pydantic import BaseModel, Field


class NearbyPlace(BaseModel):
    id: str
    name: str
    category: str
    kind: str                            # "Hospital", "Pharmacy", "Police station", "Metro station"...
    lat: float
    lng: float
    distance_km: float
    address: str | None = None
    phone: str | None = None             # first mapped number, exactly as in OpenStreetMap
    phones: list[str] = Field(default_factory=list)
    website: str | None = None
    opening_hours: str | None = None     # raw OSM tag
    open_now: bool | None = None         # None = unknown (never assumed)
    open_24h: bool = False
    operator_type: str | None = None     # "Government" | "Private" when mapped
    emergency: bool = False              # mapped as having an emergency department
    details: dict[str, str] = Field(default_factory=dict)  # transport: operator / network / lines
    rank: int = 0
    osm_url: str | None = None
    source: str = "osm"                  # "osm" | "saved_osm"


class NearbyRegion(BaseModel):
    name: str | None = None
    country_code: str | None = None


class NearbyResponse(BaseModel):
    lat: float
    lng: float
    radius_km: float
    category: str
    places: list[NearbyPlace] = Field(default_factory=list)
    count: int = 0
    provider: str | None = None          # "overpass" | "nominatim" | "saved"
    degraded: bool = False               # backup source or saved copy
    cached: bool = False
    stale: bool = False
    generated_at: str | None = None
    region: NearbyRegion | None = None
