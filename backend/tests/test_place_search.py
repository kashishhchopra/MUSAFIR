"""Place search (services/place_search.py, GET /maps/search)."""
import httpx
import pytest

from app.services import place_search

GUWAHATI = (26.1445, 91.7362)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    place_search.clear_cache()
    monkeypatch.setattr(place_search.maps, "_NOMINATIM_MIN_INTERVAL_S", 0.0)
    yield
    place_search.clear_cache()


@pytest.fixture
def live(monkeypatch):
    """Let the live code path run (tests are offline by default) -- every HTTP
    call is then faked by the test."""
    monkeypatch.setattr(type(place_search.settings), "is_test", property(lambda self: False))
    monkeypatch.setattr(place_search.settings, "PLACE_SEARCH_LIVE_ENABLED", True)
    monkeypatch.setattr(place_search.settings, "GOOGLE_MAPS_API_KEY", "")


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


PHOTON = {"features": [
    {"properties": {"name": "GS Road", "district": "Iewduh", "city": "Shillong", "state": "Meghalaya",
                    "country": "India"}, "geometry": {"coordinates": [91.8825, 25.5794]}},
    {"properties": {"name": "G.S. Road", "district": "Ulubari", "city": "Guwahati", "state": "Assam",
                    "country": "India"}, "geometry": {"coordinates": [91.7647, 26.1686]}},
    {"properties": {"name": "Bus stop near GS"}, "geometry": {"coordinates": [91.7700, 26.1700]}},
]}
NOMINATIM = [
    {"name": "G.S. Road", "display_name": "G.S. Road, Ulubari, Guwahati, Assam, India",
     "lat": "26.16861", "lon": "91.76471"},  # duplicate of Photon's
    {"name": "Fancy Bazaar", "display_name": "Fancy Bazaar, Guwahati, Assam, India",
     "lat": "26.1799", "lon": "91.7362"},
]


def _fake_osm(monkeypatch, photon=PHOTON, nominatim=NOMINATIM, calls=None):
    def fake_get(url, params=None, headers=None, timeout=None):
        if calls is not None:
            calls.append((url, params))
        if "photon" in url:
            return _Resp(photon)
        if "nominatim" in url:
            return _Resp(nominatim)
        raise AssertionError(url)
    monkeypatch.setattr(place_search.httpx, "get", fake_get)


# ---------------------------------------------------------------- offline (default in tests)
def test_offline_uses_the_builtin_landmarks_including_typos():
    r = place_search.search("kamakya temple", *GUWAHATI)
    assert r["provider"] == "offline"
    assert r["results"][0]["name"] == "Kamakhya Temple"
    assert r["results"][0]["source"] == "builtin"
    assert r["results"][0]["distance_m"] < 10_000


def test_too_short_or_unknown_queries_return_nothing():
    assert place_search.search("a")["results"] == []
    r = place_search.search("zzqqxx nowhere")
    assert r["results"] == [] and r["provider"] == "none"


# ---------------------------------------------------------------- OpenStreetMap
def test_osm_results_are_merged_deduplicated_and_ranked_near_the_tourist(live, monkeypatch):
    calls = []
    _fake_osm(monkeypatch, calls=calls)
    r = place_search.search("GS Road", *GUWAHATI, limit=5)
    assert r["provider"] == "openstreetmap"
    names = [(x["name"], x["address"].split(",")[0]) for x in r["results"]]
    # Guwahati's G.S. Road (3.9 km) before Shillong's (65 km); the Nominatim
    # copy of the same road is merged away; "G.S." matches "GS".
    assert names[0] == ("G.S. Road", "Ulubari")
    assert names[1][0] == "GS Road"
    assert sum(1 for n, _ in names if n == "G.S. Road") == 1
    assert r["results"][0]["distance_m"] < 5000
    # Both sources are asked, biased to the tourist's position.
    photon = next(p for u, p in calls if "photon" in u)
    assert photon["lat"] == GUWAHATI[0] and photon["lon"] == GUWAHATI[1]
    nom = next(p for u, p in calls if "nominatim" in u)
    assert nom["bounded"] == 0 and "viewbox" in nom


def test_exact_name_beats_a_place_that_only_mentions_it(live, monkeypatch):
    photon = {"features": [
        {"properties": {"name": "Office of the Engineer, Fancy Bazar, Guwahati-01 Mechanical Circle",
                        "city": "Guwahati"}, "geometry": {"coordinates": [91.7385, 26.1835]}},
        {"properties": {"name": "Fancy Bazaar", "city": "Guwahati"}, "geometry": {"coordinates": [91.7362, 26.1799]}},
    ]}
    _fake_osm(monkeypatch, photon=photon, nominatim=[])
    r = place_search.search("Fancy bazar", *GUWAHATI)
    assert r["results"][0]["name"] == "Fancy Bazaar"


def test_a_failing_source_does_not_break_search(live, monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if "photon" in url:
            raise httpx.ConnectTimeout("down")
        return _Resp(NOMINATIM)
    monkeypatch.setattr(place_search.httpx, "get", fake_get)
    r = place_search.search("Fancy Bazaar", *GUWAHATI)
    assert r["provider"] == "openstreetmap"
    assert r["results"][0]["name"] == "Fancy Bazaar"


def test_results_are_cached(live, monkeypatch):
    calls = []
    _fake_osm(monkeypatch, calls=calls)
    place_search.search("GS Road", *GUWAHATI)
    n = len(calls)
    place_search.search("gs road", 26.1446, 91.7361)  # same cell, same words
    assert len(calls) == n


# ---------------------------------------------------------------- Google Places
def test_google_places_is_used_when_a_key_is_set(live, monkeypatch):
    monkeypatch.setattr(place_search.settings, "GOOGLE_MAPS_API_KEY", "fake-key")
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers)
        return _Resp({"places": [
            {"displayName": {"text": "Hotel Vishwaratna"}, "formattedAddress": "AT Road, Guwahati, Assam",
             "location": {"latitude": 26.1832, "longitude": 91.7425}},
            {"displayName": {"text": "no location"}},
        ]})

    monkeypatch.setattr(place_search.httpx, "post", fake_post)
    monkeypatch.setattr(place_search.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("OSM not needed")))
    r = place_search.search("Hotel Vishwaratna", *GUWAHATI)
    assert r["provider"] == "google"
    assert [x["name"] for x in r["results"]] == ["Hotel Vishwaratna"]
    assert r["results"][0]["source"] == "google"
    assert "places:searchText" in seen["url"]
    assert seen["headers"]["X-Goog-Api-Key"] == "fake-key"
    assert seen["body"]["locationBias"]["circle"]["center"] == {"latitude": GUWAHATI[0], "longitude": GUWAHATI[1]}


def test_google_failure_falls_back_to_openstreetmap(live, monkeypatch):
    monkeypatch.setattr(place_search.settings, "GOOGLE_MAPS_API_KEY", "bad-key")
    monkeypatch.setattr(place_search.httpx, "post", lambda *a, **k: _Resp({}, status=403))
    _fake_osm(monkeypatch)
    r = place_search.search("GS Road", *GUWAHATI)
    assert r["provider"] == "openstreetmap"
    assert r["results"]


# ---------------------------------------------------------------- API
def test_endpoint_ranks_near_the_tourists_last_position(client, db, tourist_user, tourist_headers):
    from app.models.tourist import Tourist

    t = db.get(Tourist, tourist_user.tourist_id)
    t.last_lat, t.last_lng = GUWAHATI
    db.commit()
    r = client.get("/api/maps/search", params={"q": "kamakhya temple"}, headers=tourist_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["near"] == {"lat": GUWAHATI[0], "lng": GUWAHATI[1]}
    assert data["results"][0]["name"] == "Kamakhya Temple"
    assert data["results"][0]["distance_m"] < 10_000


def test_endpoint_prefers_an_explicit_position(client, tourist_headers):
    r = client.get("/api/maps/search", params={"q": "taj mahal", "lat": 27.17, "lng": 78.04},
                   headers=tourist_headers)
    assert r.json()["near"] == {"lat": 27.17, "lng": 78.04}
    assert r.json()["results"][0]["distance_m"] < 2000


def test_endpoint_validates_and_requires_login(client, tourist_headers):
    assert client.get("/api/maps/search", params={"q": "x"}, headers=tourist_headers).status_code == 422
    assert client.get("/api/maps/search", params={"q": "taj mahal"}).status_code == 401
