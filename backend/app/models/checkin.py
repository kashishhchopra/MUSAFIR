"""Tourist Check-in / Check-out: a planned outing the tourist registers ahead
of time (destination + expected return). A miss is treated as a soft distress
signal -- see services/checkin.py:tick_checkins(), which mirrors the
escalation-tick pattern in services/escalation.py.
"""
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.session import Base


class CheckIn(Base):
    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tourist_id: Mapped[int] = mapped_column(ForeignKey("tourists.id"), index=True)
    destination_name: Mapped[str] = mapped_column(String, nullable=False)
    dest_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    dest_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_return_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # planned -> checked_in (on time) | planned -> missed -> escalated (no
    # check-in by the deadline, then a grace period also elapses)
    status: Mapped[str] = mapped_column(String, default="planned")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    # ---- Walk-Me-Home (services/walk_home.py) -------------------------------
    # A monitored journey is a CheckIn with `kind == "walk_home"`: the very same
    # row, status machine and `tick_checkins` escalation as a planned outing, so
    # a missed check-in -> alert -> incident runs through ONE engine. For a walk
    # `expected_return_at` is the CURRENT check-in deadline (moved forward by each
    # "I am safe"); the columns below add what a live journey needs. All are
    # nullable / defaulted, so every existing outing row is untouched.
    # Extra statuses for a walk: completed | cancelled (both terminal).
    kind: Mapped[str] = mapped_column(String(16), default="outing", server_default="outing", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    arrive_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    check_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-session grace before a missed check-in escalates; NULL = the global
    # CHECKIN_GRACE_MINUTES (what every outing uses).
    grace_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_checkin_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checkin_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    missed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extensions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    start_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_loc_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # JSON list of TripGuardian ids this journey is shared with.
    guardian_ids_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    # The incident raised if the grace period ran out (NULL otherwise).
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True, index=True)

    __table_args__ = (
        # One live walk per tourist, enforced by the database (not just checked
        # in code) so two simultaneous "start" requests cannot both succeed.
        Index(
            "uq_checkins_one_active_walk", "tourist_id", unique=True,
            sqlite_where=text("kind = 'walk_home' AND status IN ('planned','missed','escalated')"),
            postgresql_where=text("kind = 'walk_home' AND status IN ('planned','missed','escalated')"),
        ),
    )
