"""Request/response models for fall & crash detection."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventType = Literal["fall", "crash"]


class FallMetadata(BaseModel):
    """The only measurements the server keeps about a detection.

    Deliberately a tiny allow-list of derived numbers (no raw accelerometer or
    gyroscope stream, no device identifiers): it is enough to audit *why* the
    detector fired without collecting motion data we have no use for. Unknown
    keys are rejected rather than silently stored.
    """
    model_config = ConfigDict(extra="forbid")

    peak_g: float | None = Field(None, ge=0, le=500)
    freefall_ms: float | None = Field(None, ge=0, le=60_000)
    stillness_ms: float | None = Field(None, ge=0, le=600_000)
    speed_kmh: float | None = Field(None, ge=0, le=1200)
    sample_rate_hz: float | None = Field(None, ge=0, le=1000)
    detector: str | None = Field(None, max_length=40)


class _Located(BaseModel):
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0, le=100_000)

    @model_validator(mode="after")
    def _both_or_neither(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("lat and lng must be provided together (or both omitted)")
        return self


class FallEventCreate(_Located):
    """A phone (or simulation) reporting a possible fall/crash."""
    event_type: EventType
    source: Literal["phone_sensor", "simulation"] = "phone_sensor"
    # Client-generated unique id (e.g. a UUID). Makes retries idempotent.
    client_event_id: str = Field(..., min_length=8, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")
    # How long ago the device detected it, for a request that was delayed (offline
    # retry). Relative, so a wrong phone clock cannot shorten the countdown; the
    # server clamps it to FALL_MAX_EVENT_AGE_SECONDS. Omit for "just now".
    seconds_since_detection: float | None = Field(None, ge=0, le=86_400)
    metadata: FallMetadata = Field(default_factory=FallMetadata)


class DeviceFallEventCreate(_Located):
    """A wearable (authenticated by its device key) reporting a fall/crash."""
    event_type: EventType
    client_event_id: str | None = Field(None, min_length=8, max_length=64,
                                        pattern=r"^[A-Za-z0-9._:-]+$")
    seconds_since_detection: float | None = Field(None, ge=0, le=86_400)
    metadata: FallMetadata = Field(default_factory=FallMetadata)


class FallEventOut(BaseModel):
    id: int
    tourist_id: int
    event_type: str
    source: str
    simulated: bool
    device_id: str | None = None
    status: str
    detected_at: datetime
    countdown_deadline: datetime
    resolved_at: datetime | None = None
    resolution: str | None = None
    lat: float | None = None
    lng: float | None = None
    has_location: bool
    incident_id: int | None = None
    metadata: dict = Field(default_factory=dict)
    # Server clock at response time + seconds left, so the client can run its
    # countdown against the server's deadline instead of trusting its own clock.
    server_time: datetime
    seconds_remaining: float
    # True when this request was folded into an existing event (idempotent
    # retry, or a detection inside the dedupe window) rather than creating one.
    deduplicated: bool = False


class FallDetectionConfigOut(BaseModel):
    enabled: bool
    simulation_enabled: bool
    countdown_seconds: int
    cooldown_seconds: int
    history_limit: int
    thresholds: dict[str, float]


class FallSosRequest(_Located):
    message: str = Field("Fall/crash detected - immediate SOS requested", max_length=500)
