"""Push & SMS alerts API. See services/notifications.py.

  * browsers register for Web Push (tourists, responders, admins by login;
    guardians by their share-link token -- they have no account), and the
    Android app registers its FCM token,
  * admins read the delivery history, retry failures and send tests.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.session import get_db
from app.models.guardian import TripGuardian
from app.models.notification import NotificationLog, PushSubscription
from app.models.user import User
from app.services import apns, audit, notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


class PushKeys(BaseModel):
    p256dh: str = Field(..., max_length=200)
    auth: str = Field(..., max_length=100)


class SubscribeRequest(BaseModel):
    endpoint: str = Field(..., max_length=2000)
    keys: PushKeys
    user_agent: str = Field("", max_length=300)


class GuardianSubscribeRequest(SubscribeRequest):
    token: str = Field(..., max_length=200)


class FcmSubscribeRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=2000)
    user_agent: str = Field("", max_length=300)


class ApnsSubscribeRequest(BaseModel):
    # An APNs device token is 32 bytes, sent as 64 hex characters.
    token: str = Field(..., min_length=64, max_length=200, pattern="^[0-9a-fA-F]+$")
    user_agent: str = Field("", max_length=300)


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(..., max_length=2000)
    token: str | None = Field(None, max_length=200)


class TestRequest(BaseModel):
    channel: str = Field("push", pattern="^(push|sms)$")
    to: str | None = Field(None, max_length=32)


def _upsert(db: Session, *, endpoint: str, kind: str, keys: PushKeys | None, user_agent: str,
            user: User | None, guardian: TripGuardian | None):
    sub = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if sub is None:
        sub = PushSubscription(endpoint=endpoint)
        db.add(sub)
    sub.kind = kind
    sub.p256dh, sub.auth = (keys.p256dh, keys.auth) if keys else (None, None)
    sub.active, sub.failure_count = True, 0
    sub.user_agent = user_agent[:300]
    # A device belongs to exactly one owner: re-subscribing as someone else moves it.
    sub.user_id = user.id if user else None
    sub.guardian_id = guardian.id if guardian else None
    sub.role = user.role if user else "guardian"
    db.commit()
    return sub


def _require_push_consent(db: Session, user: User) -> None:
    """A tourist who withdrew notification consent (Privacy settings, services/privacy_consent.py)
    can't register a device for push -- browser, Android or iOS alike."""
    if user.tourist_id is None:
        return
    from app.services.privacy_consent import is_withdrawn

    if is_withdrawn(db, user.tourist_id, "notifications"):
        raise HTTPException(status_code=403,
                            detail="Push notification consent is withdrawn. Grant it in Privacy settings first.")


@router.get("/push/public-key")
def public_key():
    """The VAPID public key a browser needs to subscribe (public by design)."""
    keys, mode = notifications.vapid_keys()
    return {"enabled": keys is not None, "mode": mode, "public_key": keys["public"] if keys else None,
            "fcm_enabled": notifications.fcm_configured(), "apns_enabled": apns.configured()}


@router.post("/push/subscribe", status_code=201)
def subscribe(payload: SubscribeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if notifications.vapid_keys()[0] is None:
        raise HTTPException(status_code=503, detail="Push notifications are not configured on this server")
    _require_push_consent(db, user)
    sub = _upsert(db, endpoint=payload.endpoint, kind="webpush", keys=payload.keys,
                  user_agent=payload.user_agent, user=user, guardian=None)
    return {"id": sub.id, "role": sub.role}


@router.post("/push/subscribe-fcm", status_code=201)
def subscribe_fcm(payload: FcmSubscribeRequest, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """The Android app registers its FCM token. Unsubscribe with the token as `endpoint`."""
    if not notifications.fcm_configured():
        raise HTTPException(status_code=503, detail="App push notifications are not configured on this server")
    _require_push_consent(db, user)
    sub = _upsert(db, endpoint=payload.token, kind="fcm", keys=None,
                  user_agent=payload.user_agent, user=user, guardian=None)
    return {"id": sub.id, "role": sub.role}


@router.post("/push/subscribe-apns", status_code=201)
def subscribe_apns(payload: ApnsSubscribeRequest, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """The iOS app registers its APNs device token. Unsubscribe with the token as `endpoint`."""
    if not apns.configured():
        raise HTTPException(status_code=503, detail="App push notifications are not configured on this server")
    _require_push_consent(db, user)
    sub = _upsert(db, endpoint=payload.token.lower(), kind="apns", keys=None,
                  user_agent=payload.user_agent, user=user, guardian=None)
    return {"id": sub.id, "role": sub.role}


@router.post("/push/unsubscribe")
def unsubscribe(payload: UnsubscribeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sub = db.query(PushSubscription).filter(PushSubscription.endpoint == payload.endpoint,
                                            PushSubscription.user_id == user.id).first()
    if sub:
        sub.active = False
        db.commit()
    return {"ok": True}


@router.post("/push/guardian-subscribe", status_code=201)
def guardian_subscribe(payload: GuardianSubscribeRequest, db: Session = Depends(get_db)):
    """A guardian (family member) opts in from their share page -- the unguessable
    share token is their credential, exactly like the page itself."""
    if notifications.vapid_keys()[0] is None:
        raise HTTPException(status_code=503, detail="Push notifications are not configured on this server")
    g = db.query(TripGuardian).filter(TripGuardian.token == payload.token, TripGuardian.revoked.is_(False)).first()
    if g is None:
        raise HTTPException(status_code=404, detail="This share link is not valid")
    sub = _upsert(db, endpoint=payload.endpoint, kind="webpush", keys=payload.keys,
                  user_agent=payload.user_agent, user=None, guardian=g)
    return {"id": sub.id, "role": sub.role}


@router.post("/push/guardian-unsubscribe")
def guardian_unsubscribe(payload: UnsubscribeRequest, db: Session = Depends(get_db)):
    g = db.query(TripGuardian).filter(TripGuardian.token == (payload.token or "")).first()
    if g:
        sub = db.query(PushSubscription).filter(PushSubscription.endpoint == payload.endpoint,
                                                PushSubscription.guardian_id == g.id).first()
        if sub:
            sub.active = False
            db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ admin
@router.get("/summary")
def summary(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    by_status = dict(db.query(NotificationLog.status, func.count()).group_by(NotificationLog.status).all())
    by_channel = dict(db.query(NotificationLog.channel, func.count()).group_by(NotificationLog.channel).all())
    subs = dict(db.query(PushSubscription.role, func.count()).filter(PushSubscription.active.is_(True))
                .group_by(PushSubscription.role).all())
    return {
        "total": sum(by_status.values()), "by_status": by_status, "by_channel": by_channel,
        "subscribers": {"total": sum(subs.values()), "by_role": subs},
        "config": notifications.config_status(),
    }


@router.get("/logs")
def logs(channel: str | None = None, status: str | None = None, event: str | None = None,
         incident_id: int | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
         db: Session = Depends(get_db), _: User = Depends(require_admin)):
    q = db.query(NotificationLog)
    if channel:
        q = q.filter(NotificationLog.channel == channel)
    if status:
        q = q.filter(NotificationLog.status == status)
    if event:
        q = q.filter(NotificationLog.event_type == event)
    if incident_id is not None:
        q = q.filter(NotificationLog.incident_id == incident_id)
    total = q.count()
    rows = q.order_by(NotificationLog.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [notifications.mask_log(r) for r in rows]}


@router.post("/logs/{log_id}/retry")
def retry(log_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    log = db.get(NotificationLog, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if log.status not in ("failed", "retrying", "simulated"):
        raise HTTPException(status_code=409, detail=f"A '{log.status}' notification cannot be retried")
    notifications.retry_now(db, log)
    audit.record(db, "notification_retry", actor=user.email, target=f"notification {log.id}")
    db.commit()
    db.refresh(log)
    return notifications.mask_log(log)


@router.post("/test")
def send_test(payload: TestRequest, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Send a test push to this admin's own devices, or a test SMS to a number."""
    if payload.channel == "sms" and not payload.to:
        raise HTTPException(status_code=422, detail="A phone number is required for a test SMS")
    created = notifications.send_test(db, user, payload.channel, payload.to)
    if not created:
        raise HTTPException(status_code=409,
                            detail="No push subscription for your account yet — enable notifications on this device first")
    ids = [c.id for c in created]
    audit.record(db, "notification_test", actor=user.email, target=payload.channel)
    db.commit()
    rows = db.query(NotificationLog).filter(NotificationLog.id.in_(ids)).all()
    return {"items": [notifications.mask_log(r) for r in rows]}
