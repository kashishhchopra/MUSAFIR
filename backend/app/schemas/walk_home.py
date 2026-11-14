"""Request models for Walk-Me-Home. Responses are plain dicts built by
services/walk_home.py (they differ for the tourist and for a guardian)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Located(BaseModel):
    """lat/lng must come together and be real coordinates."""

    @model_validator(mode="after")
    def _both_or_neither(self):
        for a, b in (("lat", "lng"), ("dest_lat", "dest_lng"), ("start_lat", "start_lng")):
            if hasattr(self, a) and (getattr(self, a) is None) != (getattr(self, b) is None):
                raise ValueError(f"{a} and {b} must be given together")
        return self


class WalkHomeCreate(_Located):
    model_config = ConfigDict(extra="forbid")

    destination_name: str = Field(..., min_length=1, max_length=120)
    dest_lat: float | None = Field(None, ge=-90, le=90)
    dest_lng: float | None = Field(None, ge=-180, le=180)
    # Where the tourist is now, if the browser gave a fix (never required).
    start_lat: float | None = Field(None, ge=-90, le=90)
    start_lng: float | None = Field(None, ge=-180, le=180)
    # Bounds are enforced against server settings in the service (they are
    # configurable), so only sanity limits live here.
    duration_minutes: int = Field(..., ge=1, le=10_000)
    check_interval_minutes: int | None = Field(None, ge=1, le=10_000)
    grace_minutes: int | None = Field(None, ge=1, le=10_000)
    guardian_ids: list[int] = Field(default_factory=list, max_length=50)

    @field_validator("destination_name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("destination_name must not be blank")
        return v

    @field_validator("guardian_ids")
    @classmethod
    def _unique(cls, v: list[int]) -> list[int]:
        return list(dict.fromkeys(v))


class WalkHomeLocation(_Located):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(..., ge=-90, le=90)
    lng: float = Field(..., ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0, le=100_000)
    # How old the fix already is on the phone (relative, so a wrong phone clock
    # cannot back- or forward-date it; same approach as fall detection).
    fix_age_seconds: float = Field(0, ge=0, le=86_400)


class WalkHomeExtend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minutes: int = Field(..., ge=1, le=10_000)
