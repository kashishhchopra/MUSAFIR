"""Privacy & Consent Dashboard: what a tourist controls about their own
tracking data, and the scheduled enforcement of it.

Two things live here: an on-demand purge a tourist can trigger themselves
("delete my data now"), and a scheduled job that auto-deletes raw location
history once a tourist's own retention window has passed after their trip
ends -- the DPDP-Act-facing answer to "how long do you keep this."
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.tourist import LocationPing, Tourist

logger = get_logger(__name__)


def privacy_report(db: Session, tourist: Tourist) -> dict:
    ping_count = db.query(LocationPing).filter(LocationPing.tourist_id == tourist.id).count()
    purge_after = tourist.trip_end + timedelta(days=tourist.data_retention_days)
    return {
        "tracking_enabled": tourist.tracking_enabled,
        "data_retention_days": tourist.data_retention_days,
        "preferred_language": tourist.preferred_language,
        "location_pings_stored": ping_count,
        "auto_purge_at": purge_after,
    }


def purge_location_history(db: Session, tourist: Tourist) -> int:
    """Immediate, tourist-initiated deletion of all raw GPS history. Returns
    the number of pings removed. Safety-score/incident/alert records are
    untouched -- those are the operational record of what happened, not raw
    tracking data, and stay subject to normal retention."""
    deleted = (
        db.query(LocationPing)
        .filter(LocationPing.tourist_id == tourist.id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted


def purge_expired_location(db: Session) -> tuple[int, int, int]:
    """Scheduled-purge core: returns (tourists_examined, pings_deleted,
    pings_retained). Pings from the start of a still-open emergency onwards,
    and tourists under an active retention hold, are kept (and counted)."""
    from app.services import privacy_retention as retention

    now = utc_now()
    examined = deleted_total = retained_total = 0
    category_held = retention.category_held(db, "location_history")
    held_ids = retention.held_tourist_ids(db, "location_history")
    tourists = db.query(Tourist).filter(Tourist.trip_end < now).all()
    for t in tourists:
        cutoff = t.trip_end + timedelta(days=t.data_retention_days)
        if now < cutoff:
            continue
        examined += 1
        q = db.query(LocationPing).filter(LocationPing.tourist_id == t.id)
        if category_held or t.id in held_ids:
            retained_total += q.count()
            continue
        since = protected_since(db, t.id)
        if since is not None:
            retained_total += q.filter(LocationPing.timestamp >= since).count()
            q = q.filter(LocationPing.timestamp < since)
        deleted = q.delete(synchronize_session=False)
        if deleted:
            logger.info("retention_purge", tourist_id=t.id, pings_deleted=deleted)
            deleted_total += deleted
    if deleted_total:
        db.commit()
    return examined, deleted_total, retained_total


def tick_retention_purge(db: Session) -> int:
    """Scheduled job: delete location history for any tourist whose trip
    ended more than `data_retention_days` ago. Runs on the same scheduler as
    the escalation tick (see app/main.py); the central retention engine
    (services/privacy_retention.py) calls this for the location category."""
    return purge_expired_location(db)[1]


# ---------------------------------------------------------------------------
# Location privacy (view / selective deletion). Ordinary GPS history lives in
# `location_pings`; an SOS has its own trail in `emergency_location_pings`.
# Deleting history never switches SOS off, and never removes data that an
# unresolved emergency is still using.
# ---------------------------------------------------------------------------
def protected_since(db: Session, tourist_id: int):
    """Start of the oldest still-open emergency for this tourist, or None."""
    from app.models.incident import Incident

    row = (
        db.query(Incident.detected_at)
        .filter(Incident.tourist_id == tourist_id, Incident.status != "resolved")
        .order_by(Incident.detected_at.asc())
        .first()
    )
    return row[0] if row else None


def _ping_filter(db: Session, tourist_id: int, date_from=None, date_to=None):
    q = db.query(LocationPing).filter(LocationPing.tourist_id == tourist_id)
    if date_from is not None:
        q = q.filter(LocationPing.timestamp >= date_from)
    if date_to is not None:
        q = q.filter(LocationPing.timestamp <= date_to)
    return q


def list_location_history(db: Session, tourist: Tourist, *, date_from=None, date_to=None,
                          limit: int = 100, offset: int = 0) -> dict:
    from sqlalchemy import func

    base = _ping_filter(db, tourist.id, date_from, date_to)
    total = base.count()
    span = db.query(func.min(LocationPing.timestamp), func.max(LocationPing.timestamp)).filter(
        LocationPing.tourist_id == tourist.id).one()
    rows = base.order_by(LocationPing.timestamp.desc()).offset(offset).limit(limit).all()
    return {
        "total": total, "limit": limit, "offset": offset,
        "available_from": span[0], "available_to": span[1],
        "tracking_enabled": tourist.tracking_enabled,
        "items": [{"id": p.id, "lat": p.lat, "lng": p.lng, "speed_kmh": p.speed_kmh,
                   "timestamp": p.timestamp, "is_anomaly": p.is_anomaly} for p in rows],
    }


def delete_location_history(db: Session, tourist: Tourist, *, ids: list[int] | None = None,
                            date_from=None, date_to=None, delete_all: bool = False) -> dict:
    """Delete this tourist's ordinary location history. Exactly one selector:
    `ids`, a date range, or `delete_all`. Only ever touches `tourist.id` rows."""
    from app.models.emergency_location import EmergencyLocationPing
    from app.models.incident import Incident

    chosen = sum([bool(ids), date_from is not None or date_to is not None, bool(delete_all)])
    if chosen != 1:
        raise ValueError("Choose exactly one of: ids, a date range, or delete_all.")

    q = _ping_filter(db, tourist.id, date_from, date_to)
    if ids:
        q = q.filter(LocationPing.id.in_(ids))
    matched = q.count()
    since = protected_since(db, tourist.id)
    retained = 0
    if since is not None:
        retained = q.filter(LocationPing.timestamp >= since).count()
        q = q.filter(LocationPing.timestamp < since)
    deleted = q.delete(synchronize_session=False)

    trail_deleted = 0
    cleared = False
    if delete_all:
        resolved = db.query(Incident.id).filter(
            Incident.tourist_id == tourist.id, Incident.status == "resolved")
        trail_deleted = db.query(EmergencyLocationPing).filter(
            EmergencyLocationPing.incident_id.in_(resolved)
        ).delete(synchronize_session=False)
        if since is None:
            # The cached "last known position" is also a stored location.
            tourist.last_lat = tourist.last_lng = tourist.last_seen = None
            cleared = True
    db.commit()
    return {
        "deleted": deleted, "emergency_trail_deleted": trail_deleted,
        "retained": retained, "matched": matched, "cached_position_cleared": cleared,
        "retained_reason": ("Kept: it is part of an emergency that is still open."
                            if retained else None),
    }
