"""Emergency Medical Profile: the tourist-entered medical facts a responder
may see after scanning the tourist's QR.

Every free-text/clinical column is encrypted at rest with the same
`EncryptedString` used for KYC document numbers (app/core/crypto.py), so a
leaked database file does not expose medical data. Lists (allergies,
conditions, medications, contacts) are stored as JSON text inside an
encrypted column. Nothing here is "verified" -- it is tourist-reported.

The QR token itself is NOT a new table: it is the existing
`TouristIdToken` (models/tourist_id.py); see services/medical_profile.py.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.time import utc_now
from app.db.session import Base


class MedicalProfile(Base):
    __tablename__ = "medical_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # One profile per tourist, enforced by the database.
    tourist_id: Mapped[int] = mapped_column(
        ForeignKey("tourists.id", ondelete="CASCADE"), unique=True, index=True, nullable=False,
    )
    # NULL = not provided. "Unknown" is an explicit value, distinct from NULL.
    blood_group: Mapped[str | None] = mapped_column(String, nullable=True)
    # not_provided | none_known | listed -- "no known allergies" is a positive
    # statement by the tourist and must never be confused with "not filled in".
    allergies_status: Mapped[str] = mapped_column(String, default="not_provided", nullable=False)
    conditions_status: Mapped[str] = mapped_column(String, default="not_provided", nullable=False)
    medications_status: Mapped[str] = mapped_column(String, default="not_provided", nullable=False)
    allergies: Mapped[str] = mapped_column(EncryptedString, default="[]", nullable=False)
    medical_conditions: Mapped[str] = mapped_column(EncryptedString, default="[]", nullable=False)
    medications: Mapped[str] = mapped_column(EncryptedString, default="[]", nullable=False)
    emergency_contacts: Mapped[str] = mapped_column(EncryptedString, default="[]", nullable=False)
    additional_notes: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    profile_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
