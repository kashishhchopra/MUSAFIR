"""Incident lifecycle records and their event timeline."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.time import utc_now
from app.db.session import Base


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tourist_id: Mapped[int | None] = mapped_column(ForeignKey("tourists.id"), index=True)
    # type: sos / anomaly / geofence / missing_person
    type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, default="medium")
    # lifecycle: detected -> acknowledged -> dispatched -> resolved
    status: Mapped[str] = mapped_column(String, default="detected")
    description: Mapped[str] = mapped_column(Text, default="")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    assigned_unit_id: Mapped[int | None] = mapped_column(
        ForeignKey("police_units.id"), nullable=True
    )
    # The station currently responsible for this case in the area-based
    # police network -- set from the tourist's zone at open time, and moved
    # by services/police_network.py:forward_incident() as the case travels
    # between stations. See services/police_network.py.
    station_id: Mapped[int | None] = mapped_column(
        ForeignKey("police_stations.id"), nullable=True
    )
    # escalation_stage: control_room -> emergency_contact -> responder_dispatch
    # -> acknowledged. Advanced by app/services/escalation.py:tick_escalations()
    # whenever `escalation_deadline` passes without a human acknowledgement.
    escalation_stage: Mapped[str] = mapped_column(String, default="control_room")
    escalation_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Real sentiment/distress analysis of `description` (services/sentiment.py)
    # -- surfaced to the responder alongside the text itself, never in place
    # of it. Null until analyzed (older rows, or a description-less incident).
    sentiment_label: Mapped[str | None] = mapped_column(String, nullable=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    distress_detected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Silent/Duress SOS: raised without any visible confirmation on the
    # tourist's screen (shake gesture, rapid trigger, or duress PIN). The
    # control room still sees it exactly like any other SOS -- this only
    # flags *how* it was raised, for the responder's situational awareness.
    silent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # SOS live location sharing (see services/emergency_location.py): True
    # while the tourist's device is expected to keep posting position
    # updates for this incident. Distinct from `status` on purpose -- a
    # tourist can stop sharing their live position ("I'm safe now") without
    # that being the same thing as police formally closing the case, which
    # only an operator/responder may do (see update_incident's RBAC).
    # Resolving the incident always also turns this off.
    live_tracking_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")

    detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ---- Intelligent response management (services/severity_classifier.py,
    # escalation.py, response_metrics.py) -----------------------------------
    # Every column is nullable / defaulted, so incidents created before this
    # feature (and by seed/import scripts) are untouched and simply have no
    # response target. `severity` above stays THE severity; these explain it.
    #
    # classification: why `severity` is what it is (full rule trace in
    # severity_classifications).
    baseline_severity: Mapped[str | None] = mapped_column(String, nullable=True)
    classification_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    classified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Evidence was uncertain or conflicting: a human should look. Never used to
    # silently downgrade.
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # response target: how fast a responder should ACKNOWLEDGE. The deadline is
    # stored (server-authoritative); `acknowledged_at` above is the other end.
    response_target_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # acknowledged_at - detected_at, seconds; slo_met = acknowledged within deadline.
    acknowledgment_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    slo_met: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # staged escalation (responder authority chain). 1 primary, 2 supervisor,
    # 3 higher authority. Distinct from the legacy `escalation_stage` string
    # above (tourist-side outreach), which is unchanged.
    response_stage: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    response_stage_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    escalation_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Fixed upper bound for the whole lifecycle; never moved once set.
    global_timeout_deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    global_timeout_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # none | active | acknowledged | exhausted (global timeout hit) | closed (resolved)
    escalation_status: Mapped[str] = mapped_column(String, default="none", server_default="none", index=True)

    events: Mapped[list["IncidentEvent"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    @property
    def response_time_seconds(self) -> float | None:
        if self.resolved_at:
            return (self.resolved_at - self.detected_at).total_seconds()
        return None


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now
    )

    incident: Mapped["Incident"] = relationship(back_populates="events")
