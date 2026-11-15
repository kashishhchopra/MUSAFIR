"""The personal-data registry: the single list of where MUSAFIR keeps a
tourist's personal data.

It drives three things so they can never drift apart:
  * the data inventory shown to users and admins,
  * the data export (what is collected for "Download My Data"),
  * the account-deletion engine (what is erased / de-identified / retained).

Adding a table that stores personal data = adding one `Source` here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.core.config import settings
from app.models.alert import Alert
from app.models.audit import AuditLog
from app.models.checkin import CheckIn
from app.models.device import Device
from app.models.efir import EFIR
from app.models.emergency_location import EmergencyLocationPing
from app.models.fall_event import FallEvent
from app.models.fare_check import FareCheck
from app.models.guardian import TripGuardian
from app.models.incident import Incident, IncidentEvent
from app.models.itinerary import ItineraryDocument
from app.models.liveness import LivenessVerification
from app.models.medical_profile import MedicalProfile
from app.models.notification import NotificationLog, PushSubscription
from app.models.password_reset import PasswordResetToken
from app.models.permit import Permit
from app.models.privacy import ConsentRecord, DataExportRequest, PrivacyGrievance
from app.models.safety_report import SafetyReport
from app.models.tourist import IdBlock, LocationPing, Tourist
from app.models.tourist_id import TouristIdScan, TouristIdToken
from app.models.user import User

# Columns that are credentials/secrets and never leave the system in an export.
NEVER_EXPORT = frozenset({
    "hashed_password", "hashed_key", "token", "token_hash", "raw_token_encrypted",
    "p256dh", "auth", "endpoint", "duress_pin_hash", "verification_token",
    "file_name", "sessions_valid_from",
})


def subject_ref(tourist_id: int) -> str:
    """Opaque, stable reference for a tourist that survives erasure. HMAC with
    the server secret, so it cannot be reversed or recomputed by outsiders."""
    return hmac.new(settings.SECRET_KEY.encode(), f"tourist:{tourist_id}".encode(),
                    hashlib.sha256).hexdigest()[:20]


def new_public_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5).upper()}"


@dataclass(frozen=True)
class Source:
    key: str                       # export section / inventory key
    model: type
    query: Callable[[Session, Tourist, User | None], Query]
    category: str                  # retention policy category
    title: str
    purpose: str
    necessary: str                 # required | optional
    access: str                    # who can read it
    shared_with: str
    default_action: str = "delete"  # delete | anonymize | retain (policy overrides)
    anonymizer: Callable[[Session, Query, Tourist, User | None], int] | None = None
    note: str = ""                 # deletion dependency note
    exclude: tuple = field(default_factory=tuple)


def _incident_ids(db: Session, t: Tourist):
    return db.query(Incident.id).filter(Incident.tourist_id == t.id)


# ---- anonymizers: return the number of rows de-identified -------------------
def _anon_incidents(db: Session, q: Query, t, u) -> int:
    n = 0
    for inc in q.filter(Incident.status == "resolved").all():
        inc.tourist_id = None
        inc.description = ""
        if inc.lat is not None:
            inc.lat, inc.lng = round(inc.lat, 2), round(inc.lng, 2)  # ~1 km, no longer a person's position
        n += 1
    return n


def _anon_incident_events(db: Session, q: Query, t, u) -> int:
    n = 0
    for ev in q.all():
        ev.note = ""
        n += 1
    return n


def _anon_scans(db: Session, q: Query, t, u) -> int:
    return q.update({TouristIdScan.tourist_id: None, TouristIdScan.scan_lat: None,
                     TouristIdScan.scan_lng: None}, synchronize_session=False)


def _anon_notifications(db: Session, q: Query, t, u) -> int:
    return q.update({NotificationLog.tourist_id: None, NotificationLog.address: None,
                     NotificationLog.subscription_id: None,
                     NotificationLog.recipient_label: "", NotificationLog.body: "",
                     NotificationLog.title: "", NotificationLog.payload: "{}"},
                    synchronize_session=False)


def _anon_audit(db: Session, q: Query, t, u) -> int:
    """Replace the person's e-mail / digital ID with nothing; other people's
    rows that merely mention them keep their own actor."""
    n = 0
    for row in q.all():
        if u is not None and row.actor == u.email:
            row.actor = "deleted-user"
        if row.target == t.digital_id:
            row.target = ""
        n += 1
    return n


def _anon_consents(db: Session, q: Query, t, u) -> int:
    return q.update({ConsentRecord.tourist_id: None}, synchronize_session=False)


def _anon_grievances(db: Session, q: Query, t, u) -> int:
    n = 0
    for g in q.filter(PrivacyGrievance.status.in_(("resolved", "rejected"))).all():
        g.tourist_id, g.description = None, "[removed when the account was deleted]"
        n += 1
    return n


def _by_tid(model, col="tourist_id"):
    return lambda db, t, u: db.query(model).filter(getattr(model, col) == t.id)


SOURCES: list[Source] = [
    Source("emergency_location_trail", EmergencyLocationPing,
           lambda db, t, u: db.query(EmergencyLocationPing).filter(
               EmergencyLocationPing.incident_id.in_(_incident_ids(db, t))),
           "sos_incident_records", "Emergency live-location trail",
           "Show responders where you are during an SOS", "required",
           "Admin, assigned responders", "Police station / responder handling the incident",
           note="Only for resolved incidents; trail of an open incident is retained."),
    Source("incident_events", IncidentEvent,
           lambda db, t, u: db.query(IncidentEvent).filter(
               IncidentEvent.incident_id.in_(_incident_ids(db, t))),
           "sos_incident_records", "Incident timeline notes", "Record how an emergency was handled",
           "required", "Admin, responders", "Police station / responder", "anonymize",
           anonymizer=_anon_incident_events),
    Source("sos_incidents", Incident, _by_tid(Incident), "sos_incident_records",
           "SOS & incident records", "Dispatch and record emergency response", "required",
           "Admin, responders", "Police station / responder; transferred stations if you consented to a transfer",
           "anonymize", anonymizer=_anon_incidents,
           note="An unresolved incident is always retained."),
    Source("efir_records", EFIR, _by_tid(EFIR), "efir_records", "E-FIR drafts",
           "Police report generated for an incident", "optional", "Admin only",
           "Police (as filed)", "retain", note="Held pending legal review of retention."),
    Source("alerts", Alert, _by_tid(Alert), "activity_records", "Safety alerts about you",
           "Warn you and operators of risk", "required", "Admin", "None"),
    Source("fall_events", FallEvent, _by_tid(FallEvent), "activity_records", "Fall detection events",
           "Detect falls and raise help", "optional", "You, admin", "None"),
    Source("checkins", CheckIn, _by_tid(CheckIn), "activity_records", "Check-ins & Walk-Me-Home",
           "Outing and walk monitoring", "optional", "You, admin, your guardian link", "Your guardians"),
    Source("safety_reports", SafetyReport, _by_tid(SafetyReport), "activity_records",
           "Safety reports you submitted", "Crowd-sourced safety data", "optional", "Admin", "None"),
    Source("fare_checks", FareCheck, _by_tid(FareCheck), "activity_records", "Fare checks",
           "Taxi fare fairness check", "optional", "You", "None"),
    Source("permits", Permit, _by_tid(Permit), "activity_records", "Permit drafts",
           "Prepare permit applications", "optional", "You, admin", "None"),
    Source("itinerary_documents", ItineraryDocument, _by_tid(ItineraryDocument), "activity_records",
           "Uploaded itinerary documents", "Extract your trip plan", "optional", "You", "None"),
    Source("liveness_checks", LivenessVerification, _by_tid(LivenessVerification), "tourist_profile",
           "Registration liveness check", "Confirm the ID photo is of a live person", "required",
           "Admin (internal)", "None"),
    Source("notification_log", NotificationLog, _by_tid(NotificationLog), "notification_logs",
           "Notification delivery log", "Delivery proof and retries", "required", "Admin",
           "SMS/push provider", "anonymize", anonymizer=_anon_notifications),
    Source("trip_guardians", TripGuardian, _by_tid(TripGuardian), "guardian_links",
           "Trip guardian links", "Let a person you choose see your status", "optional",
           "You, the guardian", "The guardian you invited"),
    Source("devices", Device, _by_tid(Device), "device_info", "Registered wearables",
           "Receive fall/SOS data from a band", "optional", "You, admin", "None"),
    Source("push_subscriptions", PushSubscription,
           lambda db, t, u: db.query(PushSubscription).filter(
               PushSubscription.user_id == (u.id if u else -1)),
           "device_info", "Push subscriptions (this phone)", "Deliver alerts", "optional",
           "You", "Your browser vendor's push service"),
    Source("tourist_id_scans", TouristIdScan, _by_tid(TouristIdScan), "id_scan_log",
           "Times your Digital ID was scanned", "Accountability for ID checks", "required",
           "You (summary), admin", "None", "anonymize", anonymizer=_anon_scans),
    Source("tourist_id_tokens", TouristIdToken, _by_tid(TouristIdToken), "digital_identity",
           "Digital ID QR credentials", "Verify you in the field", "required", "You",
           "None", exclude=("token_hash", "raw_token_encrypted")),
    Source("id_chain", IdBlock, _by_tid(IdBlock), "digital_identity", "Digital ID hash chain",
           "Tamper-evident ID history", "required", "You, admin", "Public anchor holds only a hash"),
    Source("medical_profile", MedicalProfile, _by_tid(MedicalProfile), "medical_profile",
           "Emergency medical profile", "Help responders in a medical emergency", "optional",
           "You; responders only through your QR and only when authorised", "Responder who scans your QR"),
    Source("location_history", LocationPing, _by_tid(LocationPing), "location_history",
           "Location history", "Safety scoring and anomaly detection", "optional",
           "You, admin", "None"),
    Source("consent_records", ConsentRecord, _by_tid(ConsentRecord), "consent_records",
           "Consent records", "Prove what you agreed to and when", "required", "You", "None",
           "anonymize",
           anonymizer=_anon_consents,
           note="Link to you is removed; an opaque reference keeps the consent history."),
    Source("export_requests", DataExportRequest, _by_tid(DataExportRequest), "data_exports",
           "Data export requests", "Deliver your data copy", "optional", "You", "None"),
    Source("privacy_grievances", PrivacyGrievance, _by_tid(PrivacyGrievance), "grievances",
           "Privacy grievances", "Handle your complaints", "optional", "You, admin", "None",
           "anonymize", anonymizer=_anon_grievances,
           note="An open grievance is retained until it is resolved."),
    Source("security_log", AuditLog,
           lambda db, t, u: db.query(AuditLog).filter(or_(
               AuditLog.actor == (u.email if u else "\x00"), AuditLog.target == t.digital_id)),
           "security_audit_logs", "Security & activity log entries about you",
           "Security and accountability", "required", "Admin", "None", "anonymize",
           anonymizer=_anon_audit),
    Source("password_resets", PasswordResetToken,
           lambda db, t, u: db.query(PasswordResetToken).filter(
               PasswordResetToken.user_id == (u.id if u else -1)),
           "session_tokens", "Password-reset requests", "Account recovery", "required",
           "System only", "E-mail provider", exclude=("token_hash",)),
]
SOURCE_BY_KEY = {s.key: s for s in SOURCES}


def row_to_dict(obj, exclude: tuple = ()) -> dict:
    """Plain, JSON-safe dict of a row's columns minus anything secret."""
    out = {}
    for col in obj.__table__.columns:
        name = col.name
        if name in NEVER_EXPORT or name in exclude:
            continue
        val = getattr(obj, name)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        out[name] = val
    return out


def inventory(db: Session | None = None) -> list[dict]:
    """The data inventory, with each category's live retention policy."""
    from app.services import privacy_retention as retention

    policies = {p.category: p for p in retention.list_policies(db)} if db is not None else {}
    cat_rows: dict[str, dict] = {}
    for s in SOURCES:
        c = cat_rows.setdefault(s.category, {
            "category": s.category, "sources": [], "tables": [], "purposes": set(),
            "necessary": "optional", "access": set(), "shared_with": set(),
        })
        c["sources"].append(s.title)
        c["tables"].append(s.model.__tablename__)
        c["purposes"].add(s.purpose)
        if s.necessary == "required":
            c["necessary"] = "required"
        c["access"].add(s.access)
        c["shared_with"].add(s.shared_with)
    out = []
    for c in cat_rows.values():
        pol = policies.get(c["category"])
        out.append({
            "category": c["category"], "tables": sorted(set(c["tables"])), "items": c["sources"],
            "purpose": "; ".join(sorted(c["purposes"])), "necessary": c["necessary"],
            "who_can_access": "; ".join(sorted(c["access"])),
            "shared_with": "; ".join(sorted(x for x in c["shared_with"] if x and x != "None")) or "Not shared",
            "retention": retention.policy_public(pol) if pol else None,
        })
    return sorted(out, key=lambda r: r["category"])


def external_processors() -> list[dict]:
    try:
        v = json.loads(settings.PRIVACY_EXTERNAL_PROCESSORS or "[]")
        return [p for p in v if isinstance(p, dict) and p.get("name")]
    except (ValueError, TypeError):
        return []
