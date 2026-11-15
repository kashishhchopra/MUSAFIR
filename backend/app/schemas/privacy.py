"""Request bodies for the privacy API (services/privacy_*.py)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConsentChoice(_Strict):
    """One box the person explicitly ticked on a form. `granted` has no default:
    a client cannot pre-select consent by omission."""
    purpose: str = Field(..., max_length=64)
    granted: bool


class ConsentGrant(_Strict):
    purpose: str = Field(..., max_length=64)


class LocationDeleteRequest(_Strict):
    """Exactly one selector: ids, a date range, or all."""
    ids: list[int] | None = Field(None, max_length=5000)
    date_from: datetime | None = None
    date_to: datetime | None = None
    delete_all: bool = False

    @model_validator(mode="after")
    def _one_selector(self):
        picked = [bool(self.ids), self.date_from is not None or self.date_to is not None,
                  bool(self.delete_all)]
        if sum(picked) != 1:
            raise ValueError("Choose exactly one of: ids, a date range, or delete_all.")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before date_to")
        return self


class ExportRequestBody(_Strict):
    format: str = Field("json", max_length=8)
    password: str = Field(..., min_length=1, max_length=200)


class DeletionStartBody(_Strict):
    password: str = Field(..., min_length=1, max_length=200)


class DeletionConfirmBody(_Strict):
    password: str = Field(..., min_length=1, max_length=200)
    confirmation: str = Field(..., max_length=32)


class GrievanceCreate(_Strict):
    category: str = Field("other", max_length=40)
    description: str = Field(..., min_length=1, max_length=4000)


class GrievanceReply(_Strict):
    message: str = Field(..., min_length=1, max_length=4000)


class GrievanceRespond(_Strict):
    message: str = Field(..., min_length=1, max_length=4000)
    status: str | None = Field(None, max_length=20)


class PolicyUpdate(BaseModel):
    """Administrator edit of a retention policy. Omitted fields are untouched;
    `retention_days: null` means "no period established"."""
    model_config = ConfigDict(extra="forbid")
    retention_days: int | None = Field(None, ge=0, le=36500)
    is_active: bool | None = None
    requires_review: bool | None = None
    deletion_action: str | None = Field(None, max_length=12)
    account_deletion_action: str | None = Field(None, max_length=12)
    start_condition: str | None = Field(None, max_length=24)
    legal_basis: str | None = Field(None, max_length=1000)
    purpose: str | None = Field(None, max_length=1000)
    note: str | None = Field(None, max_length=2000)
    title: str | None = Field(None, max_length=120)


class ExceptionCreate(_Strict):
    category: str = Field(..., max_length=64)
    reason: str = Field(..., min_length=3, max_length=2000)
    basis: str = Field(..., min_length=3, max_length=1000)
    review_days: int = Field(90, ge=1, le=3650)
    tourist_id: int | None = None


class RetentionRunBody(_Strict):
    category: str | None = Field(None, max_length=64)
