"""Native iOS push (services/apns.py) and its routing through the existing push pipeline."""
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core.config import settings
from app.models.notification import PushSubscription
from app.services import apns, notifications


@pytest.fixture
def apns_on(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    for k, v in {"APNS_KEY_ID": "KEY123", "APNS_TEAM_ID": "TEAM123", "APNS_BUNDLE_ID": "com.x.app",
                 "APNS_AUTH_KEY": key.replace("\n", "\\n")}.items():
        monkeypatch.setattr(settings, k, v)
    apns._jwt_cache.update(token=None, at=0.0)


def test_unconfigured_is_not_sent():
    ok, err, retry = apns.send("abc", {"title": "t"}, True)
    assert (ok, retry) == (False, False) and "not configured" in err


def test_send_builds_request(apns_on, monkeypatch):
    seen = {}

    def fake_post(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return SimpleNamespace(status_code=200, json=lambda: {})

    monkeypatch.setattr(apns, "_post", fake_post)
    ok, err, _ = apns.send("tok1", {"title": "SOS", "body": "help", "incident_id": 7, "url": "/i/7"}, True)
    assert ok and err is None
    assert seen["url"] == "https://api.sandbox.push.apple.com/3/device/tok1"
    assert seen["headers"]["apns-topic"] == "com.x.app" and seen["headers"]["apns-priority"] == "10"
    assert seen["body"]["aps"]["alert"]["title"] == "SOS" and seen["body"]["incident_id"] == 7


@pytest.mark.parametrize("status,reason,retryable,expired", [
    (410, "Unregistered", False, True), (400, "BadDeviceToken", False, True),
    (429, "TooManyRequests", True, False), (503, "", True, False),
])
def test_error_classification(apns_on, monkeypatch, status, reason, retryable, expired):
    monkeypatch.setattr(apns, "_post", lambda *a: SimpleNamespace(status_code=status, json=lambda: {"reason": reason}))
    ok, err, retry = apns.send("tok", {}, False)
    assert not ok and retry is retryable and ("expired" in err) is expired


def test_send_push_routes_apns_rows(apns_on, monkeypatch):
    seen = {}

    def fake_post(url, headers, body):
        seen["url"] = url
        return SimpleNamespace(status_code=200, json=lambda: {})

    monkeypatch.setattr(apns, "_post", fake_post)
    res = notifications._send_push(PushSubscription(endpoint="tok", kind="apns"), {"title": "x"}, False)
    assert res.ok and seen["url"].endswith("/3/device/tok")


TOKEN = "ab" * 32  # 64 hex chars, like a real APNs device token


def test_subscribe_apns_endpoint(client, db, tourist_headers, apns_on):
    r = client.post("/api/notifications/push/subscribe-apns", headers=tourist_headers,
                    json={"token": TOKEN.upper(), "user_agent": "MUSAFIR iOS app"})
    assert r.status_code == 201
    sub = db.query(PushSubscription).filter(PushSubscription.endpoint == TOKEN).one()
    assert sub.kind == "apns" and sub.p256dh is None and sub.role == "tourist"

    r = client.post("/api/notifications/push/unsubscribe", headers=tourist_headers, json={"endpoint": TOKEN})
    assert r.status_code == 200
    db.refresh(sub)
    assert sub.active is False


def test_subscribe_apns_rejects_bad_token_and_unconfigured(client, tourist_headers, apns_on, monkeypatch):
    r = client.post("/api/notifications/push/subscribe-apns", headers=tourist_headers, json={"token": "not-hex"})
    assert r.status_code == 422
    monkeypatch.setattr(settings, "APNS_KEY_ID", "")
    r = client.post("/api/notifications/push/subscribe-apns", headers=tourist_headers, json={"token": TOKEN})
    assert r.status_code == 503
