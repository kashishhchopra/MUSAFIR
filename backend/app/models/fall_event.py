"""Fall / crash detections and their cancellation countdown.

A FallEvent is NOT an incident. It is the short-lived "Are you OK?" record
that sits in front of the existing incident pipeline: it is created when a
phone sensor, a wearable (or a clearly-labelled simulation) reports a possible
fall/crash, and it ends in exactly one of:

  cancelled      the tourist said they are safe -- no incident is ever created
  confirmed      the countdown ran out -- ONE critical Incident was opened via
                 services/monitoring.py (the same path the rest of the system
                 uses), linked here through `incident_id`
  sos_triggered  the tourist asked for help immediately -- the existing SOS
                 workflow (monitoring.trigger_sos) ran, linked the same way

The transition out of `pending` is a single conditional UPDATE
(services/fall_detection.py), which is what makes "cancel vs. auto-escalate"
race-free: whichever statement reaches the row first wins and the other sees
status != 'pending' and does nothing.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.session import Base

# Lifecycle values (kept as plain strings, like Incident.status).
PENDING = "pending"
CANCELLED = "cancelled"
CONFIRMED = "confirmed"
SOS_TRIGGERED = "sos_triggered"

EVENT_TYPES = ("fall", "crash")
SOURCES = ("phone_sensor", "wearable", "simulation")


class FallEvent(Base):
    __tablename__ = "fall_events"
    __table_args__ = (
        # Idempotency: a retried request carrying the same client_event_id
        # resolves to the same row instead of creating a second one.
        UniqueConstraint("tourist_id", "client_event_id", name="uq_fall_events_tourist_client_id"),
        # At most ONE pending countdown per tourist, enforced by the database
        # so two simultaneous detections cannot both start one.
        Index(
            "uq_fall_events_one_pending", "tourist_id", unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tourist_id: Mapped[int] = mapped_column(ForeignKey("tourists.id"), index=True)
    client_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # fall | crash
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # phone_sensor | wearable | simulation
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # Wearable only: the Device.device_id that reported it.
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=PENDING, index=True)

    detected_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    countdown_deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # How it ended, e.g. "tourist_cancelled", "countdown_expired:client",
    # "countdown_expired:sweep", "immediate_sos". Audit/debug aid.
    resolution: Mapped[str | None] = mapped_column(String(48), nullable=True)

    # Location may legitimately be missing (no GPS fix / permission denied).
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Small JSON object of detector measurements (peak g, speed ...). Never raw
    # sensor streams -- see schemas/fall_detection.py:FallMetadata.
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)

    @property
    def simulated(self) -> bool:
        return self.source == "simulation"
