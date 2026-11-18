"""Nearby (services/nearby.py, services/nearby_tags.py, GET /api/nearby).

No network: the two OSM sources and reverse geocoding are replaced by fakes returning
OpenStreetMap-shaped data, so these test OUR logic -- classification, contact/hours
handling, radius/distance/sort, de-duplication, caching, fallback order, honest failure.
"""
from datetime import datetime

import httpx
import pytest

from app.core.config import settings
from app.services import maps, osm_source
from app.services import nearby as nb
from app.services import nearby_tags as tg
from tests.conftest import make_unit

HERE = (28.6315, 77.2167)  # Connaught Place, Delhi


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(settings, "NEARBY_LIVE_ENABLED", True)
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: {"name": "Connaught Place, New Delhi", "country_code": "IN"})
    nb.clear_cache()
    yield
    nb.clear_cache()


def el(i, lat, lng, etype="node", **tags):
    return {"type": etype, "id": i, "lat": lat, "lon": lng, "tags": tags}


DATA = {
    "hospital": [
        el(1, 28.6330, 77.2170, amenity="hospital", name="City Hospital", emergency="yes", phone="+91 11 2345 6789",
           opening_hours="24/7", **{"operator:type": "government", "addr:street": "Ring Road", "addr:city": "Delhi"}),
        el(2, 28.6320, 77.2168, amenity="clinic", name="Corner Clinic"),
        el(3, 28.6340, 77.2180, amenity="hospital", name="Care Hospital"),
        el(4, 28.6350, 77.2190, amenity="hospital"),                                   # unnamed
        el(5, 29.2000, 77.9000, amenity="hospital", name="Far Hospital"),              # ~85 km
        el(6, 28.6360, 77.2200, amenity="dentist", name="Not Medical Enough"),
    ],
    "pharmacy": [
        el(10, 28.6320, 77.2170, amenity="pharmacy", name="Day Chemist", opening_hours="Mo-Sa 09:00-21:00; Su off", phone="12345"),
        el(11, 28.6321, 77.2171, amenity="pharmacy", name="Night Pharmacy", opening_hours="24/7"),
        el(12, 28.6322, 77.2172, shop="chemist", name="Mystery Medicals", opening_hours="by appointment"),
        el(13, 28.6323, 77.2173, amenity="pharmacy", name="No Hours Store"),
    ],
    "police": [
        el(20, 28.6330, 77.2160, amenity="police", name="Connaught Place Police Station", phone="+91 11 2334 0000;100"),
        el(21, 28.6331, 77.2161, amenity="police", name="Janpath Police Post", police="post"),
        el(22, 28.6332, 77.2162, amenity="police", name="Delhi Police Headquarters"),
    ],
    "transport": [
        el(30, 28.6330, 77.2190, railway="station", station="subway", name="Rajiv Chowk", operator="DMRC", network="Delhi Metro", line="Blue;Yellow"),
        el(31, 28.6340, 77.2200, highway="bus_stop", name="CP Bus Stop"),
        el(32, 28.6341, 77.2201, highway="bus_stop"),                                  # unnamed stop: not useful, dropped
        el(33, 28.6350, 77.2210, amenity="bus_station", name="Inter-State Bus Terminal"),
        el(34, 28.6351, 77.2211, amenity="taxi", name="Auto Stand CP"),
        el(35, 28.6352, 77.2212, amenity="taxi", name="Prepaid Taxi Booth"),
        el(36, 28.6353, 77.2213, railway="station", name="New Delhi Railway Station"),
        el(37, 28.5562, 77.1000, aeroway="aerodrome", name="Delhi Airport"),
    ],
}


def fake_overpass(monkeypatch, data=None, calls=None):
    data = data or DATA

    def post(query):
        if calls is not None:
            calls.append(query)
        key = ("hospital" if "hospital" in query else "pharmacy" if '"pharmacy"' in query
               else "police" if '"police"' in query else "transport")
        return list(data[key])
    monkeypatch.setattr(nb, "_overpass_post", post)


def fail_overpass(monkeypatch):
    def post(query):
        raise ProviderErr("down")
    monkeypatch.setattr(nb, "_overpass_post", post)


ProviderErr = osm_source.ProviderError


def run(db, category, radius=10, **kw):
    return nb.nearby(db, *HERE, category, radius, **kw)


def names(r):
    return [p["name"] for p in r["places"]]


# ------------------------------------------------------------------ tag logic
def test_classification():
    c = tg.classify
    assert c({"amenity": "hospital"}) == ("hospital", "Hospital")
    assert c({"amenity": "hospital", "emergency": "yes"}) == ("hospital", "Emergency hospital")
    assert c({"amenity": "clinic"}) == ("hospital", "Clinic")
    assert c({"healthcare": "centre"}) == ("hospital", "Medical centre")
    assert c({"amenity": "clinic", "name": "Clinique Vétérinaire Saint Paul"}) is None  # animals
    assert c({"amenity": "clinic", "name": "Happy Pet Clinic"}) is None
    assert c({"amenity": "clinic", "healthcare:speciality": "veterinary", "name": "X"}) is None
    assert c({"amenity": "clinic", "name": "Capital Clinic"}) == ("hospital", "Clinic")  # "cap" is not "cat"/"pet"
    # a single doctor's practice is not a hospital, clinic or medical centre
    assert c({"amenity": "doctors", "name": "Dr X"}) is None and c({"healthcare": "doctor"}) is None
    assert c({"amenity": "pharmacy"}) == ("pharmacy", "Pharmacy")
    assert c({"shop": "chemist"}) == ("pharmacy", "Chemist")
    assert c({"amenity": "police", "name": "X Police Station"}) == ("police", "Police station")
    assert c({"amenity": "police", "police": "post"}) == ("police", "Police post")
    assert c({"amenity": "police", "name": "Police Headquarters"}) == ("police", "Police headquarters")
    assert c({"railway": "station", "station": "subway"}) == ("transport", "Metro station")
    assert c({"railway": "station"}) == ("transport", "Railway station")
    assert c({"highway": "bus_stop"}) == ("transport", "Bus stop")
    assert c({"amenity": "bus_station"}) == ("transport", "Bus terminal")
    assert c({"amenity": "taxi", "name": "Auto Stand"}) == ("transport", "Auto-rickshaw stand")
    assert c({"amenity": "taxi", "name": "Prepaid Booth"}) == ("transport", "Taxi stand")
    assert c({"aeroway": "aerodrome"}) == ("transport", "Airport")
    assert c({"amenity": "dentist"}) is None and c({"shop": "supermarket"}) is None


def test_phones_only_real_looking_numbers_nothing_invented():
    assert tg.phones_from_tags({"phone": "+91 11 2334 0000;100"}) == ["+91 11 2334 0000"]  # "100" is too short to trust
    assert tg.phones_from_tags({"contact:phone": "011-23456789", "phone": "abc"}) == ["011-23456789"]
    assert tg.phones_from_tags({}) == []
    assert tg.phones_from_tags({"phone": "call us"}) == []


def test_unsafe_websites_dropped():
    assert tg.website_from_tags({"website": "javascript:alert(1)"}) is None
    assert tg.website_from_tags({"website": "hosp.example.com"}) == "http://hosp.example.com"


def test_opening_hours_only_when_provable():
    mon_noon = datetime(2026, 11, 16, 12, 0)   # a Monday
    sun_noon = datetime(2026, 11, 22, 12, 0)   # a Sunday
    mon_night = datetime(2026, 11, 16, 23, 30)
    s = tg.opening_status
    assert s("24/7", None) == {"open_24h": True, "open_now": True}
    assert s("Mo-Sa 09:00-21:00; Su off", mon_noon)["open_now"] is True
    assert s("Mo-Sa 09:00-21:00; Su off", sun_noon)["open_now"] is False
    assert s("Mo-Sa 09:00-21:00; Su off", mon_night)["open_now"] is False
    assert s("09:00-13:00,16:00-21:00", datetime(2026, 11, 16, 14, 0))["open_now"] is False   # between shifts
    assert s("09:00-13:00,16:00-21:00", datetime(2026, 11, 16, 17, 0))["open_now"] is True
    assert s("Mo-Su 20:00-04:00", datetime(2026, 11, 16, 2, 0))["open_now"] is True            # overnight tail
    assert s("Mo-Fr 08:00-17:00", mon_noon)["open_now"] is True
    # not understood / no clock -> unknown, never a guess
    assert s("Mo-Fr 08:00-17:00; PH off", mon_noon)["open_now"] is None
    assert s("sunrise-sunset", mon_noon)["open_now"] is None
    assert s("by appointment", mon_noon)["open_now"] is None
    assert s("Mo-Fr 08:00-17:00", None)["open_now"] is None
    assert s(None, mon_noon) == {"open_24h": False, "open_now": None}


def test_local_time_from_device_offset():
    utc = datetime(2026, 11, 16, 6, 30)
    assert tg.local_time(330, utc) == datetime(2026, 11, 16, 12, 0)
    assert tg.local_time(None, utc) is None and tg.local_time(99999, utc) is None


def test_place_fields_come_only_from_tags():
    p = tg.build_place("node", 1, 1.0, 2.0, {"amenity": "hospital", "name": "H"})
    assert p["phone"] is None and p["address"] is None and p["opening_hours"] is None
    assert p["open_now"] is None and p["website"] is None and p["operator_type"] is None
    q = tg.build_place("way", 2, 1.0, 2.0, {"amenity": "hospital", "name": "G", "operator:type": "government", "phone": "+91 11 2345 6789"})
    assert q["operator_type"] == "Government" and q["phone"] == "+91 11 2345 6789"
    u = tg.build_place("node", 3, 1.0, 2.0, {"amenity": "hospital"})
    assert u["name"] == "Hospital (name not mapped)"
    assert tg.build_place("node", 4, 1.0, 2.0, {"highway": "bus_stop"}) is None  # unnamed stop is dropped


# ------------------------------------------------------------------- pipeline
def test_hospital_search_filters_sorts_and_flags_emergency(monkeypatch, db):
    fake_overpass(monkeypatch)
    r = run(db, "hospital", 10)
    assert names(r) == ["Corner Clinic", "City Hospital", "Care Hospital", "Hospital (name not mapped)"]  # by distance
    assert "Far Hospital" not in names(r) and "Not Medical Enough" not in names(r)
    city = next(p for p in r["places"] if p["name"] == "City Hospital")
    assert city["emergency"] and city["kind"] == "Emergency hospital" and city["operator_type"] == "Government"
    assert city["open_24h"] and city["open_now"] is True
    assert city["address"] == "Ring Road, Delhi" and city["phone"] == "+91 11 2345 6789"
    assert city["rank"] == 0 and next(p for p in r["places"] if p["name"] == "Corner Clinic")["rank"] == 2
    d = [p["distance_km"] for p in r["places"]]
    assert d == sorted(d) and r["count"] == 4 and r["provider"] == "overpass" and not r["degraded"]
    assert r["region"]["name"] == "Connaught Place, New Delhi"


def test_only_the_requested_category_comes_back(monkeypatch, db):
    fake_overpass(monkeypatch)
    for cat in nb.NEARBY_CATEGORIES:
        assert {p["category"] for p in run(db, cat, 10)["places"]} == {cat}


def test_pharmacy_open_status_is_never_assumed(monkeypatch, db):
    fake_overpass(monkeypatch)
    monday_noon_local = datetime(2026, 11, 16, 12, 0)  # the user's wall clock: a Monday, 12:00
    real = tg.local_time
    monkeypatch.setattr(tg, "local_time", lambda off, now=None: real(off, now) and monday_noon_local)
    r = run(db, "pharmacy", 10, tz_offset_min=330)
    by = {p["name"]: p for p in r["places"]}
    assert by["Day Chemist"]["open_now"] is True            # Mo-Sa 09-21, it is Monday noon
    assert by["Night Pharmacy"]["open_24h"] and by["Night Pharmacy"]["open_now"] is True
    assert by["Mystery Medicals"]["open_now"] is None        # "by appointment": unknown
    assert by["No Hours Store"]["open_now"] is None and by["No Hours Store"]["opening_hours"] is None
    # without the user's clock only 24/7 is provable
    r2 = run(db, "pharmacy", 10)
    by2 = {p["name"]: p for p in r2["places"]}
    assert by2["Day Chemist"]["open_now"] is None and by2["Night Pharmacy"]["open_now"] is True


def test_police_kinds_and_contacts(monkeypatch, db):
    fake_overpass(monkeypatch)
    r = run(db, "police", 10)
    by = {p["name"]: p for p in r["places"]}
    assert by["Connaught Place Police Station"]["kind"] == "Police station"
    assert by["Connaught Place Police Station"]["phones"] == ["+91 11 2334 0000"]
    assert by["Janpath Police Post"]["kind"] == "Police post" and by["Janpath Police Post"]["phone"] is None
    assert by["Delhi Police Headquarters"]["kind"] == "Police headquarters"


def test_transport_kinds_details_and_no_service_claims(monkeypatch, db):
    fake_overpass(monkeypatch)
    r = run(db, "transport", 30)
    by = {p["name"]: p for p in r["places"]}
    assert by["Rajiv Chowk"]["kind"] == "Metro station"
    assert by["Rajiv Chowk"]["details"] == {"operator": "DMRC", "network": "Delhi Metro", "lines": "Blue;Yellow"}
    assert by["CP Bus Stop"]["kind"] == "Bus stop" and by["Inter-State Bus Terminal"]["kind"] == "Bus terminal"
    assert by["Auto Stand CP"]["kind"] == "Auto-rickshaw stand" and by["Prepaid Taxi Booth"]["kind"] == "Taxi stand"
    assert by["New Delhi Railway Station"]["kind"] == "Railway station" and by["Delhi Airport"]["kind"] == "Airport"
    assert len(r["places"]) == 7                              # the unnamed stop is gone
    for p in r["places"]:
        assert "next" not in str(p).lower() and "departure" not in str(p).lower()  # no schedules invented


def test_radius_is_enforced_by_real_distance(monkeypatch, db):
    fake_overpass(monkeypatch)
    near = run(db, "transport", 1)
    assert "Delhi Airport" not in names(near) and "Rajiv Chowk" in names(near)
    wide = run(db, "transport", 50)
    assert "Delhi Airport" in names(wide)
    one = next(p for p in wide["places"] if p["name"] == "Rajiv Chowk")
    assert 0.2 < one["distance_km"] < 0.6   # (28.6330,77.2190) is ~0.3 km from (28.6315,77.2167)


def test_duplicates_collapse(monkeypatch, db):
    data = {**DATA, "pharmacy": [
        el(40, 28.6320, 77.2170, amenity="pharmacy", name="Twin Chemist"),
        el(41, 28.63201, 77.21701, "way", amenity="pharmacy", name="Twin Chemist", phone="98765 43210"),
        el(40, 28.6320, 77.2170, amenity="pharmacy", name="Twin Chemist"),
    ]}
    fake_overpass(monkeypatch, data)
    r = run(db, "pharmacy", 5)
    assert names(r) == ["Twin Chemist"] and r["places"][0]["phone"] == "98765 43210"


def test_a_real_empty_result_is_a_200_with_no_places(monkeypatch, db):
    fake_overpass(monkeypatch, {k: [] for k in DATA})
    r = run(db, "police", 5)
    assert r["places"] == [] and r["count"] == 0 and r["provider"] == "overpass"


# ----------------------------------------------------------- fallback/failures
def fake_nominatim(monkeypatch, rows_by_phrase=None, fail=False, log=None):
    default = {
        "[amenity=hospital]": [{"osm_type": "node", "osm_id": 91, "lat": "28.6330", "lon": "77.2170", "name": "Backup Hospital",
                                "category": "amenity", "type": "hospital", "display_name": "Backup Hospital, Ring Road, Delhi, India",
                                "extratags": {"phone": "+91 11 2222 3333", "emergency": "yes"}}],
        "[amenity=police]": [{"osm_type": "node", "osm_id": 92, "lat": "28.6331", "lon": "77.2161", "name": "Backup Police Station",
                              "category": "amenity", "type": "police", "display_name": "Backup Police Station, CP, Delhi"}],
    }
    rows = rows_by_phrase or default

    def req(url, params, timeout=8.0):
        if fail:
            raise httpx.ConnectError("down")
        assert params["bounded"] == 1 and params["viewbox"]
        if log is not None:
            log.append((params["q"], params["viewbox"]))
        return rows.get(params["q"], [])
    monkeypatch.setattr(maps, "nominatim_request", req)


def test_falls_back_to_nominatim_and_says_so(monkeypatch, db):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch)
    r = run(db, "hospital", 5)
    assert names(r) == ["Backup Hospital"] and r["provider"] == "nominatim" and r["degraded"] is True
    assert r["places"][0]["phone"] == "+91 11 2222 3333" and r["places"][0]["emergency"]


def test_nominatim_searches_nearest_first_in_widening_boxes(monkeypatch, db):
    fail_overpass(monkeypatch)
    log = []
    fake_nominatim(monkeypatch, log=log)
    run(db, "police", 10)
    boxes = [vb for q, vb in log if q == "[amenity=police]"]
    assert len(boxes) == 3  # 1 km, 3 km, then the full 10 km (each returned < 15, so it widened)
    widths = [abs(float(b.split(",")[2]) - float(b.split(",")[0])) for b in boxes]
    assert widths == sorted(widths) and widths[0] < widths[1] < widths[2]


def test_saved_osm_data_is_the_last_resort_for_hospital_and_police_and_labelled(monkeypatch, db):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    u = make_unit(db, name="Saved General Hospital", unit_type="ambulance", lat=28.6330, lng=77.2170)
    u.osm_id = 12345
    u.phone = "100"  # the column's placeholder default
    make_unit(db, name="Hand-written Fixture Hospital", unit_type="ambulance", lat=28.6331, lng=77.2171)  # no osm_id: not real data
    db.commit()
    r = run(db, "hospital", 5)
    assert names(r) == ["Saved General Hospital"]
    assert r["provider"] == "saved" and r["degraded"] and r["stale"]
    p = r["places"][0]
    assert p["source"] == "saved_osm" and p["phone"] is None  # the placeholder is not presented as a number


def test_pharmacy_and_transport_have_no_saved_fallback_so_an_outage_is_an_error(monkeypatch, db):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    for cat in ("pharmacy", "transport"):
        with pytest.raises(nb.NearbyUnavailable):
            run(db, cat, 5)


def test_all_sources_down_and_nothing_saved_is_unavailable(monkeypatch, db):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    with pytest.raises(nb.NearbyUnavailable):
        run(db, "hospital", 5)


def test_a_stale_live_copy_beats_nothing(monkeypatch, db):
    fake_overpass(monkeypatch)
    first = run(db, "hospital", 10)
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    again = run(db, "hospital", 10, refresh=True)
    assert again["stale"] and again["cached"] and names(again) == names(first)


def test_cache_and_refresh(monkeypatch, db):
    calls = []
    fake_overpass(monkeypatch, calls=calls)
    run(db, "pharmacy", 5)
    run(db, "pharmacy", 5)
    assert len(calls) == 1
    run(db, "pharmacy", 5, refresh=True)
    assert len(calls) == 2
    run(db, "pharmacy", 10)          # different radius = different question
    run(db, "police", 5)             # different category
    assert len(calls) == 4
    # a small search is NOT served from a cell ~1 km away
    nb.nearby(db, HERE[0] + 0.02, HERE[1], "pharmacy", 1)
    assert len(calls) == 5


def test_switched_off_is_reported(monkeypatch, db):
    monkeypatch.setattr(settings, "NEARBY_LIVE_ENABLED", False)
    with pytest.raises(nb.NearbyUnavailable):
        run(db, "hospital", 5)


def test_unknown_category_rejected(db):
    with pytest.raises(ValueError):
        run(db, "restaurant", 5)


# ------------------------------------------------------------------------ API
def test_api_uses_the_coordinates_it_is_given(client, tourist_headers, monkeypatch):
    seen = []
    fake_overpass(monkeypatch, calls=seen)
    r = client.get("/api/nearby", headers=tourist_headers,
                   params={"lat": 19.076, "lng": 72.8777, "category": "hospital", "radius_km": 2})
    assert r.status_code == 200 and "19.076000,72.877700" in seen[0] and "around:2000" in seen[0]
    assert r.json()["lat"] == 19.076


def test_api_shape(client, tourist_headers, monkeypatch):
    fake_overpass(monkeypatch)
    r = client.get("/api/nearby", headers=tourist_headers,
                   params={"lat": HERE[0], "lng": HERE[1], "category": "hospital", "radius_km": 10, "tz_offset_min": 330})
    body = r.json()
    city = next(p for p in body["places"] if p["name"] == "City Hospital")
    assert city["emergency"] is True and city["osm_url"].endswith("/node/1") and city["source"] == "osm"
    assert body["count"] == len(body["places"]) and body["category"] == "hospital"


def test_api_outage_is_503_not_an_empty_200(client, tourist_headers, monkeypatch):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    r = client.get("/api/nearby", headers=tourist_headers, params={"lat": 1, "lng": 1, "category": "transport"})
    assert r.status_code == 503 and "unavailable" in r.json()["detail"].lower()


def test_api_validation_and_auth(client, tourist_headers):
    base = {"lat": 1, "lng": 1, "category": "hospital"}
    assert client.get("/api/nearby", params=base).status_code == 401
    assert client.get("/api/nearby", headers=tourist_headers, params={**base, "category": "restaurant"}).status_code == 400
    assert client.get("/api/nearby", headers=tourist_headers, params={**base, "lat": 95}).status_code == 422
    assert client.get("/api/nearby", headers=tourist_headers, params={**base, "lng": 190}).status_code == 422
    assert client.get("/api/nearby", headers=tourist_headers, params={**base, "radius_km": 500}).status_code == 422
    assert client.get("/api/nearby", headers=tourist_headers, params={**base, "tz_offset_min": 5000}).status_code == 422
    assert client.get("/api/nearby", headers=tourist_headers, params={"lat": 1, "lng": 1}).status_code == 422


def test_the_old_stored_position_endpoint_is_gone(client, admin_headers, tourist_user):
    r = client.get(f"/api/tourists/{tourist_user.tourist_id}/nearby?category=hospital", headers=admin_headers)
    assert r.status_code in (404, 405, 422)


def test_one_transient_nominatim_timeout_is_retried_not_fatal(monkeypatch, db):
    fail_overpass(monkeypatch)
    monkeypatch.setattr(osm_source.time, "sleep", lambda s: None)
    attempts = []
    ok_row = {"osm_type": "node", "osm_id": 95, "lat": "28.6331", "lon": "77.2161", "name": "Retry Police Station",
              "category": "amenity", "type": "police", "display_name": "Retry Police Station, CP, Delhi"}

    def req(url, params, timeout=8.0):
        attempts.append(params["q"])
        if len(attempts) == 1:
            raise httpx.ReadTimeout("slow")
        return [ok_row] if params["q"] == "[amenity=police]" else []
    monkeypatch.setattr(maps, "nominatim_request", req)
    r = run(db, "police", 5)
    assert names(r) == ["Retry Police Station"] and attempts[:2] == ["[amenity=police]", "[amenity=police]"]


def test_a_non_transient_nominatim_error_is_not_retried(monkeypatch, db):
    fail_overpass(monkeypatch)
    attempts = []

    def req(url, params, timeout=8.0):
        attempts.append(1)
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(maps, "nominatim_request", req)
    with pytest.raises(nb.NearbyUnavailable):
        run(db, "police", 5)
    assert len(attempts) == 1
