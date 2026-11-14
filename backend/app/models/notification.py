"""Push subscriptions and the notification delivery log (services/notifications.py)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedString
from app.core.time import utc_now
from app.db.session import Base


class PushSubscription(Base):
    """One browser/device registered for push. Belongs to a logged-in user
    (tourist / responder / admin) or -- for a family member with only a share
    link and no account -- to a TripGuardian.

    kind "webpush": a browser subscription; `endpoint` is the push-service URL
    and p256dh/auth are its encryption keys. kind "fcm": the Android app;
    `endpoint` holds the FCM registration token and the keys are unused."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, default="webpush", server_default="webpush")
    endpoint: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    p256dh: Mapped[str | None] = mapped_column(String, nullable=True)
    auth: Mapped[str | None] = mapped_column(String, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    guardian_id: Mapped[int | None] = mapped_column(ForeignKey("trip_guardians.id"), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String, default="tourist")      # tourist|responder|admin|guardian
    user_agent: Mapped[str] = mapped_column(String, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NotificationLog(Base):
    """One row per (message, channel, recipient). The admin dashboard's
    notification history, the retry queue and the de-duplication ledger."""

    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)       # sos | incident_update | critical_incident | escalation | test
    channel: Mapped[str] = mapped_column(String, index=True)          # push | sms
    recipient_type: Mapped[str] = mapped_column(String)               # tourist|guardian|emergency_contact|responder|admin
    recipient_label: Mapped[str] = mapped_column(String, default="")  # human label, phone masked
    # Real delivery address, needed to retry. Encrypted at rest (phone number).
    address: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    subscription_id: Mapped[int | None] = mapped_column(ForeignKey("push_subscriptions.id"), nullable=True)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), index=True, nullable=True)
    tourist_id: Mapped[int | None] = mapped_column(ForeignKey("tourists.id"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String, default="")              # where a click should land
    severity: Mapped[str] = mapped_column(String, default="info")
    # queued | sending | sent | simulated | retrying | failed | suppressed
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    provider_id: Mapped[str | None] = mapped_column(String, nullable=True)   # Twilio message SID
    demo: Mapped[bool] = mapped_column(Boolean, default=False)        # console fallback, nothing really sent
    dedupe_key: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
