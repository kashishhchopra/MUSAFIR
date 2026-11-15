"""Push & SMS alerts (services/notifications.py + api/notifications.py)."""
import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from pywebpush import WebPushException

from app.core.config import settings
from app.core.time import utc_now
from app.models.guardian import TripGuardian
from app.models.incident import Incident
from app.models.notification import NotificationLog, PushSubscription
from app.models.tourist import Tourist
from app.services import notifications
from app.services.monitoring import trigger_sos
from tests.conftest import make_tourist


class FakePush:
    """Stand-in for pywebpush.webpush: records calls, can be told to fail."""

    def __init__(self):
        self.calls, self.failures = [], []

    def __call__(self, **kw):
        self.calls.append({"endpoint": kw["subscription_info"]["endpoint"], "data": json.loads(kw["data"]),
                           "urgency": kw["headers"]["Urgency"]})
        if self.failures:
            raise self.failures.pop(0)


class FakeSms:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True


@pytest.fixture
def push(monkeypatch):
    fake = FakePush()
    monkeypatch.setattr(notifications, "_webpush", fake)
    return fake


@pytest.fixture
def sms(monkeypatch):
    fake = FakeSms()
    monkeypatch.setitem(notifications._channels, "console", fake)
    return fake


def _sub(db, endpoint, *, user=None, guardian=None):
    s = PushSubscription(endpoint=endpoint, p256dh="p", auth="a", user_id=user.id if user else None,
                         guardian_id=guardian.id if guardian else None,
                         role=user.role if user else "guardian")
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def world(db, admin_user, tourist_user, responder_user, responder_unit):
    t = db.get(Tourist, tourist_user.tourist_id)
    t.emergency_contacts = json.dumps([{"name": "Dad", "phone": "+919876543210", "relation": "family"}])
    g = TripGuardian(tourist_id=t.id, guardian_name="Mom", guardian_contact="+919000000001")
    db.add(g)
    db.commit()
    subs = {
        "admin": _sub(db, "https://push.test/admin", user=admin_user),
        "responder": _sub(db, "https://push.test/responder", user=responder_user),
        "tourist": _sub(db, "https://push.test/tourist", user=tourist_user),
        "guardian": _sub(db, "https://push.test/guardian", guardian=g),
    }
    return SimpleNamespace(tourist=t, guardian=g, subs=subs, unit=responder_unit)


def _logs(db, **f):
    return db.query(NotificationLog).filter_by(**f).order_by(NotificationLog.id).all()


# ------------------------------------------------------------------ SOS fan-out
def test_sos_notifies_everyone_who_should_know(db, world, push, sms):
    r = trigger_sos(db, world.tourist, 26.1445, 91.7362, "Help, being followed")
    inc = db.get(Incident, r["incident_id"])

    pushed = {c["endpoint"]: c for c in push.calls}
    assert set(pushed) == {"https://push.test/admin", "https://push.test/responder",
                           "https://push.test/tourist", "https://push.test/guardian"}
    # deep links: each role lands on the page where it can act
    assert pushed["https://push.test/admin"]["data"]["url"] == f"/admin/incidents?incident={inc.id}"
    assert pushed["https://push.test/responder"]["data"]["url"] == f"/responder?incident={inc.id}"
    assert pushed["https://push.test/tourist"]["data"]["url"] == "/app"
    assert pushed["https://push.test/guardian"]["data"]["url"] == f"/guardian/{world.guardian.token}"
    admin = pushed["https://push.test/admin"]
    assert "SOS ALERT" in admin["data"]["title"] and world.tourist.full_name in admin["data"]["body"]
    assert admin["urgency"] == "high" and admin["data"]["incident_id"] == inc.id

    # SMS: the emergency contact and the guardian's phone, with the message / live link
    by_to = {m["to"]: m for m in sms.sent}
    assert set(by_to) == {"+919876543210", "+919000000001"}
    assert "Help, being followed" in by_to["+919876543210"]["body"]
    assert f"/guardian/{world.guardian.token}" in by_to["+919000000001"]["body"]

    rows = _logs(db, incident_id=inc.id)
    assert {x.status for x in rows} == {"sent"} and len(rows) == 6
    assert all(x.attempts == 1 and x.sent_at for x in rows)


def test_silent_sos_never_pushes_to_the_tourists_own_device(db, world, push, sms):
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "silent help", silent=True)
    assert "https://push.test/tourist" not in {c["endpoint"] for c in push.calls}
    assert "https://push.test/admin" in {c["endpoint"] for c in push.calls}
    admin_body = next(c for c in push.calls if c["endpoint"].endswith("/admin"))["data"]["body"]
    assert "do not call" in admin_body.lower()
    assert all("do not call" in m["body"].lower() for m in sms.sent)


def test_a_rolled_back_sos_notifies_nobody(db, world, push, sms):
    inc = Incident(tourist_id=world.tourist.id, type="sos", severity="critical", status="detected", description="x")
    db.add(inc)
    db.flush()
    notifications.notify_sos(db, inc, world.tourist, message="m", silent=False, lat=26.1, lng=91.7, unit_name=None)
    assert db.info.get("notif_outbox")
    db.rollback()
    assert not db.info.get("notif_outbox") and push.calls == [] and sms.sent == []
    assert db.query(NotificationLog).count() == 0


def test_duplicates_are_suppressed_not_resent(db, world, push, sms):
    r = trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    inc = db.get(Incident, r["incident_id"])
    n_push, n_sms = len(push.calls), len(sms.sent)
    notifications.notify_sos(db, inc, world.tourist, message="help", silent=False, lat=26.1445, lng=91.7362,
                             unit_name=None)
    db.commit()
    assert (len(push.calls), len(sms.sent)) == (n_push, n_sms)
    assert db.query(NotificationLog).filter_by(status="suppressed").count() >= 6


def test_sim_tourists_are_never_texted_and_allowlist_is_honoured(db, world, push, sms, monkeypatch):
    world.tourist.digital_id = "SIM-0001"
    db.commit()
    first = trigger_sos(db, world.tourist, 26.1445, 91.7362, "sim sos")
    assert sms.sent == []
    assert any("simulated tourist" in (x.last_error or "") for x in _logs(db, channel="sms"))

    # A second, separate emergency: resolve the first, or a repeat SOS inside the dedupe window
    # would (correctly) be merged into it instead of raising a new one.
    db.get(Incident, first["incident_id"]).status = "resolved"
    world.tourist.digital_id = "STS-REAL"
    db.commit()
    monkeypatch.setattr(settings, "SMS_ALLOWLIST", "+919000000001")
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "real sos")
    assert [m["to"] for m in sms.sent] == ["+919000000001"]       # Dad is not on the allowlist


# ------------------------------------------------------------------ failures + retries
def test_transient_push_failure_is_retried_then_succeeds(db, world, push, sms):
    push.failures = [WebPushException("boom", response=SimpleNamespace(status_code=503))] * 4  # all 4 first tries
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    retrying = _logs(db, channel="push", status="retrying")
    assert len(retrying) == 4 and all(x.next_retry_at > utc_now() and x.attempts == 1 for x in retrying)
    assert "503" in retrying[0].last_error

    for x in retrying:
        x.next_retry_at = utc_now() - timedelta(seconds=1)
    db.commit()
    assert notifications.retry_due(db) == 4
    db.expire_all()
    assert all(x.status == "sent" and x.attempts == 2 for x in _logs(db, channel="push"))


def test_gives_up_after_max_attempts(db, world, push, sms, monkeypatch):
    monkeypatch.setattr(settings, "NOTIFICATION_MAX_ATTEMPTS", 2)
    push.failures = [WebPushException("down", response=SimpleNamespace(status_code=500))] * 100
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    for _ in range(2):
        for x in _logs(db, channel="push", status="retrying"):
            x.next_retry_at = utc_now() - timedelta(seconds=1)
        db.commit()
        notifications.retry_due(db)
    db.expire_all()
    assert {x.status for x in _logs(db, channel="push")} == {"failed"}
    assert all(x.attempts == 2 for x in _logs(db, channel="push"))


def test_expired_subscription_is_deactivated_and_not_retried(db, world, push, sms):
    push.failures = [WebPushException("gone", response=SimpleNamespace(status_code=410))]
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    failed = _logs(db, status="failed", channel="push")
    assert len(failed) == 1 and "expired" in failed[0].last_error
    db.expire_all()
    assert db.get(PushSubscription, failed[0].subscription_id).active is False
    assert notifications.retry_due(db) == 0


def test_push_without_vapid_keys_fails_clearly(db, world, sms, monkeypatch):
    monkeypatch.setattr(notifications, "vapid_keys", lambda: (None, "disabled"))
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    rows = _logs(db, channel="push")
    assert rows and all(x.status == "failed" and "not configured" in x.last_error for x in rows)


# ------------------------------------------------------------------ Twilio
def _twilio_on(monkeypatch):
    monkeypatch.setattr(settings, "NOTIFICATION_CHANNEL", "twilio")
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "TWILIO_FROM_NUMBER", "+15550001111")


def _resp(status, body):
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://api.twilio.com"))


def test_twilio_channel_success_and_error_mapping(monkeypatch):
    _twilio_on(monkeypatch)
    ch = notifications.get_channel()
    assert isinstance(ch, notifications.TwilioSmsChannel)
    seen = {}

    def post(url, data, timeout, auth):
        seen.update(url=url, data=data, auth=auth)
        return _resp(201, {"sid": "SM123", "status": "queued"})

    monkeypatch.setattr(httpx, "post", post)
    r = ch.send_detailed("+91 98765-43210", "s", "hello")
    assert r.ok and r.provider_id == "SM123"
    assert seen["data"]["To"] == "+919876543210" and seen["data"]["From"] == "+15550001111"
    assert seen["auth"] == ("ACtest", "tok") and "ACtest" in seen["url"]

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(400, {"code": 21608, "message": "unverified number"}))
    r = ch.send_detailed("+919876543210", "s", "hi")
    assert not r.ok and not r.retryable and "21608" in r.error

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(503, {}))
    assert ch.send_detailed("+919876543210", "s", "hi").retryable

    def boom(*a, **k):
        raise httpx.ConnectTimeout("t")
    monkeypatch.setattr(httpx, "post", boom)
    assert ch.send_detailed("+919876543210", "s", "hi").retryable
    assert not ch.send_detailed("+91-1", "s", "hi").ok            # not a valid number: rejected, never sent


def test_sms_flows_through_twilio_and_records_the_sid(db, world, push, monkeypatch):
    _twilio_on(monkeypatch)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _resp(201, {"sid": "SMabc"}))
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    rows = _logs(db, channel="sms")
    assert len(rows) == 2 and {x.provider_id for x in rows} == {"SMabc"} and {x.status for x in rows} == {"sent"}
    assert all(x.demo is False for x in rows)


def test_console_fallback_is_marked_simulated_never_sent(db, world, push):
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    rows = _logs(db, channel="sms")
    assert rows and {x.status for x in rows} == {"simulated"} and all(x.demo for x in rows)


def test_phone_helpers():
    assert notifications.normalize_phone("+91 98765-43210") == "+919876543210"
    assert notifications.normalize_phone("9876543210") == "+919876543210"
    assert notifications.normalize_phone("0044 7700 900123") == "+447700900123"
    k = notifications.generate_vapid_keys()
    from py_vapid import Vapid
    assert Vapid.from_raw(k["private"].encode()) and len(k["public"]) > 80


# ------------------------------------------------------------------ incident updates / critical
def test_acknowledging_an_sos_updates_tourist_and_guardian(client, db, world, push, sms, admin_headers):
    r = trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    inc = db.get(Incident, r["incident_id"])
    inc.status, inc.acknowledged_at = "detected", None           # auto-dispatch set it; pretend it is still new
    db.commit()
    push.calls.clear()
    assert client.post(f"/api/incidents/{inc.id}/acknowledge", headers=admin_headers).status_code == 200
    got = {c["endpoint"]: c["data"] for c in push.calls}
    assert got["https://push.test/tourist"]["title"] == "Your alert was acknowledged"
    assert world.tourist.full_name in got["https://push.test/guardian"]["title"]
    assert "https://push.test/admin" not in got                  # the actor is not re-notified


def test_resolving_sos_texts_family(client, db, world, push, sms, admin_headers):
    r = trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    sms.sent.clear()
    resp = client.patch(f"/api/incidents/{r['incident_id']}", headers=admin_headers,
                        json={"status": "resolved", "note": "all good"})
    assert resp.status_code == 200
    assert {m["to"] for m in sms.sent} == {"+919876543210", "+919000000001"}
    assert all("resolved" in m["body"] for m in sms.sent)


def test_non_sos_incidents_do_not_ping_the_tourist_and_only_critical_ping_admins(db, world, push, sms):
    t = world.tourist
    crit = Incident(tourist_id=t.id, type="missing", severity="critical", status="detected", description="gone")
    high = Incident(tourist_id=t.id, type="anomaly", severity="high", status="detected", description="odd")
    db.add_all([crit, high])
    db.flush()
    notifications.notify_incident_opened(db, crit, t)
    notifications.notify_incident_opened(db, high, t)
    db.commit()
    assert [c["endpoint"] for c in push.calls] == ["https://push.test/admin"]
    notifications.notify_incident_update(db, high, "resolved")
    db.commit()
    assert len(push.calls) == 1                                  # an anomaly incident never texts/pushes the tourist


def test_escalation_texts_contacts_and_pushes_control_room(db, world, push, sms):
    inc = Incident(tourist_id=world.tourist.id, type="sos", severity="critical", status="detected", description="x",
                   assigned_unit_id=world.unit.id)
    db.add(inc)
    db.flush()
    notifications.notify_escalation(db, inc, world.tourist, "emergency_contact")
    db.commit()
    assert [m["to"] for m in sms.sent] == ["+919876543210"]
    assert "has not been acknowledged" in sms.sent[0]["body"]
    assert {c["endpoint"] for c in push.calls} == {"https://push.test/admin", "https://push.test/responder"}


# ------------------------------------------------------------------ API
def _keys():
    return {"p256dh": "BKey", "auth": "authsecret"}


def test_public_key_and_subscribe_flow(client, db, tourist_headers, tourist_user, admin_headers):
    pk = client.get("/api/notifications/push/public-key").json()
    assert pk["enabled"] and len(pk["public_key"]) > 80

    body = {"endpoint": "https://push.test/new", "keys": _keys(), "user_agent": "UA"}
    assert client.post("/api/notifications/push/subscribe", json=body).status_code == 401
    r = client.post("/api/notifications/push/subscribe", json=body, headers=tourist_headers)
    assert r.status_code == 201 and r.json()["role"] == "tourist"
    sub = db.query(PushSubscription).filter_by(endpoint="https://push.test/new").one()
    assert sub.user_id == tourist_user.id and sub.active

    # the same device re-subscribing as someone else moves, rather than duplicates
    client.post("/api/notifications/push/subscribe", json=body, headers=admin_headers)
    db.expire_all()
    assert db.query(PushSubscription).count() == 1 and db.query(PushSubscription).one().role == "admin"

    client.post("/api/notifications/push/unsubscribe", json={"endpoint": "https://push.test/new"}, headers=admin_headers)
    db.expire_all()
    assert db.query(PushSubscription).one().active is False


def test_guardian_can_subscribe_with_only_their_token(client, db, world):
    body = {"token": world.guardian.token, "endpoint": "https://push.test/mom2", "keys": _keys()}
    r = client.post("/api/notifications/push/guardian-subscribe", json=body)
    assert r.status_code == 201 and r.json()["role"] == "guardian"
    assert client.post("/api/notifications/push/guardian-subscribe", json={**body, "token": "nope"}).status_code == 404
    world.guardian.revoked = True
    db.commit()
    assert client.post("/api/notifications/push/guardian-subscribe", json=body).status_code == 404   # revoked link


def test_admin_history_filters_masking_retry_and_summary(client, db, world, push, sms, admin_headers, tourist_headers):
    for path in ("/logs", "/summary"):
        assert client.get(f"/api/notifications{path}", headers=tourist_headers).status_code == 403
    trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    data = client.get("/api/notifications/logs", headers=admin_headers).json()
    assert data["total"] == 6
    text = json.dumps(data)
    assert "9876543210" not in text and "9000000001" not in text and "3210" in text    # masked, never raw
    sms_only = client.get("/api/notifications/logs?channel=sms", headers=admin_headers).json()
    assert sms_only["total"] == 2 and all(i["channel"] == "sms" for i in sms_only["items"])
    assert client.get("/api/notifications/logs?status=failed", headers=admin_headers).json()["total"] == 0

    s = client.get("/api/notifications/summary", headers=admin_headers).json()
    assert s["by_status"]["sent"] == 6 and s["subscribers"]["total"] == 4
    assert s["config"]["push"]["enabled"] and "demo" in s["config"]["sms"]["mode"]

    row = db.query(NotificationLog).filter_by(channel="push").first()
    row.status, row.last_error = "failed", "boom"
    db.commit()
    again = client.post(f"/api/notifications/logs/{row.id}/retry", headers=admin_headers)
    assert again.status_code == 200 and again.json()["status"] == "sent"
    assert client.post(f"/api/notifications/logs/{row.id}/retry", headers=admin_headers).status_code == 409
    assert client.post("/api/notifications/logs/99999/retry", headers=admin_headers).status_code == 404


def test_test_notification_endpoint(client, db, admin_user, admin_headers, push, sms):
    assert client.post("/api/notifications/test", json={"channel": "push"}, headers=admin_headers).status_code == 409
    _sub(db, "https://push.test/mine", user=admin_user)
    r = client.post("/api/notifications/test", json={"channel": "push"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["items"][0]["status"] == "sent"
    assert push.calls[-1]["data"]["title"] == "MUSAFIR test notification"
    assert client.post("/api/notifications/test", json={"channel": "sms"}, headers=admin_headers).status_code == 422
    r = client.post("/api/notifications/test", json={"channel": "sms", "to": "+919876500000"}, headers=admin_headers)
    assert r.status_code == 200 and sms.sent[-1]["to"] == "+919876500000"


# ------------------------------------------------------------------ real Web Push, no mocks
def test_real_webpush_encryption_and_vapid_roundtrip(db, world, sms, monkeypatch):
    """Runs the real pywebpush code against a local 'push service' and decrypts what
    arrives with the subscriber's private key -- proving payload encryption (RFC 8291)
    and the VAPID signature (RFC 8292) are genuinely correct, not just that we called a function."""
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from py_vapid import Vapid

    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    receiver_key = ec.generate_private_key(ec.SECP256R1())
    auth_secret = b"0123456789abcdef"
    p256dh = b64(receiver_key.public_key().public_bytes(serialization.Encoding.X962,
                                                         serialization.PublicFormat.UncompressedPoint))
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            got["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            got["headers"] = {k.lower(): v for k, v in self.headers.items()}
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    try:
        admin_sub = world.subs["admin"]
        admin_sub.endpoint = f"http://127.0.0.1:{srv.server_port}/push/abc"
        admin_sub.p256dh, admin_sub.auth = p256dh, b64(auth_secret)
        db.commit()
        monkeypatch.setattr(notifications, "_push_subs", lambda db, **kw: [(admin_sub, "Admin")]
                            if kw.get("user_roles") == ["admin"] else [])
        trigger_sos(db, world.tourist, 26.1445, 91.7362, "help")
    finally:
        srv.server_close()

    assert got, "the local push service never received a request"
    plain = http_ece.decrypt(got["body"], private_key=receiver_key, auth_secret=auth_secret, version="aes128gcm")
    payload = json.loads(plain)
    assert "SOS ALERT" in payload["title"] and payload["severity"] == "critical" and payload["incident_id"]
    assert got["headers"]["content-encoding"] == "aes128gcm" and got["headers"]["urgency"] == "high"
    # the VAPID JWT verifies against the server's public key
    keys, _mode = notifications.vapid_keys()
    auth_header = got["headers"]["authorization"]
    assert auth_header.startswith("vapid ") and keys["public"] in auth_header
    assert Vapid.verify(auth_header) is True                       # signature valid for the embedded key
    row = db.query(NotificationLog).filter_by(channel="push", status="sent").one()
    assert row.attempts == 1
