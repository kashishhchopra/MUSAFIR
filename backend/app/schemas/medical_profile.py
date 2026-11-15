"""Schemas for the Emergency Medical Profile (app/api/medical_profile.py).

All content is tourist-reported. Bounds (counts, note length) come from
settings so they are configuration, not literals scattered through the code.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings

BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "Unknown")
ALLERGY_CATEGORIES = ("food", "medicine", "other")
CLEARABLE_FIELDS = (
    "blood_group", "allergies", "medical_conditions", "medications",
    "emergency_contacts", "additional_notes",
)
# International-friendly: a foreign tourist's emergency contact is often abroad,
# so the Indian-mobile-only validator used at registration is too strict here.
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{4,18}[0-9]$")
MAX_CONTACTS = 2  # primary + optional secondary

ListStatus = Literal["not_provided", "none_known", "listed"]


def _clean(v):
    """Collapse whitespace; empty strings become None. Non-strings pass through."""
    if not isinstance(v, str):
        return v
    v = " ".join(v.split())
    return v or None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AllergyItem(_Strict):
    name: str = Field(..., min_length=1, max_length=80)
    category: Literal["food", "medicine", "other"] = "other"
    reaction: str | None = Field(None, max_length=120)

    @field_validator("name", "reaction", mode="before")
    @classmethod
    def _c(cls, v):
        return _clean(v)


class ConditionItem(_Strict):
    name: str = Field(..., min_length=1, max_length=80)
    description: str | None = Field(None, max_length=200)

    @field_validator("name", "description", mode="before")
    @classmethod
    def _c(cls, v):
        return _clean(v)


class MedicationItem(_Strict):
    name: str = Field(..., min_length=1, max_length=80)
    dosage: str | None = Field(None, max_length=60)  # as reported by the tourist
    frequency: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=200)

    @field_validator("name", "dosage", "frequency", "notes", mode="before")
    @classmethod
    def _c(cls, v):
        return _clean(v)


class ContactItem(_Strict):
    name: str = Field(..., min_length=1, max_length=80)
    relationship: str = Field(..., min_length=1, max_length=40)
    phone: str = Field(..., max_length=20)

    @field_validator("name", "relationship", "phone", mode="before")
    @classmethod
    def _c(cls, v):
        return _clean(v)

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        if not PHONE_RE.match(v):
            raise ValueError("Enter a valid phone number")
        return v


class MedicalProfileUpdate(_Strict):
    """Partial update: only the fields the client sends are changed, so a
    tourist can save a half-completed profile and fill the rest later."""
    blood_group: str | None = None
    allergies_status: ListStatus | None = None
    allergies: list[AllergyItem] | None = None
    conditions_status: ListStatus | None = None
    medical_conditions: list[ConditionItem] | None = None
    medications_status: ListStatus | None = None
    medications: list[MedicationItem] | None = None
    emergency_contacts: list[ContactItem] | None = None
    additional_notes: str | None = None

    @field_validator("blood_group")
    @classmethod
    def _bg(cls, v: str | None) -> str | None:
        if v is not None and v not in BLOOD_GROUPS:
            raise ValueError(f"blood_group must be one of {', '.join(BLOOD_GROUPS)}")
        return v

    @field_validator("additional_notes")
    @classmethod
    def _notes(cls, v: str | None) -> str | None:
        v = v.strip() if isinstance(v, str) else v
        if v and len(v) > settings.MEDICAL_NOTES_MAX_CHARS:
            raise ValueError(f"Notes are limited to {settings.MEDICAL_NOTES_MAX_CHARS} characters")
        return v or None

    @model_validator(mode="after")
    def _limits(self):
        for name, cap in (("allergies", settings.MEDICAL_MAX_ALLERGIES),
                          ("medical_conditions", settings.MEDICAL_MAX_CONDITIONS),
                          ("medications", settings.MEDICAL_MAX_MEDICATIONS),
                          ("emergency_contacts", MAX_CONTACTS)):
            v = getattr(self, name)
            if v is not None and len(v) > cap:
                raise ValueError(f"At most {cap} entries are allowed for {name}")
        return self


class MedicalAccessRequest(_Strict):
    """Responder scan: the raw QR payload (`TSID:<token>`) or a pasted token."""
    token: str = Field(..., min_length=1, max_length=200)
    purpose: str | None = Field(None, max_length=60)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)


def _json_list(raw: str) -> list[str]:
    try:
        v = json.loads(raw)
    except ValueError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def suggested_conditions() -> list[str]:
    return _json_list(settings.MEDICAL_SUGGESTED_CONDITIONS)


def access_purposes() -> list[str]:
    return _json_list(settings.MEDICAL_ACCESS_PURPOSES)


class MedicalAccessLogOut(BaseModel):
    id: int
    scanned_at: datetime
    responder_email: str | None
    responder_role: str
    tourist_digital_id: str | None
    result: str
    purpose: str | None
    request_id: str | None
