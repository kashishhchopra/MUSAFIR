"""Schemas for saved routes (app/api/saved_routes.py)."""
from datetime import datetime

from pydantic import BaseModel, Field


class RoutePoint(BaseModel):
    name: str = Field(default="", max_length=200)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class SavedRouteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mode: str = Field(default="driving", pattern="^(driving|walking|bicycling|transit)$")
    origin: RoutePoint
    destination: RoutePoint
    stops: list[RoutePoint] = Field(default_factory=list, max_length=13)
    distance_km: float | None = Field(default=None, ge=0)
    duration_min: float | None = Field(default=None, ge=0)
    geometry: list[list[float]] | None = Field(default=None, max_length=20000)
    risk_level: str = Field(default="", max_length=20)


class SavedRouteOut(BaseModel):
    id: int
    name: str
    mode: str
    origin: RoutePoint
    destination: RoutePoint
    stops: list[RoutePoint]
    distance_km: float | None
    duration_min: float | None
    geometry: list[list[float]] | None
    risk_level: str
    created_at: datetime
    last_opened_at: datetime | None
