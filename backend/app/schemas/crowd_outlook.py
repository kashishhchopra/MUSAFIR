"""Schemas for the crowd outlook (app/api/crowd_forecast.py)."""
from pydantic import BaseModel, Field


class OutlookHour(BaseModel):
    hour: str
    day_offset: int = 0
    score: float
    band: str                       # quiet | moderate | busy | very_busy
    rain_chance: float | None = None
    temp_c: float | None = None


class OutlookFactor(BaseModel):
    effect: str                     # up | down | neutral
    text: str


class OutlookArea(BaseModel):
    counts: dict[str, int]
    level: str
    label: str


class OutlookInputs(BaseModel):
    weather: bool
    area: bool
    holidays: bool
    season: bool
    area_pending: bool = False   # the surroundings lookup was still running: ask again shortly


class CrowdOutlook(BaseModel):
    lat: float
    lng: float
    method: str = "estimate"
    note: str
    local_time: str
    now: OutlookHour
    hours: list[OutlookHour] = Field(default_factory=list)
    best_time: dict | None = None
    area: OutlookArea | None = None
    factors: list[OutlookFactor] = Field(default_factory=list)
    inputs: OutlookInputs
    region: dict | None = None
