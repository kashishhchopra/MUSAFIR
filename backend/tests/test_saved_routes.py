"""Saved routes API (app/api/saved_routes.py)."""
from tests.conftest import make_tourist

BODY = {
    "name": "Hotel to Taj Mahal", "mode": "walking",
    "origin": {"name": "My location", "lat": 27.18, "lng": 78.01},
    "destination": {"name": "Taj Mahal", "lat": 27.1751, "lng": 78.0421},
    "stops": [{"name": "Agra Fort", "lat": 27.1795, "lng": 78.0211}],
    "distance_km": 3.4, "duration_min": 41.0,
    "geometry": [[27.18, 78.01], [27.1751, 78.0421]],
}


def _url(tid, suffix=""):
    return f"/api/tourists/{tid}/saved-routes{suffix}"


def test_save_list_open_delete_roundtrip(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    r = client.post(_url(tid), json=BODY, headers=tourist_headers)
    assert r.status_code == 201
    saved = r.json()
    assert saved["name"] == "Hotel to Taj Mahal"
    assert saved["destination"]["lat"] == 27.1751
    assert saved["stops"][0]["name"] == "Agra Fort"
    assert saved["geometry"] == BODY["geometry"]
    assert saved["created_at"] and saved["last_opened_at"] is None

    listed = client.get(_url(tid), headers=tourist_headers).json()
    assert [x["id"] for x in listed] == [saved["id"]]  # persisted in the DB

    opened = client.post(_url(tid, f"/{saved['id']}/open"), headers=tourist_headers).json()
    assert opened["last_opened_at"] is not None

    assert client.delete(_url(tid, f"/{saved['id']}"), headers=tourist_headers).status_code == 204
    assert client.get(_url(tid), headers=tourist_headers).json() == []
    assert client.delete(_url(tid, f"/{saved['id']}"), headers=tourist_headers).status_code == 404


def test_save_rejects_invalid_input(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    bad = {**BODY, "origin": {"name": "x", "lat": 123, "lng": 0}}
    assert client.post(_url(tid), json=bad, headers=tourist_headers).status_code == 422
    assert client.post(_url(tid), json={**BODY, "name": ""}, headers=tourist_headers).status_code == 422
    assert client.post(_url(tid), json={**BODY, "mode": "teleport"}, headers=tourist_headers).status_code == 422


def test_cannot_touch_another_tourists_routes(client, tourist_headers, db, tourist_user):
    other = make_tourist(db, name="Someone Else")
    assert client.post(_url(other.id), json=BODY, headers=tourist_headers).status_code == 403
    assert client.get(_url(other.id), headers=tourist_headers).status_code == 403


def test_requires_login(client, tourist_user):
    assert client.get(_url(tourist_user.tourist_id)).status_code in (401, 403)
