"""Emergency notifications: Web Push + SMS, with a delivery log, de-duplication
and retries.

Layers
  1. Transport channels (the original extension point, unchanged):
       `get_channel().send(to, subject, body)`
       - ConsoleNotificationChannel  logs instead of sending (default, no credentials)
       - TwilioSmsChannel            real SMS via Twilio's REST API
     Email has its own entry point, `get_email_channel()` (password-reset codes):
       - SmtpEmailChannel            real email over SMTP/STARTTLS (Gmail by default)
  2. Push -- `_send_push()`: Web Push (VAPID, via pywebpush) to browsers that
     subscribed (tourists, responders, admins, and guardians who only have a
     share link), shown by the service worker even when the page is closed;
     or Firebase Cloud Messaging (`_send_fcm()`) to the Android app.
  3. The pipeline -- `notify_sos`, `notify_incident_update`,
     `notify_incident_opened`, `notify_escalation`, `send_test`:
       * resolve recipients, write one NotificationLog row per (message, channel,
         recipient) inside the caller's transaction -- so a rolled-back SOS never
         notifies anyone;
       * after the caller COMMITS (SQLAlchemy `after_commit`), deliver on a worker
         thread (never blocking the SOS request) -- or inline when
         NOTIFICATIONS_SYNC / tests;
       * de-duplicate on (event, incident, channel, recipient);
       * retry transient failures with exponential backoff (`retry_due`, run by
         the scheduler); permanent failures (expired subscription, invalid
         number) are not retried.

Honesty rules
  * The console fallback is recorded as status "simulated" -- never "sent".
  * SMS is never sent for simulated (SIM-) tourists, and `SMS_ALLOWLIST` can pin
    delivery to known test numbers.
  * Silent/duress SOS never pushes to the tourist's own device, and tells every
    other recipient not to call or approach visibly.
"""
from __future__ import annotations

import base64
import json
import os
import re
import smtplib
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

import httpx
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import utc_now
from app.db.session import SessionLocal
from app.models.guardian import TripGuardian
from app.models.incident import Incident
from app.models.notification import NotificationLog, PushSubscription
from app.models.tourist import Tourist
from app.models.user import User

logger = get_logger(__name__)


# =============================================================== transport channels
@dataclass
class SendResult:
    ok: bool
    provider_id: str | None = None
    error: str | None = None
    retryable: bool = False
    demo: bool = False


class NotificationChannel(Protocol):
    def send(self, to: str, subject: str, body: str) -> bool:
        """Returns True if the message was handed off successfully."""
        ...


class ConsoleNotificationChannel:
    """Default channel: logs the message instead of sending it. Never fails,
    never needs credentials -- keeps the app fully runnable offline."""

    def send(self, to: str, subject: str, body: str) -> bool:
        logger.info("notification_sent", channel="console", to=to, subject=subject, body=body)
        return True

    def send_detailed(self, to: str, subject: str, body: str) -> SendResult:
        self.send(to, subject, body)
        return SendResult(ok=True, demo=True)


def normalize_phone(raw: str) -> str:
    """Best-effort E.164: '+91-98765 43210' -> '+919876543210'; a bare 10-digit
    number gets SMS_DEFAULT_COUNTRY_CODE. Invalid input is returned cleaned but
    unchanged in shape so the caller can reject it."""
    s = re.sub(r"[^\d+]", "", raw or "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+") and len(s) == 10:
        s = settings.SMS_DEFAULT_COUNTRY_CODE + s
    return s


_E164 = re.compile(r"^\+\d{8,15}$")


class TwilioSmsChannel:
    """Real SMS through Twilio's REST API (no SDK dependency)."""

    URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"

    @staticmethod
    def configured() -> bool:
        return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN
                    and (settings.TWILIO_FROM_NUMBER or settings.TWILIO_MESSAGING_SERVICE_SID))

    def send(self, to: str, subject: str, body: str) -> bool:
        return self.send_detailed(to, subject, body).ok

    def send_detailed(self, to: str, subject: str, body: str) -> SendResult:
        number = normalize_phone(to)
        if not _E164.match(number):
            return SendResult(ok=False, error=f"invalid phone number '{to}'", retryable=False)
        data = {"To": number, "Body": body[:600]}
        if settings.TWILIO_MESSAGING_SERVICE_SID:
            data["MessagingServiceSid"] = settings.TWILIO_MESSAGING_SERVICE_SID
        else:
            data["From"] = settings.TWILIO_FROM_NUMBER
        try:
            resp = httpx.post(self.URL.format(sid=settings.TWILIO_ACCOUNT_SID), data=data, timeout=10.0,
                              auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN))
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=f"network error: {e}", retryable=True)
        try:
            j = resp.json()
        except ValueError:
            j = {}
        if resp.status_code in (200, 201):
            return SendResult(ok=True, provider_id=j.get("sid"))
        msg = f"Twilio {resp.status_code}" + (f" / {j.get('code')}" if j.get("code") else "") + \
              (f": {j.get('message')}" if j.get("message") else "")
        # 429 and 5xx are transient; other 4xx (bad/unverified number, auth) will not fix themselves.
        return SendResult(ok=False, error=msg, retryable=resp.status_code == 429 or resp.status_code >= 500)


class SmtpEmailChannel:
    """Real email over SMTP with STARTTLS -- Gmail by default (see the SMTP_*
    settings). Uses the standard library only; the password is never logged."""

    @staticmethod
    def configured() -> bool:
        return bool(settings.SMTP_HOST and settings.SMTP_USERNAME and settings.SMTP_PASSWORD)

    def send(self, to: str, subject: str, body: str) -> bool:
        return self.send_detailed(to, subject, body).ok

    def send_detailed(self, to: str, subject: str, body: str) -> SendResult:
        msg = EmailMessage()
        msg["From"] = formataddr((settings.SMTP_FROM_NAME, settings.SMTP_FROM or settings.SMTP_USERNAME))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT,
                              timeout=settings.SMTP_TIMEOUT_SECONDS) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(msg)
        except smtplib.SMTPAuthenticationError as e:
            # Wrong address / app password, or 2-Step Verification off: retrying won't help.
            logger.error("email_send_failed", reason="authentication", code=e.smtp_code, to=to)
            return SendResult(ok=False, error=f"SMTP authentication failed ({e.smtp_code})", retryable=False)
        except smtplib.SMTPRecipientsRefused:
            logger.error("email_send_failed", reason="recipient refused", to=to)
            return SendResult(ok=False, error="recipient refused", retryable=False)
        except (smtplib.SMTPException, OSError) as e:
            logger.error("email_send_failed", reason=type(e).__name__, to=to)
            return SendResult(ok=False, error=f"{type(e).__name__}: {e}", retryable=True)
        logger.info("email_sent", to=to, subject=subject)
        return SendResult(ok=True)


class UndeliveredEmailChannel:
    """Production with no SMTP configured: say an email could not be sent, but
    never write its body (it may hold a password-reset code) to the logs."""

    def send(self, to: str, subject: str, body: str) -> bool:
        logger.warning("email_not_configured", to=to, subject=subject,
                       hint="set SMTP_USERNAME and SMTP_PASSWORD to send email")
        return False


_channels: dict[str, NotificationChannel] = {
    "console": ConsoleNotificationChannel(),
}


def get_channel() -> NotificationChannel:
    if settings.NOTIFICATION_CHANNEL == "twilio" and TwilioSmsChannel.configured():
        return _twilio
    return _channels.get(settings.NOTIFICATION_CHANNEL, _channels["console"])


def get_email_channel() -> NotificationChannel:
    """Where emails go: SMTP when configured; otherwise the console log in
    development/tests, and nowhere (with a warning) in production."""
    if SmtpEmailChannel.configured():
        return _smtp
    if settings.is_production:
        return _undelivered_email
    return _channels["console"]


_twilio = TwilioSmsChannel()
_smtp = SmtpEmailChannel()
_undelivered_email = UndeliveredEmailChannel()


# =============================================================== Web Push (VAPID)
_vapid_lock = threading.Lock()
_vapid_cache: dict[str, str] = {}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_vapid_keys() -> dict[str, str]:
    """A fresh VAPID key pair: `public` is the applicationServerKey the browser
    needs, `private` is the raw 32-byte secret (both URL-safe base64)."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid

    v = Vapid()
    v.generate_keys()
    priv = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    pub = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"public": _b64(pub), "private": _b64(priv)}


def vapid_keys() -> tuple[dict[str, str] | None, str]:
    """(keys, mode). mode: 'configured' | 'auto-generated (dev)' | 'disabled'."""
    if not settings.PUSH_ENABLED:
        return None, "disabled"
    if settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY:
        return {"public": settings.VAPID_PUBLIC_KEY, "private": settings.VAPID_PRIVATE_KEY}, "configured"
    if settings.is_production:
        return None, "disabled"           # production never invents a signing key
    import os
    with _vapid_lock:
        if not _vapid_cache:
            path = os.environ.get("VAPID_DEV_KEY_FILE", ".vapid_keys.json")
            try:
                with open(path, encoding="utf-8") as f:
                    _vapid_cache.update(json.load(f))
            except (OSError, ValueError):
                _vapid_cache.update(generate_vapid_keys())
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(_vapid_cache, f)
                except OSError:
                    logger.warning("vapid_dev_key_not_persisted")
    return dict(_vapid_cache), "auto-generated (dev)"


def _webpush(**kwargs):  # indirection so tests can stub the network call
    from pywebpush import webpush
    return webpush(**kwargs)


FCM_CHANNEL_ID = "sos_alerts"   # must match the channel the Android app creates
_FCM_APP_NAME = "musafir"
_fcm_lock = threading.Lock()


def fcm_configured() -> bool:
    return bool(settings.PUSH_ENABLED and settings.FCM_CREDENTIALS_FILE
                and os.path.isfile(settings.FCM_CREDENTIALS_FILE))


def _fcm_app():
    import firebase_admin
    from firebase_admin import credentials

    with _fcm_lock:
        try:
            return firebase_admin.get_app(_FCM_APP_NAME)
        except ValueError:
            return firebase_admin.initialize_app(
                credentials.Certificate(settings.FCM_CREDENTIALS_FILE), name=_FCM_APP_NAME)


def _fcm_send(*, token: str, title: str, body: str, data: dict[str, str], urgent: bool) -> str:
    """Indirection so tests can stub the network call. Returns FCM's message id."""
    from firebase_admin import messaging

    message = messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body),
        data=data,
        android=messaging.AndroidConfig(
            priority="high" if urgent else "normal",
            ttl=86400,
            notification=messaging.AndroidNotification(channel_id=FCM_CHANNEL_ID, tag=data.get("tag")),
        ),
    )
    return messaging.send(message, app=_fcm_app())


def _send_fcm(sub: PushSubscription, payload: dict, urgent: bool) -> SendResult:
    from firebase_admin import exceptions as fb_exc
    from firebase_admin import messaging

    if not fcm_configured():
        return SendResult(ok=False, error="FCM is not configured (no FCM_CREDENTIALS_FILE)", retryable=False)
    data = {k: str(v) for k, v in payload.items() if v is not None}
    try:
        msg_id = _fcm_send(token=sub.endpoint, title=payload.get("title", ""), body=payload.get("body", ""),
                           data=data, urgent=urgent)
        return SendResult(ok=True, provider_id=msg_id)
    except (messaging.UnregisteredError, messaging.SenderIdMismatchError) as e:
        return SendResult(ok=False, error=f"subscription expired (fcm {type(e).__name__})", retryable=False)
    except fb_exc.InvalidArgumentError as e:
        if "registration token" in str(e).lower():
            return SendResult(ok=False, error="subscription expired (fcm invalid token)", retryable=False)
        return SendResult(ok=False, error=f"fcm rejected the message: {str(e)[:160]}", retryable=False)
    except Exception as e:  # noqa: BLE001 -- quota, outage, network: try again later
        return SendResult(ok=False, error=f"fcm {type(e).__name__}: {str(e)[:160]}", retryable=True)


def _send_push(sub: PushSubscription, payload: dict, urgent: bool) -> SendResult:
    if sub.kind == "fcm":
        return _send_fcm(sub, payload, urgent)

    from py_vapid import Vapid
    from pywebpush import WebPushException

    keys, mode = vapid_keys()
    if not keys:
        return SendResult(ok=False, error="push is not configured (no VAPID keys)", retryable=False)
    try:
        _webpush(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps(payload),
            vapid_private_key=Vapid.from_raw(keys["private"].encode()),
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=86400,
            headers={"Urgency": "high" if urgent else "normal"},
            timeout=10,
        )
        return SendResult(ok=True)
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):          # subscription gone (uninstalled / permission revoked)
            return SendResult(ok=False, error=f"subscription expired ({status})", retryable=False)
        return SendResult(ok=False, error=f"push service {status or 'error'}: {str(e)[:160]}",
                          retryable=status is None or status == 429 or status >= 500)
    except Exception as e:  # noqa: BLE001 -- network/crypto errors: try again later
        return SendResult(ok=False, error=f"{type(e).__name__}: {str(e)[:160]}", retryable=True)


# =============================================================== pipeline: enqueue
_OUTBOX = "notif_outbox"


def _mask_phone(raw: str) -> str:
    if "@" in (raw or ""):                               # a guardian may give an email address
        local, _, domain = raw.partition("@")
        return f"{local[:1]}•••@{domain}"
    d = re.sub(r"\D", "", raw or "")
    return f"•••• {d[-4:]}" if len(d) >= 4 else "•••"


def _enqueue(db: Session, *, event_name: str, channel: str, rtype: str, label: str, key: str,
             title: str, body: str, url: str = "", severity: str = "info", address: str | None = None,
             sub: PushSubscription | None = None, incident: Incident | None = None,
             tourist: Tourist | None = None, status: str = "queued", error: str | None = None,
             demo: bool = False, payload: dict | None = None) -> NotificationLog | None:
    """Add one NotificationLog (inside the caller's transaction). Returns None
    when it is a duplicate (a 'suppressed' row is recorded instead)."""
    scope = f"i{incident.id}" if incident else f"t{tourist.id}" if tourist else "x"
    dedupe_key = f"{event_name}:{scope}:{channel}:{key}"
    common = dict(
        event_type=event_name.split(":")[0], channel=channel, recipient_type=rtype, recipient_label=label,
        address=address, subscription_id=sub.id if sub else None,
        incident_id=incident.id if incident else None,
        tourist_id=(tourist.id if tourist else (incident.tourist_id if incident else None)),
        title=title, body=body, url=url, severity=severity,
        payload=json.dumps({"event": event_name, **(payload or {})}),
    )
    try:
        with db.begin_nested():
            log = NotificationLog(**common, status=status, last_error=error, demo=demo, dedupe_key=dedupe_key)
            db.add(log)
            db.flush()
    except IntegrityError:
        db.add(NotificationLog(**common, status="suppressed", dedupe_key=None,
                               last_error="duplicate: this exact notification was already sent"))
        db.flush()
        logger.info("notification_duplicate_suppressed", dedupe_key=dedupe_key)
        return None
    if status == "queued":
        db.info.setdefault(_OUTBOX, []).append(log.id)
    return log


def _abs(path: str) -> str:
    return settings.PUBLIC_BASE_URL.rstrip("/") + path


def _place(db: Session, lat: float | None, lng: float | None) -> str:
    if lat is None or lng is None:
        return "unknown location"
    from app.models.zone import Zone
    from app.services.geo import zones_containing_point
    inside = zones_containing_point(lat, lng, db.query(Zone).all())
    return f"{inside[0].name} ({lat:.4f}, {lng:.4f})" if inside else f"{lat:.5f}, {lng:.5f}"


def _is_phone(value: str | None) -> bool:
    return bool(value) and "@" not in value and len(re.sub(r"\D", "", value)) >= 7


def _sms_blocked(tourist: Tourist | None, phone: str) -> str | None:
    """Why this SMS must not be sent (None = allowed)."""
    if tourist is not None and tourist.digital_id.startswith("SIM-"):
        return "simulated tourist: SMS is never sent for simulation data"
    if isinstance(get_channel(), TwilioSmsChannel) and not _is_phone(phone):
        return "contact is not a phone number: SMS not sent"
    allow = [normalize_phone(p) for p in settings.SMS_ALLOWLIST.split(",") if p.strip()]
    if allow and normalize_phone(phone) not in allow:
        return "not in SMS_ALLOWLIST (test mode): not sent"
    return None


def _enqueue_sms(db, *, event_name, rtype, label, phone, title, body, severity, incident, tourist, url=""):
    reason = _sms_blocked(tourist, phone)
    return _enqueue(
        db, event_name=event_name, channel="sms", rtype=rtype, label=f"{label} · {_mask_phone(phone)}",
        key=re.sub(r"\D", "", phone) or phone, title=title, body=body, url=url, severity=severity,
        address=phone, incident=incident, tourist=tourist,
        status="suppressed" if reason else "queued", error=reason)


def _push_subs(db: Session, *, user_roles=None, user_ids=None, tourist_id=None, unit_id=None,
               guardian_id=None) -> list[tuple[PushSubscription, str]]:
    """(subscription, label) pairs for active subscriptions matching a filter."""
    q = db.query(PushSubscription).filter(PushSubscription.active.is_(True))
    out: list[tuple[PushSubscription, str]] = []
    if guardian_id is not None:
        g = db.get(TripGuardian, guardian_id)
        return [(s, f"Guardian {g.guardian_name if g else ''}".strip())
                for s in q.filter(PushSubscription.guardian_id == guardian_id).all()]
    uq = db.query(User)
    if user_roles:
        uq = uq.filter(User.role.in_(user_roles))
    if tourist_id is not None:
        uq = uq.filter(User.tourist_id == tourist_id)
    if unit_id is not None:
        uq = uq.filter(User.unit_id == unit_id)
    if user_ids is not None:
        uq = uq.filter(User.id.in_(user_ids))
    users = {u.id: u for u in uq.all()}
    if not users:
        return out
    for s in q.filter(PushSubscription.user_id.in_(users)).all():
        u = users[s.user_id]
        out.append((s, f"{u.role.title()} {u.full_name}"))
    return out


def _push_fanout(db, subs, *, event_name, rtype, title, body, url_for, severity, incident=None,
                 tourist=None, payload=None):
    for sub, label in subs:
        _enqueue(db, event_name=event_name, channel="push", rtype=rtype, label=label, key=f"s{sub.id}",
                 title=title, body=body, url=url_for(sub), severity=severity, sub=sub,
                 incident=incident, tourist=tourist, payload=payload)


def _admin_url(inc: Incident | None) -> str:
    return f"/admin/incidents?incident={inc.id}" if inc else "/admin/notifications"


def _responder_url(inc: Incident | None) -> str:
    return f"/responder?incident={inc.id}" if inc else "/responder"


def _guardians(db: Session, tourist_id: int) -> list[TripGuardian]:
    return db.query(TripGuardian).filter(TripGuardian.tourist_id == tourist_id,
                                         TripGuardian.revoked.is_(False)).all()


def _contacts(tourist: Tourist) -> list[dict]:
    try:
        return [c for c in json.loads(tourist.emergency_contacts or "[]") if (c.get("phone") or "").strip()]
    except ValueError:
        return []


# ------------------------------------------------------------------ the events
def notify_sos(db: Session, inc: Incident, tourist: Tourist, *, message: str, silent: bool,
               lat: float, lng: float, unit_name: str | None) -> None:
    """SOS -> push to tourist (not if silent), guardians, the assigned unit's
    responders and the control room; SMS to emergency contacts and guardians."""
    place = _place(db, lat, lng)
    maps = f"https://maps.google.com/?q={lat:.5f},{lng:.5f}"
    status_txt = f"dispatched to {unit_name}" if unit_name else "awaiting dispatch"
    hush = " Silent SOS: do not call or approach visibly." if silent else ""
    common = dict(severity="critical", incident=inc, tourist=tourist)

    # --- the tourist's own device: reassurance, never for a silent/duress SOS
    if not silent:
        _push_fanout(db, _push_subs(db, tourist_id=tourist.id, user_roles=["tourist"]),
                     event_name="sos", rtype="tourist", title="SOS received",
                     body=f"Responders have been notified. {('Help dispatched: ' + unit_name + '. ') if unit_name else ''}Stay where you are.",
                     url_for=lambda s: "/app", **common)

    # --- control room + assigned responders
    resp_title = f"🚨 SOS ALERT — {tourist.full_name}"
    resp_body = (f"{tourist.full_name} has triggered an emergency SOS. Location: {place}. "
                 f"Status: {status_txt}.{hush}")
    _push_fanout(db, _push_subs(db, user_roles=["admin"]), event_name="sos", rtype="admin",
                 title=resp_title, body=resp_body, url_for=lambda s: _admin_url(inc), **common)
    if inc.assigned_unit_id:
        _push_fanout(db, _push_subs(db, user_roles=["responder"], unit_id=inc.assigned_unit_id),
                     event_name="sos", rtype="responder", title=resp_title, body=resp_body,
                     url_for=lambda s: _responder_url(inc), **common)

    # --- family: guardians (push if they subscribed, SMS if their contact is a number)
    for g in _guardians(db, tourist.id):
        link = f"/guardian/{g.token}"
        g_body = (f"{tourist.full_name}, whose trip you follow, has triggered an SOS near {place}.{hush} "
                  f"Open the live status.")
        _push_fanout(db, _push_subs(db, guardian_id=g.id), event_name="sos", rtype="guardian",
                     title=f"🚨 SOS — {tourist.full_name}", body=g_body, url_for=lambda s, link=link: link, **common)
        if (g.guardian_contact or "").strip():
            _enqueue_sms(db, event_name="sos", rtype="guardian", label=f"Guardian {g.guardian_name}",
                         phone=g.guardian_contact, title="MUSAFIR SOS", severity="critical",
                         body=f"MUSAFIR SOS: {tourist.full_name} has triggered an SOS near {place}.{hush} "
                              f"Live status: {_abs(link)}",
                         incident=inc, tourist=tourist, url=link)

    # --- emergency contacts: SMS
    for c in _contacts(tourist):
        _enqueue_sms(db, event_name="sos", rtype="emergency_contact", label=c.get("name") or "Emergency contact",
                     phone=c["phone"], title="MUSAFIR SOS", severity="critical",
                     body=(f"MUSAFIR SOS: {tourist.full_name} has triggered an SOS. Last known location: "
                           f"{lat:.5f}, {lng:.5f} ({maps}). Message: {message}.{hush}"),
                     incident=inc, tourist=tourist)


_UPDATE_TEXT = {
    "acknowledged": ("Your alert was acknowledged", "The control room has seen your SOS and is working on it."),
    "dispatched": ("Help is on the way", "A responder unit has been dispatched to you."),
    "resolved": ("Incident resolved", "Your SOS case has been closed. Stay safe."),
}


def notify_incident_update(db: Session, inc: Incident, status: str) -> None:
    """Status change on an incident. SOS incidents keep the tourist and family
    informed; a manual dispatch tells the assigned unit's responders."""
    tourist = db.get(Tourist, inc.tourist_id) if inc.tourist_id else None
    ev = f"incident_update:{status}"
    if status == "dispatched" and inc.assigned_unit_id:
        _push_fanout(db, _push_subs(db, user_roles=["responder"], unit_id=inc.assigned_unit_id),
                     event_name=ev, rtype="responder", title=f"Dispatch — incident #{inc.id}",
                     body=f"{inc.type.upper()} incident assigned to your unit: {inc.description[:140]}",
                     url_for=lambda s: _responder_url(inc), severity=inc.severity, incident=inc, tourist=tourist)
    if inc.type != "sos" or tourist is None or status not in _UPDATE_TEXT:
        return
    title, body = _UPDATE_TEXT[status]
    if not inc.silent:       # a silent SOS must not light up the tourist's screen
        _push_fanout(db, _push_subs(db, tourist_id=tourist.id, user_roles=["tourist"]), event_name=ev,
                     rtype="tourist", title=title, body=body, url_for=lambda s: "/app",
                     severity="high", incident=inc, tourist=tourist)
    for g in _guardians(db, tourist.id):
        link = f"/guardian/{g.token}"
        g_title = f"{tourist.full_name}: {title.lower()}"
        _push_fanout(db, _push_subs(db, guardian_id=g.id), event_name=ev, rtype="guardian", title=g_title,
                     body=body, url_for=lambda s, link=link: link, severity="high", incident=inc, tourist=tourist)
        if status == "resolved" and (g.guardian_contact or "").strip():
            _enqueue_sms(db, event_name=ev, rtype="guardian", label=f"Guardian {g.guardian_name}",
                         phone=g.guardian_contact, title="MUSAFIR update", severity="high",
                         body=f"MUSAFIR: the SOS case for {tourist.full_name} has been resolved. {_abs(link)}",
                         incident=inc, tourist=tourist, url=link)
    if status == "resolved":
        for c in _contacts(tourist):
            _enqueue_sms(db, event_name=ev, rtype="emergency_contact", label=c.get("name") or "Emergency contact",
                         phone=c["phone"], title="MUSAFIR update", severity="high",
                         body=f"MUSAFIR: the SOS case for {tourist.full_name} has been resolved.",
                         incident=inc, tourist=tourist)


def notify_incident_opened(db: Session, inc: Incident, tourist: Tourist | None) -> None:
    """A CRITICAL non-SOS incident (SOS has its own richer fan-out) -> control room push."""
    if inc.severity != "critical" or inc.type == "sos":
        return
    who = tourist.full_name if tourist else "a tourist"
    _push_fanout(db, _push_subs(db, user_roles=["admin"]), event_name="critical_incident", rtype="admin",
                 title=f"Critical incident #{inc.id}", body=f"{inc.type.replace('_', ' ').title()} — {who}: {inc.description[:140]}",
                 url_for=lambda s: _admin_url(inc), severity="critical", incident=inc, tourist=tourist)


def notify_escalation(db: Session, inc: Incident, tourist: Tourist, stage: str) -> None:
    """The control room did not acknowledge in time -> contacts get an SMS (the
    original behaviour) and the control room / responders are pushed again."""
    ev = f"escalation:{stage}"
    text = (f"Incident #{inc.id} for {tourist.full_name} has not been "
            f"acknowledged by the control room. Escalating.")
    if stage == "emergency_contact":
        for c in _contacts(tourist):
            _enqueue_sms(db, event_name=ev, rtype="emergency_contact", label=c.get("name") or "Emergency contact",
                         phone=c["phone"], title=f"Unacknowledged incident for {tourist.full_name}",
                         body=text, severity="critical", incident=inc, tourist=tourist)
    title = f"⚠ ESCALATED — incident #{inc.id} unacknowledged"
    _push_fanout(db, _push_subs(db, user_roles=["admin"]), event_name=ev, rtype="admin", title=title,
                 body=text, url_for=lambda s: _admin_url(inc), severity="critical", incident=inc, tourist=tourist)
    if inc.assigned_unit_id:
        _push_fanout(db, _push_subs(db, user_roles=["responder"], unit_id=inc.assigned_unit_id),
                     event_name=ev, rtype="responder", title=title, body=text,
                     url_for=lambda s: _responder_url(inc), severity="critical", incident=inc, tourist=tourist)


def send_test(db: Session, user: User, channel: str, to: str | None = None) -> list[NotificationLog]:
    """Admin 'send a test' -- push to the caller's own subscriptions, or SMS to a number."""
    stamp = utc_now().strftime("%H:%M:%S")
    logs: list[NotificationLog] = []
    if channel == "push":
        for sub, label in _push_subs(db, user_ids=[user.id]):
            lg = _enqueue(db, event_name=f"test:{stamp}", channel="push", rtype=user.role, label=label,
                          key=f"s{sub.id}", title="MUSAFIR test notification",
                          body="If you can read this, push notifications reach this device.",
                          url=_admin_url(None), severity="info", sub=sub)
            if lg:
                logs.append(lg)
    else:
        reason = _sms_blocked(None, to or "")
        lg = _enqueue(db, event_name=f"test:{stamp}", channel="sms", rtype="admin",
                      label=f"Test SMS · {_mask_phone(to or '')}", key=re.sub(r"\D", "", to or ""),
                      title="MUSAFIR test", body="MUSAFIR test message: SMS delivery is working.",
                      address=to, severity="info", status="suppressed" if reason else "queued", error=reason)
        if lg:
            logs.append(lg)
    return logs


# =============================================================== pipeline: deliver
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="notify")


def _sync_mode() -> bool:
    return settings.NOTIFICATIONS_SYNC or settings.is_test


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=settings.NOTIFICATION_RETRY_BASE_SECONDS * (4 ** max(0, attempts - 1)))


def _deliver_one(db: Session, log: NotificationLog) -> None:
    """Send one log row and record the outcome. Network I/O happens between two commits."""
    log.status, log.attempts = "sending", (log.attempts or 0) + 1
    db.commit()

    if log.channel == "push":
        sub = db.get(PushSubscription, log.subscription_id) if log.subscription_id else None
        if sub is None or not sub.active:
            res = SendResult(ok=False, error="subscription no longer active", retryable=False)
        else:
            payload = {"title": log.title, "body": log.body, "url": log.url, "severity": log.severity,
                       "incident_id": log.incident_id, "tag": log.dedupe_key or f"n{log.id}",
                       "event": log.event_type, "ts": int(utc_now().timestamp() * 1000)}
            res = _send_push(sub, payload, urgent=log.severity in ("critical", "high"))
            if res.ok:
                sub.failure_count, sub.last_success_at = 0, utc_now()
            else:
                sub.failure_count = (sub.failure_count or 0) + 1
                if "expired" in (res.error or ""):
                    sub.active = False
    else:
        ch = get_channel()
        if isinstance(ch, (ConsoleNotificationChannel, TwilioSmsChannel)):
            res = ch.send_detailed(log.address or "", log.title, log.body)
        else:                                             # a bare send()-only channel (tests / custom backends)
            ok = bool(ch.send(to=log.address or "", subject=log.title, body=log.body))
            res = SendResult(ok=ok, retryable=not ok, error=None if ok else "channel reported failure",
                             demo=isinstance(ch, ConsoleNotificationChannel))

    if res.ok:
        log.status = "simulated" if res.demo else "sent"
        log.demo, log.provider_id, log.sent_at, log.last_error, log.next_retry_at = (
            res.demo, res.provider_id, utc_now(), None, None)
    elif res.retryable and log.attempts < settings.NOTIFICATION_MAX_ATTEMPTS:
        log.status, log.last_error = "retrying", (res.error or "")[:500]
        log.next_retry_at = utc_now() + _backoff(log.attempts)
    else:
        log.status, log.last_error, log.next_retry_at = "failed", (res.error or "")[:500], None
    db.commit()
    logger.info("notification_delivery", id=log.id, channel=log.channel, status=log.status,
                attempts=log.attempts, error=log.last_error)


def process_ids(db: Session, ids: list[int]) -> None:
    for i in ids:
        log = db.get(NotificationLog, i)
        if log is None or log.status not in ("queued", "retrying", "sending"):
            continue
        try:
            _deliver_one(db, log)
        except Exception:  # noqa: BLE001 -- one bad row must not strand the rest
            db.rollback()
            logger.exception("notification_delivery_crashed", id=i)


def _run_in_thread(ids: list[int]) -> None:
    db = SessionLocal()
    try:
        process_ids(db, ids)
    finally:
        db.close()


def _submit(session: Session, ids: list[int]) -> None:
    if _sync_mode():
        # after_commit forbids SQL on the committing session; a sibling session on
        # the same bind sees the committed rows (and is the test database in tests).
        s = Session(bind=session.get_bind(), autoflush=False, expire_on_commit=False)
        try:
            process_ids(s, ids)
        finally:
            s.close()
    else:
        _executor.submit(_run_in_thread, ids)


@event.listens_for(Session, "after_commit")
def _dispatch_after_commit(session: Session) -> None:
    # Releasing a SAVEPOINT (used by _enqueue's duplicate guard) also fires
    # after_commit while the real transaction is still open -- deliver only once
    # the outermost commit has actually happened.
    if session.in_nested_transaction():
        return
    ids = session.info.pop(_OUTBOX, None)
    if ids:
        _submit(session, ids)


@event.listens_for(Session, "after_rollback")
def _discard_on_rollback(session: Session) -> None:
    session.info.pop(_OUTBOX, None)        # an SOS that rolled back must notify nobody


def retry_due(db: Session) -> int:
    """Scheduler tick: re-send retrying rows whose backoff has elapsed (and rows
    stuck in 'sending' after a crash). Returns how many were attempted."""
    now = utc_now()
    rows = (db.query(NotificationLog)
            .filter(((NotificationLog.status == "retrying") & (NotificationLog.next_retry_at <= now))
                    | ((NotificationLog.status == "sending")
                       & (NotificationLog.created_at <= now - timedelta(minutes=2))))
            .order_by(NotificationLog.id).limit(50).all())
    ids = [r.id for r in rows]
    if ids:
        process_ids(db, ids)
    return len(ids)


def retry_now(db: Session, log: NotificationLog) -> None:
    """Admin 'retry' on a failed row: reset and send again (inline, bypassing dedupe)."""
    log.status, log.attempts, log.last_error, log.next_retry_at = "queued", 0, None, None
    db.commit()
    process_ids(db, [log.id])


# =============================================================== admin reporting
def config_status() -> dict:
    keys, mode = vapid_keys()
    twilio_ok = TwilioSmsChannel.configured()
    live = settings.NOTIFICATION_CHANNEL == "twilio" and twilio_ok
    return {
        "push": {"enabled": keys is not None, "mode": mode, "public_key_available": keys is not None},
        "fcm": {"enabled": fcm_configured()},
        "sms": {
            "channel": settings.NOTIFICATION_CHANNEL, "twilio_configured": twilio_ok, "live": live,
            "mode": "twilio (real SMS)" if live else "demo (console only — nothing is texted)",
            "allowlist_active": bool(settings.SMS_ALLOWLIST.strip()),
        },
        "retry": {"max_attempts": settings.NOTIFICATION_MAX_ATTEMPTS,
                  "base_backoff_s": settings.NOTIFICATION_RETRY_BASE_SECONDS},
    }


def mask_log(log: NotificationLog) -> dict:
    """Serialisable row; never exposes the raw phone number."""
    return {
        "id": log.id, "created_at": log.created_at.isoformat(), "event_type": log.event_type,
        "channel": log.channel, "recipient_type": log.recipient_type, "recipient": log.recipient_label,
        "incident_id": log.incident_id, "tourist_id": log.tourist_id, "title": log.title, "body": log.body,
        "url": log.url, "severity": log.severity, "status": log.status, "attempts": log.attempts,
        "last_error": log.last_error, "next_retry_at": log.next_retry_at.isoformat() if log.next_retry_at else None,
        "provider_id": log.provider_id, "demo": log.demo,
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
    }
