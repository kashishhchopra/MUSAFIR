"""DPDP-oriented privacy management tables (services/privacy_*.py).

Design notes
  * `ConsentRecord` is an append-only ledger: every grant is a new row, and a
    withdrawal stamps `withdrawn_at` on the row it ends. History is therefore
    never overwritten. The tourist link is nulled on erasure and `subject_ref`
    (an opaque HMAC) is kept, so the consent audit trail survives without
    keeping the person.
  * Rows that may need to outlive a tourist (deletion requests, jobs, holds,
    grievances) store a plain `subject_ref` and an unconstrained `tourist_id`
    instead of a foreign key, so erasing the tourist can never be blocked by --
    or cascade into -- the evidence that it happened.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utc_now
from app.db.session import Base


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (Index("ix_consent_subject_purpose", "subject_ref", "purpose"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consent_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    tourist_id: Mapped[int | None] = mapped_column(
        ForeignKey("tourists.id", ondelete="SET NULL"), index=True, nullable=True)
    subject_ref: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False, index=True)
    data_categories: Mapped[str] = mapped_column(Text, default="[]")   # JSON list
    status: Mapped[str] = mapped_column(String, default="granted")     # granted | withdrawn
    consent_version: Mapped[int] = mapped_column(Integer, default=1)   # per subject+purpose
    notice_version: Mapped[str] = mapped_column(String, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source: Mapped[str] = mapped_column(String, default="privacy_dashboard")
    # The affirmative action that produced it (what the person did), JSON.
    evidence: Mapped[str] = mapped_column(Text, default="{}")


class RetentionPolicy(Base):
    __tablename__ = "retention_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String, default="")
    purpose: Mapped[str] = mapped_column(Text, default="")
    legal_basis: Mapped[str] = mapped_column(Text, default="")
    # None = no period established (never auto-deleted; needs a decision).
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_condition: Mapped[str] = mapped_column(String, default="created")
    deletion_action: Mapped[str] = mapped_column(String, default="delete")        # delete|anonymize|retain
    account_deletion_action: Mapped[str] = mapped_column(String, default="delete")  # delete|anonymize|retain
    per_tourist: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String, default="system")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RetentionException(Base):
    """A documented reason a category (optionally one tourist) must not be
    deleted yet. Always has a review date; released holds stay as history."""

    __tablename__ = "retention_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String, index=True, nullable=False)
    subject_ref: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    tourist_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    review_by: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class DeletionJob(Base):
    """Minimal audit of one deletion run. Counts only -- never the data."""

    __tablename__ = "deletion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String, index=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String, default="scheduled")  # scheduled|manual|account_deletion|export_cleanup
    status: Mapped[str] = mapped_column(String, default="running")      # running|completed|failed
    subject_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_deleted: Mapped[int] = mapped_column(Integer, default=0)
    records_retained: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataExportRequest(Base):
    __tablename__ = "data_export_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    export_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    tourist_id: Mapped[int] = mapped_column(
        ForeignKey("tourists.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")   # pending|ready|failed|expired
    export_format: Mapped[str] = mapped_column(String, default="json")
    file_name: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class AccountDeletionRequest(Base):
    __tablename__ = "account_deletion_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    tourist_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_ref: Mapped[str] = mapped_column(String, index=True, nullable=False)
    # awaiting_confirmation | requested | in_progress | completed |
    # partially_retained | failed | cancelled
    status: Mapped[str] = mapped_column(String, default="awaiting_confirmation", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    process_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[str] = mapped_column(Text, default="[]")        # JSON [{step,status,count}]
    retained: Mapped[str] = mapped_column(Text, default="[]")     # JSON [{category,reason,basis}]
    processors: Mapped[str] = mapped_column(Text, default="[]")   # JSON [{name,status}]


class PrivacyGrievance(Base):
    __tablename__ = "privacy_grievances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grievance_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    tourist_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    subject_ref: Mapped[str] = mapped_column(String, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String, default="other")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open|in_review|resolved|rejected
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class GrievanceResponse(Base):
    __tablename__ = "privacy_grievance_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grievance_pk: Mapped[int] = mapped_column(
        ForeignKey("privacy_grievances.id", ondelete="CASCADE"), index=True, nullable=False)
    author_role: Mapped[str] = mapped_column(String, default="admin")  # tourist | admin
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status_after: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
