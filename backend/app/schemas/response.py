"""Request bodies for the response-management API (api/response.py)."""
from pydantic import BaseModel, ConfigDict, Field


class SeverityOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: str = Field(..., pattern="^(critical|high|medium|low)$")
    reason: str = Field(..., min_length=5, max_length=500)


class ResponseConfigUpdate(BaseModel):
    """Any subset of the adjustable settings; omitted fields are unchanged."""
    model_config = ConfigDict(extra="forbid")
    response_targets_seconds: dict[str, int] | None = None
    stage2_seconds: int | None = Field(None, ge=1)
    global_timeout_seconds: int | None = Field(None, ge=1)
    warning_seconds: int | None = Field(None, ge=0)
