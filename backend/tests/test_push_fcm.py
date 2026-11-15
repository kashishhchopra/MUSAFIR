"""FCM push to the Android app (services/notifications.py `_send_fcm`, api/notifications.py)."""
import pytest
from firebase_admin import exceptions as fb_exc
from firebase_admin import messaging

from app.models.incident import Incident
from app.models.notification import NotificationLog, PushSubscription
from app.models.tourist import Tourist
from app.services import notifications
from app.services.monitoring import trigger_sos

TOKEN = "fcm-token-" + "x" * 40


class FakeFcm:
    """Stand-in for `_fcm_send`: records calls, can be told to fail."""

    def __init__(self):
        self.calls, self.failures = [], []

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.failures:
            raise self.failures.pop(0)
        return f"projects/test/messages/{len(self.calls)}"


@pytest.fixture
def fcm(monkeypatch):
    fake = FakeFcm()
    monkeypatch.setattr(notifications, "_fcm_send", fake)
    monkeypatch.setattr(notifications, "fcm_configured", lambda: True)
    return fake


@pytest.fixture
def app_sub(db, tourist_user):
    s = PushSubscription(kind="fcm", endpoint=TOKEN, user_id=tourist_user.id, role="tourist")
    db.add(s)
    db.commit()
    return s


def _sos(db, tourist_user):
    t = db.get(Tourist, tourist_user.tourist_id)
    r = trigger_sos(db, t, 26.1445, 91.7362, "Help")
    return db.get(Incident, r["incident_id"])


def test_sos_reaches_the_android_app_through_fcm(db, tourist_user, app_sub, fcm):
    inc = _sos(db, tourist_user)

    call = next(c for c in fcm.calls if c["token"] == TOKEN)
    assert call["urgent"] is True and "SOS" in call["title"]
    assert call["data"]["url"] == "/app" and call["data"]["incident_id"] == str(inc.id)
    assert all(isinstance(v, str) for v in call["data"].values())   # FCM data must be strings

    log = db.query(NotificationLog).filter_by(subscription_id=app_sub.id).one()
    assert log.status == "sent" and log.provider_id.startswith("projects/test/messages/")


def test_unregistered_token_deactivates_the_subscription(db, tourist_user, app_sub, fcm):
    fcm.failures.append(messaging.UnregisteredError("Requested entity was not found."))
    _sos(db, tourist_user)

    log = db.query(NotificationLog).filter_by(subscription_id=app_sub.id).one()
    db.refresh(app_sub)
    assert log.status == "failed" and "expired" in log.last_error
    assert app_sub.active is False


def test_invalid_token_is_treated_as_expired(db, tourist_user, app_sub, fcm):
    fcm.failures.append(fb_exc.InvalidArgumentError("The registration token is not a valid FCM registration token"))
    _sos(db, tourist_user)
    db.refresh(app_sub)
    assert app_sub.active is False


def test_transient_fcm_errors_are_retried(db, tourist_user, app_sub, fcm):
    fcm.failures.append(fb_exc.UnavailableError("FCM is temporarily unavailable"))
    _sos(db, tourist_user)

    log = db.query(NotificationLog).filter_by(subscription_id=app_sub.id).one()
    db.refresh(app_sub)
    assert log.status == "retrying" and log.next_retry_at is not None
    assert app_sub.active is True


def test_fcm_without_credentials_fails_clearly(db, tourist_user, app_sub, monkeypatch):
    monkeypatch.setattr(notifications, "fcm_configured", lambda: False)
    _sos(db, tourist_user)

    log = db.query(NotificationLog).filter_by(subscription_id=app_sub.id).one()
    assert log.status == "failed" and "not configured" in log.last_error


def test_fcm_configured_needs_an_existing_credentials_file(monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FCM_CREDENTIALS_FILE", "")
    assert notifications.fcm_configured() is False
    monkeypatch.setattr(settings, "FCM_CREDENTIALS_FILE", str(tmp_path / "missing.json"))
    assert notifications.fcm_configured() is False
    key = tmp_path / "firebase.json"
    key.write_text("{}")
    monkeypatch.setattr(settings, "FCM_CREDENTIALS_FILE", str(key))
    assert notifications.fcm_configured() is True


# ------------------------------------------------------------------ API
def test_subscribe_fcm_flow(client, db, tourist_headers, tourist_user, admin_headers, admin_user, monkeypatch):
    body = {"token": TOKEN, "user_agent": "MUSAFIR Android"}

    monkeypatch.setattr(notifications, "fcm_configured", lambda: False)
    assert client.post("/api/notifications/push/subscribe-fcm", json=body, headers=tourist_headers).status_code == 503
    assert client.get("/api/notifications/push/public-key").json()["fcm_enabled"] is False

    monkeypatch.setattr(notifications, "fcm_configured", lambda: True)
    assert client.post("/api/notifications/push/subscribe-fcm", json=body).status_code == 401
    r = client.post("/api/notifications/push/subscribe-fcm", json=body, headers=tourist_headers)
    assert r.status_code == 201 and r.json()["role"] == "tourist"
    sub = db.query(PushSubscription).filter_by(endpoint=TOKEN).one()
    assert (sub.kind, sub.p256dh, sub.auth, sub.user_id) == ("fcm", None, None, tourist_user.id)

    # the same phone logging in as someone else moves the token to that account
    client.post("/api/notifications/push/subscribe-fcm", json=body, headers=admin_headers)
    db.refresh(sub)
    assert sub.user_id == admin_user.id and db.query(PushSubscription).count() == 1

    client.post("/api/notifications/push/unsubscribe", json={"endpoint": TOKEN}, headers=admin_headers)
    db.refresh(sub)
    assert sub.active is False


def test_short_tokens_are_rejected(client, tourist_headers, monkeypatch):
    monkeypatch.setattr(notifications, "fcm_configured", lambda: True)
    r = client.post("/api/notifications/push/subscribe-fcm", json={"token": "short"}, headers=tourist_headers)
    assert r.status_code == 422
