"""Discovery (services/discovery.py, services/discovery_tags.py, /api/discovery).

Network is never touched: the two data sources (_overpass_post, maps.nominatim_request)
and reverse geocoding are replaced by fakes that return realistic OpenStreetMap-shaped
data, so these tests check OUR logic -- classification, region-aware food filtering,
radius/distance, de-duplication, caching, fallback order and honest failure reporting.
"""
import httpx
import pytest

from app.core.config import settings
from app.services import discovery as d
from app.services import discovery_tags as tg
from app.services import maps

HERE = (28.6315, 77.2167)  # Connaught Place, Delhi


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    monkeypatch.setattr(settings, "DISCOVERY_LIVE_ENABLED", True)
    d.clear_cache()
    yield
    d.clear_cache()


def el(i, lat, lng, etype="node", **tags):
    return {"type": etype, "id": i, "lat": lat, "lon": lng, "tags": tags}


DELHI = {"name": "Connaught Place, New Delhi, Delhi", "country_code": "IN", "state": "Delhi", "city": "New Delhi"}

OVERPASS_DATA = {
    "hidden_gem": [
        el(1, 28.6330, 77.2170, tourism="viewpoint", name="Sunset Point"),
        el(2, 28.6400, 77.2200, historic="ruins", name="Old Baoli", description="Stepwell"),
        el(3, 28.9000, 77.5000, tourism="viewpoint", name="Too Far Point"),             # ~40 km away
        el(4, 28.6340, 77.2180, tourism="attraction", name="Famous Gate", wikipedia="en:Gate"),  # famous: excluded
        el(5, 28.6350, 77.2190, leisure="park"),                                          # unnamed: excluded
    ],
    "regional_food": [
        el(10, 28.6320, 77.2170, amenity="restaurant", name="Punjabi Dhaba", cuisine="punjabi;indian"),
        el(11, 28.6321, 77.2171, amenity="restaurant", name="Pizza Palace", cuisine="pizza;italian"),
        el(12, 28.6322, 77.2172, amenity="fast_food", name="Burger Chain", brand="BK", cuisine="burger"),
        el(13, 28.6323, 77.2173, shop="bakery", name="Old Delhi Bakery"),
        el(14, 28.6324, 77.2174, amenity="restaurant", name="Mystery Place"),            # no cuisine, no local hint
    ],
    "homestay": [
        el(20, 28.6330, 77.2200, tourism="guest_house", name="Sharma Homestay", phone="+91 98100 00000",
           website="sharma.example.com", **{"addr:street": "Rose Lane", "addr:city": "Delhi"}),
        el(21, 28.6331, 77.2201, tourism="hostel", name="Backpackers"),
        el(22, 28.6332, 77.2202, tourism="hotel", name="Big Hotel"),                     # a hotel: not a homestay
    ],
}


def fake_overpass(monkeypatch, data=None, calls=None):
    data = data or OVERPASS_DATA

    def post(query):
        if calls is not None:
            calls.append(query)
        key = "hidden_gem" if '"viewpoint"' in query else "regional_food" if "restaurant" in query else "homestay"
        return list(data[key])
    monkeypatch.setattr(d, "_overpass_post", post)


# ------------------------------------------------------------------ tag logic
def test_classify_covers_each_category_and_rejects_the_rest():
    assert tg.classify({"name": "x", "tourism": "viewpoint"}) == ("hidden_gem", "Viewpoint")
    assert tg.classify({"name": "x", "historic": "fort"}) == ("hidden_gem", "Fort")
    assert tg.classify({"name": "x", "waterway": "waterfall"})[1] == "Waterfall"
    assert tg.classify({"name": "x", "natural": "water", "water": "lake"})[1] == "Lake"
    assert tg.classify({"name": "x", "amenity": "restaurant"}) == ("regional_food", "Restaurant")
    assert tg.classify({"name": "x", "shop": "bakery"}) == ("regional_food", "Bakery")
    assert tg.classify({"name": "x", "tourism": "guest_house"}) == ("homestay", "Guest house")
    assert tg.classify({"name": "x", "tourism": "guest_house", "guest_house": "bed_and_breakfast"})[1] == "Bed & breakfast"
    assert tg.classify({"name": "x", "tourism": "homestay"})[1] == "Homestay"
    assert tg.classify({"tourism": "viewpoint"}) is None                        # unnamed
    assert tg.classify({"name": "x", "tourism": "hotel"}) is None               # hotels are not homestays
    assert tg.classify({"name": "x", "tourism": "attraction", "wikidata": "Q1"}) is None  # famous
    assert tg.classify({"name": "x", "tourism": "attraction"}) == ("hidden_gem", "Local landmark")


def test_place_fields_come_only_from_tags_nothing_invented():
    p = tg.build_place("node", 7, 1.0, 2.0, {"name": "Spot", "tourism": "viewpoint"})
    assert p["description"] is None and p["address"] is None and p["phone"] is None and p["website"] is None
    q = tg.build_place("way", 8, 1.0, 2.0, {
        "name": "Stay", "tourism": "guest_house", "phone": "123", "website": "stay.example.com",
        "description": "Lovely", "addr:housenumber": "4", "addr:street": "Lane", "addr:city": "Town"})
    assert q["description"] == "Lovely" and q["phone"] == "123"
    assert q["website"] == "http://stay.example.com"
    assert q["address"] == "4 Lane, Town"
    assert q["osm_url"] == "https://www.openstreetmap.org/way/8"


def test_unsafe_websites_are_dropped():
    p = tg.build_place("node", 1, 1.0, 2.0, {"name": "S", "tourism": "guest_house", "website": "javascript:alert(1)"})
    assert p["website"] is None


# ------------------------------------------------------------- regional food
def test_food_relevance_in_delhi():
    prof = tg.region_profile(DELHI)
    assert prof["label"] == "Delhi"
    assert tg.food_relevance({"name": "A", "cuisine": "punjabi;indian"}, prof)[:2] == (True, "region")
    assert tg.food_relevance({"name": "A", "cuisine": "indian"}, prof)[:2] == (True, "country")
    assert tg.food_relevance({"name": "A", "cuisine": "pizza;italian"}, prof)[0] is False       # foreign
    # an international menu with one token "indian" is not local food ...
    assert tg.food_relevance({"name": "A", "cuisine": "italian;pizza;crepe;indian"}, prof)[0] is False
    # ... but a genuinely mixed local place is
    assert tg.food_relevance({"name": "A", "cuisine": "indian;regional;chinese"}, prof)[0] is True
    assert tg.food_relevance({"name": "A", "cuisine": "indian", "brand": "X"}, prof)[0] is False  # chain
    assert tg.food_relevance({"name": "Old Bakery", "shop": "bakery"}, prof)[0] is True          # local kind of outlet
    assert tg.food_relevance({"name": "Mystery", "amenity": "restaurant"}, prof)[0] is False     # nothing says it is local
    assert tg.food_relevance({"name": "Sharma Dhaba", "amenity": "restaurant"}, prof)[0] is True  # named like one


def test_regional_food_follows_the_region():
    rajasthan = tg.region_profile({"country_code": "IN", "state": "Rajasthan", "city": "Jaipur"})
    assert tg.food_relevance({"name": "A", "cuisine": "rajasthani"}, rajasthan)[:2] == (True, "region")
    assert tg.food_relevance({"name": "A", "cuisine": "rajasthani"}, tg.region_profile(DELHI))[0] is False  # not Delhi's cuisine
    paris = tg.region_profile({"country_code": "FR", "country": "France"})
    assert tg.food_relevance({"name": "A", "cuisine": "french"}, paris)[0] is True
    assert tg.food_relevance({"name": "A", "cuisine": "indian"}, paris)[0] is False


def test_unknown_area_filters_nothing_but_chains():
    assert tg.region_profile(None) is None
    assert tg.region_profile({"country_code": "ZZ"}) is None
    assert tg.food_relevance({"name": "A", "cuisine": "pizza"}, None)[0] is True
    assert tg.food_relevance({"name": "A", "brand": "Chain"}, None)[0] is False


# --------------------------------------------------------------- the pipeline
def test_categories_are_separated_filtered_sorted_and_counted(monkeypatch):
    fake_overpass(monkeypatch)
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: DELHI)
    r = d.discover(*HERE, radius_km=10)
    by = {c: [p["name"] for p in r["places"] if p["category"] == c] for c in d.DISCOVERY_CATEGORIES}
    assert by["hidden_gem"] == ["Sunset Point", "Old Baoli"]                  # far, unnamed and famous ones are out
    assert by["regional_food"] == ["Punjabi Dhaba", "Old Delhi Bakery"]       # pizza, chain, unknown-cuisine are out
    assert by["homestay"] == ["Sharma Homestay", "Backpackers"]               # the hotel is out
    assert r["counts"] == {"hidden_gem": 2, "regional_food": 2, "homestay": 2}
    dists = [p["distance_km"] for p in r["places"]]
    assert dists == sorted(dists) and all(x <= 10 for x in dists)
    assert r["provider"] == "overpass" and not r["degraded"]
    assert r["region"]["name"].startswith("Connaught Place") and r["region"]["cuisine_scope"] == "Delhi"
    food = {p["name"]: p for p in r["places"] if p["category"] == "regional_food"}
    assert food["Punjabi Dhaba"]["regional_match"] == "region" and food["Punjabi Dhaba"]["cuisine"] == "Punjabi, Indian"


def test_distance_is_real_and_radius_is_enforced(monkeypatch):
    fake_overpass(monkeypatch)
    near = d.discover(*HERE, radius_km=0.5, categories=["hidden_gem"])
    assert [p["name"] for p in near["places"]] == ["Sunset Point"]            # Old Baoli is ~1 km away: outside 0.5 km
    p = near["places"][0]
    # (28.6330,77.2170) is ~0.2 km from (28.6315,77.2167)
    assert 0.1 < p["distance_km"] < 0.3
    wide = d.discover(*HERE, radius_km=50, categories=["hidden_gem"])
    assert "Too Far Point" in [x["name"] for x in wide["places"]]


def test_a_single_category_only_returns_that_category(monkeypatch):
    fake_overpass(monkeypatch)
    r = d.discover(*HERE, 10, ["homestay"])
    assert {p["category"] for p in r["places"]} == {"homestay"}
    assert list(r["counts"]) == ["homestay"]


def test_duplicates_collapse_keeping_the_richer_record(monkeypatch):
    data = {**OVERPASS_DATA, "homestay": [
        el(30, 28.6330, 77.2200, tourism="guest_house", name="Twin Stay"),
        el(31, 28.63301, 77.22001, "way", tourism="guest_house", name="Twin Stay", phone="999"),  # same place, richer
        el(30, 28.6330, 77.2200, tourism="guest_house", name="Twin Stay"),                        # same OSM id again
    ]}
    fake_overpass(monkeypatch, data)
    r = d.discover(*HERE, 10, ["homestay"])
    assert [p["name"] for p in r["places"]] == ["Twin Stay"]
    assert r["places"][0]["phone"] == "999"


def test_genuinely_empty_is_an_empty_list_not_an_error(monkeypatch):
    fake_overpass(monkeypatch, {"hidden_gem": [], "regional_food": [], "homestay": []})
    r = d.discover(*HERE, 5)
    assert r["places"] == [] and r["counts"] == {"hidden_gem": 0, "regional_food": 0, "homestay": 0}


# -------------------------------------------------------- fallback & failures
def _nominatim_rows(category):
    return {
        "hidden_gem": [{"osm_type": "node", "osm_id": 91, "lat": "28.6330", "lon": "77.2170", "name": "Backup Viewpoint",
                        "category": "tourism", "type": "viewpoint", "display_name": "Backup Viewpoint, Ridge Road, New Delhi, India",
                        "extratags": {"description": "From OSM"}}],
        "regional_food": [{"osm_type": "node", "osm_id": 92, "lat": "28.6320", "lon": "77.2170", "name": "Backup Dhaba",
                           "category": "amenity", "type": "restaurant", "display_name": "Backup Dhaba, CP, New Delhi",
                           "extratags": {"cuisine": "punjabi"}}],
        "homestay": [{"osm_type": "way", "osm_id": 93, "lat": "28.6330", "lon": "77.2200", "name": "Backup Guest House",
                      "category": "tourism", "type": "guest_house", "display_name": "Backup Guest House, Paharganj, New Delhi"}],
    }[category]


def fake_nominatim(monkeypatch, fail=False):
    def req(url, params, timeout=8.0):
        if fail:
            raise httpx.ConnectError("down")
        q = params["q"]
        cat = "hidden_gem" if "viewpoint" in q else "regional_food" if "restaurant" in q else "homestay" if "guest_house" in q else None
        assert params["bounded"] == 1 and params["viewbox"]  # always limited to the search area
        return _nominatim_rows(cat) if cat else []
    monkeypatch.setattr(maps, "nominatim_request", req)


def fail_overpass(monkeypatch):
    def post(query):
        raise d.ProviderError("Overpass unavailable (502)")
    monkeypatch.setattr(d, "_overpass_post", post)


def test_falls_back_to_nominatim_when_overpass_is_down_and_says_so(monkeypatch):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch)
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: DELHI)
    r = d.discover(*HERE, 10)
    assert r["provider"] == "nominatim" and r["degraded"] is True
    assert {p["name"] for p in r["places"]} == {"Backup Viewpoint", "Backup Dhaba", "Backup Guest House"}
    vp = next(p for p in r["places"] if p["name"] == "Backup Viewpoint")
    assert vp["description"] == "From OSM" and vp["address"] == "Ridge Road, New Delhi, India"
    assert next(p for p in r["places"] if p["name"] == "Backup Dhaba")["regional_match"] == "region"


def test_when_every_source_fails_it_raises_instead_of_pretending_nothing_is_nearby(monkeypatch):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    with pytest.raises(d.DiscoveryUnavailable):
        d.discover(*HERE, 10)


def test_a_stale_copy_is_served_and_labelled_when_everything_is_down(monkeypatch):
    fake_overpass(monkeypatch)
    first = d.discover(*HERE, 10, ["homestay"])
    assert not first["stale"]
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    again = d.discover(*HERE, 10, ["homestay"], refresh=True)  # refresh forces a source attempt
    assert again["stale"] is True and again["cached"] is True
    assert [p["name"] for p in again["places"]] == [p["name"] for p in first["places"]]


def test_results_are_cached_and_refresh_bypasses_the_cache(monkeypatch):
    calls = []
    fake_overpass(monkeypatch, calls=calls)
    d.discover(*HERE, 10, ["homestay"])
    d.discover(*HERE, 10, ["homestay"])
    assert len(calls) == 1
    again = d.discover(HERE[0] + 0.001, HERE[1], 10, ["homestay"])  # same ~1 km cell
    assert len(calls) == 1 and again["cached"] is True
    d.discover(*HERE, 10, ["homestay"], refresh=True)
    assert len(calls) == 2
    d.discover(*HERE, 25, ["homestay"])  # a different radius is a different question
    assert len(calls) == 3


def test_a_failing_overpass_endpoint_is_skipped_for_a_while(monkeypatch):
    monkeypatch.setattr(settings, "OVERPASS_API_URL", "http://op1.test/api")
    monkeypatch.setattr(settings, "OVERPASS_API_URLS", "http://op2.test/api")
    seen = []

    def post(url, data=None, timeout=None, headers=None):
        seen.append(url)
        if "op1" in url:
            raise httpx.ReadTimeout("slow")
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"elements": [el(1, 28.633, 77.217, tourism="viewpoint", name="V")]}, request=req)
    monkeypatch.setattr(d.httpx, "post", post)
    r1 = d.discover(*HERE, 10, ["hidden_gem"])
    assert [p["name"] for p in r1["places"]] == ["V"] and r1["provider"] == "overpass"  # op2 answered
    assert seen == ["http://op1.test/api", "http://op2.test/api"]
    seen.clear()
    d.discover(*HERE, 10, ["hidden_gem"], refresh=True)
    assert seen == ["http://op2.test/api"]  # op1 is not retried straight away


def test_switched_off_is_reported_not_silently_empty(monkeypatch):
    monkeypatch.setattr(settings, "DISCOVERY_LIVE_ENABLED", False)
    with pytest.raises(d.DiscoveryUnavailable):
        d.discover(*HERE, 10)


# ---------------------------------------------------------------------- API
def test_api_returns_places_with_all_fields(client, tourist_headers, monkeypatch):
    fake_overpass(monkeypatch)
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: DELHI)
    r = client.get("/api/discovery", headers=tourist_headers, params={"lat": HERE[0], "lng": HERE[1], "radius_km": 10})
    assert r.status_code == 200
    body = r.json()
    stay = next(p for p in body["places"] if p["name"] == "Sharma Homestay")
    assert stay["kind"] == "Guest house" and stay["phone"] == "+91 98100 00000"
    assert stay["website"] == "http://sharma.example.com" and stay["address"] == "Rose Lane, Delhi"
    assert stay["osm_url"].endswith("/node/20") and stay["source"] == "osm"
    assert body["provider"] == "overpass" and body["counts"]["homestay"] == 2


def test_api_category_filter(client, tourist_headers, monkeypatch):
    fake_overpass(monkeypatch)
    r = client.get("/api/discovery", headers=tourist_headers,
                   params={"lat": HERE[0], "lng": HERE[1], "radius_km": 10, "categories": "homestay"})
    assert {p["category"] for p in r.json()["places"]} == {"homestay"}


def test_api_reports_an_outage_as_503_not_as_an_empty_200(client, tourist_headers, monkeypatch):
    fail_overpass(monkeypatch)
    fake_nominatim(monkeypatch, fail=True)
    r = client.get("/api/discovery", headers=tourist_headers, params={"lat": HERE[0], "lng": HERE[1]})
    assert r.status_code == 503 and "unavailable" in r.json()["detail"].lower()


def test_api_empty_result_is_a_normal_200(client, tourist_headers, monkeypatch):
    fake_overpass(monkeypatch, {"hidden_gem": [], "regional_food": [], "homestay": []})
    r = client.get("/api/discovery", headers=tourist_headers, params={"lat": 10.0, "lng": 20.0})
    assert r.status_code == 200 and r.json()["places"] == []


def test_api_validation_and_auth(client, tourist_headers):
    assert client.get("/api/discovery", params={"lat": 1, "lng": 1}).status_code == 401
    assert client.get("/api/discovery", headers=tourist_headers, params={"lat": 95, "lng": 1}).status_code == 422
    assert client.get("/api/discovery", headers=tourist_headers, params={"lat": 1, "lng": 200}).status_code == 422
    assert client.get("/api/discovery", headers=tourist_headers, params={"lat": 1, "lng": 1, "radius_km": 500}).status_code == 422
    assert client.get("/api/discovery", headers=tourist_headers, params={"lat": 1, "lng": 1, "radius_km": 0}).status_code == 422


def test_unknown_category_names_are_ignored(client, tourist_headers, monkeypatch):
    fake_overpass(monkeypatch)
    r = client.get("/api/discovery", headers=tourist_headers,
                   params={"lat": HERE[0], "lng": HERE[1], "categories": "bogus,homestay"})
    assert {p["category"] for p in r.json()["places"]} == {"homestay"}


def test_offline_bundle_has_a_timestamp(client, tourist_headers, monkeypatch):
    fake_overpass(monkeypatch)
    r = client.get("/api/discovery/offline-bundle", headers=tourist_headers, params={"lat": HERE[0], "lng": HERE[1], "radius_km": 10})
    assert r.status_code == 200 and r.json()["generated_at"] and r.json()["places"]


def test_reverse_endpoint(client, tourist_headers, monkeypatch):
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: DELHI)
    r = client.get("/api/maps/reverse", headers=tourist_headers, params={"lat": 28.63, "lng": 77.21})
    assert r.status_code == 200 and r.json()["place"]["name"].startswith("Connaught Place")
    monkeypatch.setattr(maps, "reverse_geocode", lambda lat, lng: None)
    assert client.get("/api/maps/reverse", headers=tourist_headers, params={"lat": 0, "lng": 0}).json() == {"place": None}
    assert client.get("/api/maps/reverse", params={"lat": 0, "lng": 0}).status_code == 401


def test_indoor_alternatives_still_work_for_the_trip_planner(monkeypatch):
    monkeypatch.setattr(d, "_overpass_post", lambda q: [el(1, 28.64, 77.22, tourism="museum", name="City Museum")])
    out = d.find_indoor_alternatives(*HERE)
    assert out and out[0]["name"] == "City Museum" and out[0]["distance_km"] > 0
    monkeypatch.setattr(d, "_overpass_post", lambda q: (_ for _ in ()).throw(d.ProviderError("down")))
    d.clear_cache()
    assert d.find_indoor_alternatives(*HERE) == []  # never invented
