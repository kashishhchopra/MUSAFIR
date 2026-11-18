"""Walk-Me-Home upgrade: type-ahead suggestions, trusted-contact validation /
de-duplication / editing, and the route-buffer config."""
from unittest.mock import patch

import httpx

from app.services import maps


def _g(client, headers, tid, name, contact=""):
    return client.post(f"/api/tourists/{tid}/guardians", headers=headers,
                       json={"guardian_name": name, "guardian_contact": contact})


# ------------------------------------------------------------- suggestions
def test_suggest_requires_auth(client):
    assert client.get("/api/maps/suggest", params={"q": "leela"}).status_code == 401


def test_suggest_short_query_is_empty(client, tourist_headers):
    r = client.get("/api/maps/suggest", params={"q": "l"}, headers=tourist_headers)
    assert r.status_code == 200 and r.json()["results"] == []


class _Resp:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


PHOTON = {"features": [
    {"geometry": {"coordinates": [77.0266, 28.4595]},
     "properties": {"name": "The Leela Ambience", "street": "Ambience Island", "city": "Gurugram",
                    "state": "Haryana", "country": "India"}},
    {"geometry": {"coordinates": [72.88, 19.11]},
     "properties": {"name": "The Leela Mumbai", "city": "Mumbai", "country": "India"}},
]}


def test_photon_results_become_labelled_places(monkeypatch):
    maps.clear_suggest_cache()
    monkeypatch.setattr(maps.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(maps.settings, "PHOTON_URL", "http://photon.test/api/")
    seen = {}

    def fake_get(url, params=None, **kw):
        seen["params"] = params
        return _Resp(PHOTON)

    monkeypatch.setattr(maps.httpx, "get", fake_get)
    out = maps.suggest_places("leela ambi", 5, near=(28.6, 77.2))
    assert out[0]["name"] == "The Leela Ambience, Ambience Island, Gurugram, Haryana, India"
    assert (out[0]["lat"], out[0]["lng"]) == (28.4595, 77.0266)
    assert out[0]["demo"] is False
    assert seen["params"]["lat"] == 28.6 and seen["params"]["lon"] == 77.2  # bias, not a filter
    # second identical call is served from the cache (no new request)
    monkeypatch.setattr(maps.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("cached")))
    assert maps.suggest_places("leela ambi", 5, near=(28.6, 77.2)) == out
    maps.clear_suggest_cache()


def test_photon_down_falls_back_to_search(monkeypatch):
    maps.clear_suggest_cache()
    monkeypatch.setattr(maps.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(maps.settings, "PHOTON_URL", "http://photon.test/api/")
    calls = []

    def fake_get(url, params=None, **kw):
        calls.append(url)
        if "photon" in url:
            raise httpx.ConnectError("down")
        return _Resp([{"display_name": "Leela Palace, Delhi", "lat": "28.5", "lon": "77.2"}])

    monkeypatch.setattr(maps.httpx, "get", fake_get)
    monkeypatch.setattr(maps.time, "sleep", lambda s: None)
    out = maps.suggest_places("leela palace", 3)
    assert out and out[0]["name"] == "Leela Palace, Delhi"
    maps.clear_suggest_cache()


def test_nothing_found_is_an_empty_list_not_a_guess(monkeypatch):
    maps.clear_suggest_cache()
    monkeypatch.setattr(maps.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(maps.settings, "PHOTON_URL", "http://photon.test/api/")
    monkeypatch.setattr(maps.time, "sleep", lambda s: None)
    monkeypatch.setattr(maps.httpx, "get", lambda url, **k: _Resp({"features": []} if "photon" in url else []))
    assert maps.suggest_places("qqzzxx nowhere", 5) == []
    maps.clear_suggest_cache()


# ------------------------------------------------------------- guardians
def test_guardian_phone_validated_and_normalised(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    assert _g(client, tourist_headers, tid, "Mum", "abc").status_code == 422
    assert _g(client, tourist_headers, tid, "Mum", "123").status_code == 422
    ok = _g(client, tourist_headers, tid, "Mum", "+91 98765-43210")
    assert ok.status_code == 201 and ok.json()["guardian_contact"] == "+919876543210"
    assert _g(client, tourist_headers, tid, "Dad", "Dad@Example.com").json()["guardian_contact"] == "dad@example.com"
    assert _g(client, tourist_headers, tid, "Sis", "").status_code == 201  # contact optional


def test_duplicate_guardian_rejected_but_revoked_can_be_readded(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    first = _g(client, tourist_headers, tid, "Mum", "+919876543210")
    assert first.status_code == 201
    dup = _g(client, tourist_headers, tid, "Mother", "+91 98765 43210")  # same number, other spelling
    assert dup.status_code == 409 and "already" in dup.json()["detail"]
    assert _g(client, tourist_headers, tid, "myself").status_code == 201
    assert _g(client, tourist_headers, tid, "Myself").status_code == 409  # same name, no contact
    client.post(f"/api/tourists/{tid}/guardians/{first.json()['id']}/revoke", headers=tourist_headers)
    assert _g(client, tourist_headers, tid, "Mum", "+919876543210").status_code == 201


def test_edit_guardian(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    a = _g(client, tourist_headers, tid, "Mum", "+919876543210").json()
    b = _g(client, tourist_headers, tid, "Dad", "+919876543211").json()
    r = client.patch(f"/api/tourists/{tid}/guardians/{a['id']}", headers=tourist_headers,
                     json={"guardian_name": "Mummy", "guardian_contact": "+919876500000"})
    assert r.status_code == 200 and r.json()["guardian_name"] == "Mummy"
    clash = client.patch(f"/api/tourists/{tid}/guardians/{a['id']}", headers=tourist_headers,
                         json={"guardian_contact": b["guardian_contact"]})
    assert clash.status_code == 409
    bad = client.patch(f"/api/tourists/{tid}/guardians/{a['id']}", headers=tourist_headers,
                       json={"guardian_contact": "nope"})
    assert bad.status_code == 422
    assert client.patch(f"/api/tourists/{tid}/guardians/99999", headers=tourist_headers,
                        json={"guardian_name": "x"}).status_code == 404


def test_cannot_edit_someone_elses_guardian(client, tourist_headers, tourist_user, admin_headers):
    tid = tourist_user.tourist_id
    a = _g(client, tourist_headers, tid, "Mum", "+919876543210").json()
    # a different tourist id in the path is refused by require_self_or_admin
    r = client.patch(f"/api/tourists/{tid + 1000}/guardians/{a['id']}", headers=tourist_headers,
                     json={"guardian_name": "x"})
    assert r.status_code in (403, 404)


def test_config_exposes_route_buffer(client, tourist_headers):
    c = client.get("/api/walk-home/config", headers=tourist_headers).json()
    assert c["route_buffer_percent"] > 0 and c["route_buffer_min_minutes"] > 0


# ------------------------------------------------------------- history / retention
def _start(client, headers, tid, **kw):
    body = {"destination_name": "Home", "dest_lat": 28.5, "dest_lng": 77.2,
            "duration_minutes": 30, "check_interval_minutes": 10}
    body.update(kw)
    return client.post(f"/api/tourists/{tid}/walk-home", headers=headers, json=body)


def test_position_is_wiped_when_journey_ends_and_history_can_be_deleted(client, tourist_headers, tourist_user, db):
    from app.models.checkin import CheckIn
    tid = tourist_user.tourist_id
    j = _start(client, tourist_headers, tid).json()
    r = client.post(f"/api/tourists/{tid}/walk-home/{j['id']}/location", headers=tourist_headers,
                    json={"lat": 28.51, "lng": 77.21, "accuracy_m": 10})
    assert r.json()["accepted"] is True
    # cannot wipe history while the journey is live
    assert client.delete(f"/api/tourists/{tid}/walk-home", headers=tourist_headers).json()["deleted"] == 0
    assert client.post(f"/api/tourists/{tid}/walk-home/{j['id']}/end", headers=tourist_headers).status_code == 200
    db.expire_all()
    row = db.get(CheckIn, j["id"])
    assert row.last_lat is None and row.last_lng is None and row.start_lat is None and row.last_loc_at is None
    assert len(client.get(f"/api/tourists/{tid}/walk-home", headers=tourist_headers).json()) == 1
    assert client.delete(f"/api/tourists/{tid}/walk-home", headers=tourist_headers).json()["deleted"] == 1
    assert client.get(f"/api/tourists/{tid}/walk-home", headers=tourist_headers).json() == []


def test_cannot_delete_another_tourists_history(client, tourist_headers, tourist_user):
    r = client.delete(f"/api/tourists/{tourist_user.tourist_id + 1000}/walk-home", headers=tourist_headers)
    assert r.status_code in (403, 404)
