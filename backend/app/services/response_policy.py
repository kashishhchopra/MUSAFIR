"""Response-time targets and escalation timing -- one central place.

Defaults come from app/core/config.py (RESPONSE_*). An administrator can
override the targets, the stage-2 duration and the global timeout at runtime
(PUT /response/config); an override is stored in `response_settings` and wins.
Nothing else in the code base hard-codes a target or a deadline.

The shipped defaults (60 s / 3 min / 5 min / 10 min) are ILLUSTRATIVE demo
values, not emergency-service standards.

How the three clocks relate (also documented in the README):

* response target     -- how long a responder has to ACKNOWLEDGE, by severity.
                         deadline = detected_at + target.
* stage deadline      -- when an unacknowledged incident moves to the next
                         authority. Stage 1 (primary responder) ends at the
                         response deadline; stage 2 (supervisor) lasts
                         `stage2_seconds`; stage 3 (higher authority) lasts
                         until the global timeout.
* global timeout      -- one fixed bound for the whole lifecycle, measured from
                         when the workflow started. It is never reset, and no
                         stage deadline is ever later than it.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.response import ResponseSetting

SEVERITIES = ("critical", "high", "medium", "low")
MIN_SECONDS = 5
MAX_SECONDS = 7 * 24 * 3600

_TARGET_KEYS = {s: f"response_target.{s}" for s in SEVERITIES}
_STAGE2_KEY = "stage2_seconds"
_GLOBAL_KEY = "global_timeout_seconds"
_WARNING_KEY = "warning_seconds"


class PolicyError(ValueError):
    pass


def _defaults() -> dict[str, int]:
    return {
        _TARGET_KEYS["critical"]: settings.RESPONSE_TARGET_CRITICAL_SECONDS,
        _TARGET_KEYS["high"]: settings.RESPONSE_TARGET_HIGH_SECONDS,
        _TARGET_KEYS["medium"]: settings.RESPONSE_TARGET_MEDIUM_SECONDS,
        _TARGET_KEYS["low"]: settings.RESPONSE_TARGET_LOW_SECONDS,
        _STAGE2_KEY: settings.RESPONSE_STAGE2_SECONDS,
        _GLOBAL_KEY: settings.RESPONSE_GLOBAL_TIMEOUT_SECONDS,
        _WARNING_KEY: settings.RESPONSE_WARNING_SECONDS,
    }


def _stored(db: Session) -> dict[str, int]:
    out = {}
    for row in db.query(ResponseSetting).all():
        try:
            out[row.key] = int(row.value)
        except ValueError:
            continue
    return out


def _effective(db: Session) -> dict[str, int]:
    return {**_defaults(), **_stored(db)}


def target_seconds(db: Session, severity: str) -> int:
    sev = severity if severity in SEVERITIES else "medium"
    return _effective(db)[_TARGET_KEYS[sev]]


def targets(db: Session) -> dict[str, int]:
    eff = _effective(db)
    return {s: eff[_TARGET_KEYS[s]] for s in SEVERITIES}


def stage2_seconds(db: Session) -> int:
    return _effective(db)[_STAGE2_KEY]


def global_timeout_seconds(db: Session) -> int:
    return _effective(db)[_GLOBAL_KEY]


def warning_seconds(db: Session) -> int:
    return _effective(db)[_WARNING_KEY]


def stage_labels() -> list[str]:
    labels = [p.strip() for p in settings.RESPONSE_STAGE_LABELS.split(",") if p.strip()]
    defaults = ["Primary responder", "Supervisor", "Higher authority"]
    return (labels + defaults[len(labels):])[:3]


def audience(stage: int | str) -> list[str]:
    raw = {1: settings.RESPONSE_STAGE1_AUDIENCE, 2: settings.RESPONSE_STAGE2_AUDIENCE,
           3: settings.RESPONSE_STAGE3_AUDIENCE, "global": settings.RESPONSE_GLOBAL_TIMEOUT_AUDIENCE}[stage]
    return [p.strip() for p in raw.split(",") if p.strip()]


def config(db: Session) -> dict:
    """What the dashboards show: effective values and where each one comes from."""
    stored = _stored(db)
    eff = _effective(db)
    rows = db.query(ResponseSetting).all()
    last = max(rows, key=lambda r: r.updated_at) if rows else None
    return {
        "enabled": settings.RESPONSE_MANAGEMENT_ENABLED,
        "response_targets_seconds": targets(db),
        "stage2_seconds": eff[_STAGE2_KEY],
        "global_timeout_seconds": eff[_GLOBAL_KEY],
        "warning_seconds": eff[_WARNING_KEY],
        "stage_labels": stage_labels(),
        "audiences": {"1": audience(1), "2": audience(2), "3": audience(3), "global": audience("global")},
        "overridden": sorted(stored),
        "updated_at": last.updated_at if last else None,
        "updated_by": last.updated_by if last else None,
        "limits": {"min_seconds": MIN_SECONDS, "max_seconds": MAX_SECONDS},
        "illustrative": True,
    }


def update(db: Session, changes: dict, actor_id: int | None) -> dict:
    """Validate and store admin overrides. `changes` may contain
    `response_targets_seconds` ({severity: seconds}), `stage2_seconds`,
    `global_timeout_seconds`, `warning_seconds`. Nothing is written unless the
    WHOLE resulting configuration is valid. Caller commits."""
    eff = _effective(db)
    new = dict(eff)
    for sev, val in (changes.get("response_targets_seconds") or {}).items():
        if sev not in SEVERITIES:
            raise PolicyError(f"unknown severity '{sev}'")
        new[_TARGET_KEYS[sev]] = _seconds(val, f"{sev} target")
    if changes.get("stage2_seconds") is not None:
        new[_STAGE2_KEY] = _seconds(changes["stage2_seconds"], "stage 2 duration")
    if changes.get("global_timeout_seconds") is not None:
        new[_GLOBAL_KEY] = _seconds(changes["global_timeout_seconds"], "global timeout")
    if changes.get("warning_seconds") is not None:
        w = int(changes["warning_seconds"])
        if w < 0 or w > MAX_SECONDS:
            raise PolicyError("warning_seconds must be 0 (off) or a positive number of seconds")
        new[_WARNING_KEY] = w

    t = [new[_TARGET_KEYS[s]] for s in SEVERITIES]  # critical, high, medium, low
    if any(a > b for a, b in zip(t, t[1:])):
        raise PolicyError("targets must not get longer for a more serious severity "
                          "(critical <= high <= medium <= low)")
    if new[_GLOBAL_KEY] < max(t):
        raise PolicyError("the global timeout must be at least as long as the longest response target, "
                          "otherwise stage 1 could never finish")
    if new[_WARNING_KEY] and new[_WARNING_KEY] >= min(t):
        raise PolicyError("the deadline warning must be shorter than the shortest response target")

    now = utc_now()
    for key, value in new.items():
        if eff.get(key) == value:
            continue
        row = db.get(ResponseSetting, key)
        if row is None:
            db.add(ResponseSetting(key=key, value=str(value), updated_by=actor_id, updated_at=now))
        else:
            row.value, row.updated_by, row.updated_at = str(value), actor_id, now
    db.flush()
    return config(db)


def _seconds(value, label: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise PolicyError(f"{label} must be a whole number of seconds") from None
    if not MIN_SECONDS <= v <= MAX_SECONDS:
        raise PolicyError(f"{label} must be between {MIN_SECONDS} and {MAX_SECONDS} seconds")
    return v


def deadline(detected_at: datetime, seconds: int) -> datetime:
    from datetime import timedelta
    return detected_at + timedelta(seconds=seconds)
