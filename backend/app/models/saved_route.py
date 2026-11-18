"""A route the tourist chose to keep: start, destination, any in-between stops,
the real distance/time/geometry from services/maps.py at the moment of saving,
and when. Persisted so it survives an app restart and can be re-opened on the
map or handed to Google Maps to resume navigation.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.session import Base


class SavedRoute(Base):
    __tablename__ = "saved_routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tourist_id: Mapped[int] = mapped_column(ForeignKey("tourists.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="driving")
    origin_name: Mapped[str] = mapped_column(String(200), default="")
    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lng: Mapped[float] = mapped_column(Float, nullable=False)
    dest_name: Mapped[str] = mapped_column(String(200), default="")
    dest_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lng: Mapped[float] = mapped_column(Float, nullable=False)
    # JSON list of in-between stops [{name, lat, lng}] (may be empty)
    stops_json: Mapped[str] = mapped_column(Text, default="[]")
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    # JSON [[lat, lng], ...] real route line, or "" when only an estimate existed
    geometry_json: Mapped[str] = mapped_column(Text, default="")
    # JSON risk summary of a safe-route recommendation, or ""
    risk_level: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
