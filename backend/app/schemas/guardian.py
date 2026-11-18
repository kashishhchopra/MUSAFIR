import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def clean_contact(v: str | None) -> str:
    """A guardian contact is optional; when given it must be a real phone number
    (7-15 digits, optional leading +, spaces/dashes/brackets allowed) or an email.
    Phones are stored normalised ("+91 98765-43210" -> "+919876543210") so the
    same person cannot be added twice in different spellings."""
    v = (v or "").strip()
    if not v:
        return ""
    if "@" in v:
        if not _EMAIL.match(v):
            raise ValueError("Enter a valid email address or phone number")
        return v.lower()
    if not re.fullmatch(r"\+?[\d\s\-().]{7,25}", v):
        raise ValueError("Enter a valid phone number (7-15 digits, e.g. +919876543210)")
    digits = re.sub(r"\D", "", v)
    if not 7 <= len(digits) <= 15:
        raise ValueError("Enter a valid phone number (7-15 digits, e.g. +919876543210)")
    return ("+" if v.startswith("+") else "") + digits


def clean_name(v: str) -> str:
    v = " ".join((v or "").split())
    if not v:
        raise ValueError("Name must not be blank")
    return v


class TripGuardianCreate(BaseModel):
    guardian_name: str = Field(..., min_length=1, max_length=100)
    guardian_contact: str = Field("", max_length=100)

    _name = field_validator("guardian_name")(lambda cls, v: clean_name(v))
    _contact = field_validator("guardian_contact")(lambda cls, v: clean_contact(v))


class TripGuardianUpdate(BaseModel):
    guardian_name: str | None = Field(None, min_length=1, max_length=100)
    guardian_contact: str | None = Field(None, max_length=100)

    @field_validator("guardian_name")
    @classmethod
    def _n(cls, v):
        return None if v is None else clean_name(v)

    @field_validator("guardian_contact")
    @classmethod
    def _c(cls, v):
        return None if v is None else clean_contact(v)


class TripGuardianOut(BaseModel):
    id: int
    token: str
    guardian_name: str
    guardian_contact: str
    revoked: bool
    created_at: datetime

    class Config:
        from_attributes = True
