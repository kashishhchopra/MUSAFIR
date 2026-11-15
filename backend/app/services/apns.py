"""Native iOS push via Apple's APNs HTTP/2 API (token-based auth, .p8 key).

The iOS (Capacitor) app registers its APNs device token via
POST /notifications/push/subscribe-apns, stored as a PushSubscription with
kind="apns" and the token as `endpoint`. services/notifications._send_push routes
those rows here (kind="fcm" rows go to Firebase for the Android app).
Nothing is sent unless APNS_KEY_ID / APNS_TEAM_ID / APNS_BUNDLE_ID / APNS_AUTH_KEY
are all configured.
"""
import contextlib
import json
import time

from app.core.config import settings

_jwt_cache: dict = {"token": None, "at": 0.0}


def configured() -> bool:
    return all([settings.APNS_KEY_ID, settings.APNS_TEAM_ID, settings.APNS_BUNDLE_ID, settings.APNS_AUTH_KEY])


def _provider_token() -> str:
    """ES256 JWT; Apple wants it refreshed between 20 and 60 minutes."""
    now = time.time()
    if _jwt_cache["token"] and now - _jwt_cache["at"] < 30 * 60:
        return _jwt_cache["token"]
    from jose import jwt
    key = settings.APNS_AUTH_KEY.replace("\\n", "\n")
    token = jwt.encode({"iss": settings.APNS_TEAM_ID, "iat": int(now)}, key, algorithm="ES256",
                       headers={"kid": settings.APNS_KEY_ID})
    _jwt_cache.update(token=token, at=now)
    return token


def _post(url: str, headers: dict, body: dict):  # indirection so tests can stub the network call
    import httpx
    with httpx.Client(http2=True, timeout=10) as client:
        return client.post(url, headers=headers, content=json.dumps(body))


def send(token: str, payload: dict, urgent: bool):
    """Returns (ok, error, retryable)."""
    if not configured():
        return False, "iOS push is not configured (no APNs key)", False
    host = "api.sandbox.push.apple.com" if settings.APNS_USE_SANDBOX else "api.push.apple.com"
    body = {
        "aps": {"alert": {"title": payload.get("title", ""), "body": payload.get("body", "")},
                "sound": "default",
                "interruption-level": "time-sensitive" if urgent else "active",
                "thread-id": str(payload.get("incident_id") or payload.get("tag") or "musafir")},
        "url": payload.get("url", ""), "incident_id": payload.get("incident_id"), "event": payload.get("event"),
    }
    headers = {"authorization": f"bearer {_provider_token()}", "apns-topic": settings.APNS_BUNDLE_ID,
               "apns-push-type": "alert", "apns-priority": "10" if urgent else "5"}
    try:
        r = _post(f"https://{host}/3/device/{token}", headers, body)
    except Exception as e:  # noqa: BLE001 -- network/crypto errors: try again later
        return False, f"{type(e).__name__}: {str(e)[:160]}", True
    if r.status_code == 200:
        return True, None, False
    reason = ""
    with contextlib.suppress(Exception):
        reason = r.json().get("reason", "")
    if r.status_code == 410 or reason in ("BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"):
        return False, f"subscription expired ({r.status_code} {reason})", False
    return False, f"apns {r.status_code} {reason}", r.status_code == 429 or r.status_code >= 500
