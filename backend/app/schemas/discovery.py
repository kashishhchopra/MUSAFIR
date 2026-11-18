"""Schemas for the Discovery feature (app/api/discovery.py)."""
from pydantic import BaseModel, Field


class DiscoveryPlace(BaseModel):
    id: int | str
    name: str
    category: str
    kind: str = ""                      # "Viewpoint", "Guest house", "Bakery"... from the OSM tags
    lat: float
    lng: float
    distance_km: float
    address: str | None = None
    description: str | None = None      # only when OpenStreetMap carries one
    cuisine: str | None = None          # only when listed
    phone: str | None = None
    website: str | None = None
    opening_hours: str | None = None
    osm_url: str | None = None
    regional_match: str | None = None   # "region" | "country" | None (cuisine not matched / not listed)
    match_label: str | None = None
    tip: str = ""                       # kept for older clients; same as description
    source: str = "osm"


class DiscoveryRegion(BaseModel):
    name: str | None = None
    country_code: str | None = None
    cuisine_scope: str | None = None


class DiscoveryResponse(BaseModel):
    lat: float
    lng: float
    radius_km: float
    places: list[DiscoveryPlace] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    provider: str | None = None         # "overpass" | "nominatim"
    degraded: bool = False              # answered by the backup source: usually fewer places
    cached: bool = False
    stale: bool = False
    partial: bool = False               # some categories could not be loaded
    generated_at: str | None = None
    region: DiscoveryRegion | None = None


class OfflineBundle(BaseModel):
    generated_at: str
    lat: float
    lng: float
    radius_km: float
    places: list[DiscoveryPlace] = Field(default_factory=list)
