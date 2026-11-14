"""Records behind intelligent response management.

* SeverityClassification -- one row per classification decision or change
  (automatic, re-evaluation, admin override): the audit trail for "why is this
  incident CRITICAL / HIGH / MEDIUM / LOW".
* EscalationEvent -- one row per workflow event (assigned, stage advanced,
  deadline missed, global timeout, acknowledged, severity changed). The unique
  (incident_id, dedupe_key) constraint is what makes escalation idempotent: a
  second worker or a replayed tick cannot record the same event twice.
* ResponseSetting -- admin overrides of the response targets / stage timing.
  Defaults live in app/core/config.py; a row here wins over them.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.session import Base


class SeverityClassification(Base):
    __tablename__ = "severity_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    incident_type: Mapped[str] = mapped_column(String, default="")
    severity: Mapped[str] = mapped_column(String, nullable=False)
    # severity before this decision (None for the first one)
    previous_severity: Mapped[str | None] = mapped_column(String, nullable=True)
    baseline_severity: Mapped[str] = mapped_column(String, nullable=False)
    # initial | reevaluation | override
    trigger: Mapped[str] = mapped_column(String, default="initial")
    rules_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text, default="")
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class EscalationEvent(Base):
    __tablename__ = "escalation_events"
    __table_args__ = (
        UniqueConstraint("incident_id", "dedupe_key", name="uq_escalation_events_dedupe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    # assigned | warning | deadline_missed | stage_advanced | global_timeout |
    # acknowledged | severity_changed | resolved
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    from_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class ResponseSetting(Base):
    __tablename__ = "response_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
