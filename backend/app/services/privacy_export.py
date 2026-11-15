"""'Download My Personal Data'.

A request builds a snapshot of the tourist's own data, stores it encrypted at
rest (same Fernet key as the other PII columns) in a private directory, and
exposes it only through an authenticated, owner-only, expiring download. It is
never e-mailed. Secrets (password hashes, token hashes, push keys, device keys,
the duress PIN) are excluded by `privacy_registry.NEVER_EXPORT`.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import os
import time
import zipfile
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.logging import get_logger
from app.core.time import utc_now
from app.models.privacy import DataExportRequest
from app.models.tourist import Tourist
from app.models.user import User
from app.services import privacy_consent, privacy_registry
from app.services.privacy_registry import SOURCE_BY_KEY, new_public_id, row_to_dict

logger = get_logger(__name__)
FORMATS = ("json", "csv")
SCHEMA_VERSION = "1"


class ExportError(ValueError):
    pass


def export_dir() -> Path:
    d = Path(settings.PRIVACY_EXPORT_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _rows(db: Session, t: Tourist, u: User | None, key: str) -> list[dict]:
    s = SOURCE_BY_KEY[key]
    return [row_to_dict(r, s.exclude) for r in s.query(db, t, u).all()]


def build_payload(db: Session, tourist: Tourist, user: User | None, export_id: str, fmt: str) -> dict:
    t = tourist
    profile = row_to_dict(t)
    profile["itinerary"] = json.loads(t.itinerary or "[]")
    profile["emergency_contacts"] = json.loads(t.emergency_contacts or "[]")
    profile["planned_states"] = json.loads(t.planned_states or "[]")
    consents = privacy_consent.history(db, t)
    handled = {"location_history", "consent_records", "sos_incidents", "incident_events",
               "emergency_location_trail", "tourist_id_tokens", "tourist_id_scans", "id_chain",
               "medical_profile"}
    other = {k: _rows(db, t, user, k) for k in SOURCE_BY_KEY if k not in handled}
    return {
        "export_id": export_id,
        "generated_at": utc_now().isoformat(),
        "schema_version": SCHEMA_VERSION,
        "format": fmt,
        "user_profile": profile,
        "account": ({"email": user.email, "role": user.role, "created_at": user.created_at.isoformat()}
                    if user else None),
        "privacy_preferences": {
            "tracking_enabled": t.tracking_enabled,
            "data_retention_days": t.data_retention_days,
            "preferred_language": t.preferred_language,
            "consent_status": [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in c.items()
                 if k in ("key", "status", "consented_at", "withdrawn_at", "notice_version")}
                for c in privacy_consent.status_overview(db, t)],
        },
        "consent_records": [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in c.items()}
            for c in consents],
        "location_history": _rows(db, t, user, "location_history"),
        "sos_records": {
            "incidents": _rows(db, t, user, "sos_incidents"),
            "timeline": _rows(db, t, user, "incident_events"),
            "live_location_trail": _rows(db, t, user, "emergency_location_trail"),
        },
        "digital_identity": {
            "qr_credentials": _rows(db, t, user, "tourist_id_tokens"),
            "times_scanned": _rows(db, t, user, "tourist_id_scans"),
            "id_chain": _rows(db, t, user, "id_chain"),
        },
        "medical_profile": _rows(db, t, user, "medical_profile"),
        "other_records": other,
        "processing_information": [
            {"category": c["category"], "purpose": c["purpose"], "shared_with": c["shared_with"],
             "who_can_access": c["who_can_access"],
             "retention": (c["retention"] or {}).get("state")}
            for c in privacy_registry.inventory(db)],
        "notice": "This file contains your personal data. Keep it private. "
                  "It does not include passwords, tokens or other people's data.",
    }


def _flatten(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v


def _write_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([_flatten(r.get(c)) for c in cols])
    return buf.getvalue()


def _csv_bundle(payload: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        prof = [{"field": k, "value": _flatten(v)} for k, v in payload["user_profile"].items()]
        z.writestr("user_profile.csv", _write_csv(prof))
        z.writestr("consent_records.csv", _write_csv(payload["consent_records"]))
        z.writestr("location_history.csv", _write_csv(payload["location_history"]))
        z.writestr("medical_profile.csv", _write_csv(payload["medical_profile"]))
        for k, v in payload["sos_records"].items():
            z.writestr(f"sos_{k}.csv", _write_csv(v))
        for k, v in payload["digital_identity"].items():
            z.writestr(f"digital_identity_{k}.csv", _write_csv(v))
        for k, v in payload["other_records"].items():
            if v:
                z.writestr(f"{k}.csv", _write_csv(v))
        z.writestr("processing_information.csv", _write_csv(payload["processing_information"]))
        z.writestr("README.txt", f"MUSAFIR personal data export {payload['export_id']}\n"
                                 f"Generated {payload['generated_at']}\n{payload['notice']}\n")
    return out.getvalue()


def request_export(db: Session, tourist: Tourist, user: User | None, fmt: str) -> DataExportRequest:
    if fmt not in FORMATS:
        raise ExportError(f"Format must be one of: {', '.join(FORMATS)}")
    since = utc_now() - timedelta(days=1)
    recent = db.query(DataExportRequest).filter(
        DataExportRequest.tourist_id == tourist.id, DataExportRequest.created_at >= since).count()
    if recent >= settings.PRIVACY_EXPORT_MAX_PER_DAY:
        raise ExportError("Daily export limit reached. Try again tomorrow.")
    req = DataExportRequest(export_id=new_public_id("EXP"), tourist_id=tourist.id,
                            export_format=fmt, status="pending")
    db.add(req)
    db.commit()
    try:
        payload = build_payload(db, tourist, user, req.export_id, fmt)
        raw = (json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode()
               if fmt == "json" else _csv_bundle(payload))
        name = f"{req.export_id}.bin"
        (export_dir() / name).write_text(encrypt(base64.b64encode(raw).decode()), encoding="ascii")
        now = utc_now()
        req.file_name, req.size_bytes = name, len(raw)
        req.status, req.ready_at = "ready", now
        req.expires_at = now + timedelta(hours=settings.PRIVACY_EXPORT_TTL_HOURS)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        req = db.get(DataExportRequest, req.id)
        req.status, req.failure_reason = "failed", type(exc).__name__
        logger.error("export_failed", export_id=req.export_id, error=str(exc))
    db.commit()
    db.refresh(req)
    return req


def out(r: DataExportRequest) -> dict:
    return {
        "export_id": r.export_id, "status": r.status, "format": r.export_format,
        "size_bytes": r.size_bytes, "created_at": r.created_at, "ready_at": r.ready_at,
        "expires_at": r.expires_at, "downloaded_at": r.downloaded_at,
        "download_count": r.download_count, "failure_reason": r.failure_reason,
    }


def _is_expired(r: DataExportRequest) -> bool:
    return r.status == "expired" or (r.expires_at is not None and r.expires_at < utc_now())


def get_for(db: Session, tourist_id: int, export_id: str) -> DataExportRequest | None:
    """Owner-scoped lookup: another tourist's export id simply does not exist."""
    r = db.query(DataExportRequest).filter(
        DataExportRequest.export_id == export_id,
        DataExportRequest.tourist_id == tourist_id).first()
    if r is not None and r.status == "ready" and _is_expired(r):
        _expire(db, r)
    return r


def list_for(db: Session, tourist_id: int) -> list[DataExportRequest]:
    rows = db.query(DataExportRequest).filter(
        DataExportRequest.tourist_id == tourist_id).order_by(DataExportRequest.id.desc()).all()
    for r in rows:
        if r.status == "ready" and _is_expired(r):
            _expire(db, r)
    return rows


def read_file(db: Session, r: DataExportRequest) -> tuple[bytes, str, str]:
    """(bytes, media_type, download_name). Caller has already checked ownership."""
    if r.status != "ready" or _is_expired(r) or not r.file_name:
        raise ExportError("This export is not available any more. Request a new one.")
    path = export_dir() / os.path.basename(r.file_name)
    if not path.exists():
        raise ExportError("This export is not available any more. Request a new one.")
    raw = base64.b64decode(decrypt(path.read_text(encoding="ascii")))
    r.download_count += 1
    r.downloaded_at = utc_now()
    db.commit()
    if r.export_format == "json":
        return raw, "application/json", f"musafir-data-{r.export_id}.json"
    return raw, "application/zip", f"musafir-data-{r.export_id}.zip"


def _remove_file(r: DataExportRequest) -> None:
    if r.file_name:
        try:
            (export_dir() / os.path.basename(r.file_name)).unlink(missing_ok=True)
        except OSError:
            logger.warning("export_file_remove_failed", export_id=r.export_id)


def _expire(db: Session, r: DataExportRequest) -> None:
    _remove_file(r)
    r.status, r.file_name = "expired", None
    db.commit()


def cleanup_expired(db: Session) -> int:
    """Delete files of expired exports (and stray files with no live request)."""
    n = 0
    for r in db.query(DataExportRequest).filter(DataExportRequest.status == "ready").all():
        if _is_expired(r):
            _expire(db, r)
            n += 1
    live = {r.file_name for r in db.query(DataExportRequest).filter(
        DataExportRequest.file_name.isnot(None)).all()}
    try:
        for f in export_dir().iterdir():
            # Only files older than an hour: a fresh one may belong to an export
            # that has written its file but not yet committed its row.
            if f.is_file() and f.name not in live and f.stat().st_mtime < time.time() - 3600:
                f.unlink(missing_ok=True)
                n += 1
    except OSError:
        pass
    return n


def delete_all_for_tourist(db: Session, tourist_id: int) -> int:
    rows = db.query(DataExportRequest).filter(DataExportRequest.tourist_id == tourist_id).all()
    for r in rows:
        _remove_file(r)
        db.delete(r)
    db.commit()
    return len(rows)
