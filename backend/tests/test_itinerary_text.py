"""Natural-language itinerary entry (services/itinerary_extract.py + /from-text)."""
from datetime import date

from app.services.itinerary_extract import parse_natural_language

TODAY = date(2026, 11, 18)  # a Wednesday


def _names(text):
    r = parse_natural_language(text, TODAY, geocode=False)
    return [(d["name"], d.get("date"), d.get("time")) for d in r["destinations"]]


def test_single_destination_with_relative_date():
    assert _names("I want to visit the Taj Mahal tomorrow.") == [("Taj Mahal", "2026-11-19", None)]


def test_multiple_stops_dates_and_times_carry_forward():
    got = _names("Visit Taj Mahal tomorrow, then Agra Fort at 5pm and Fatehpur Sikri on 22 Nov")
    assert got == [
        ("Taj Mahal", "2026-11-19", None),
        ("Agra Fort", "2026-11-19", "17:00"),
        ("Fatehpur Sikri", "2026-11-22", None),
    ]


def test_part_of_day_and_names_starting_with_i():
    assert _names("go to Red Fort in the morning, India Gate in the evening") == [
        ("Red Fort", None, "09:00"), ("India Gate", None, "18:00")]
    assert _names("I'd like to visit Indian Museum on Friday") == [("Indian Museum", "2026-11-20", None)]


def test_arrow_chain_and_plan_phrasing():
    assert [n for n, _, _ in _names("Delhi -> Agra -> Jaipur")] == ["Delhi", "Agra", "Jaipur"]
    assert [n for n, _, _ in _names("plan a trip to Agra")] == ["Agra"]


def test_nonsense_yields_no_destinations():
    assert _names("hello") == []


def test_from_text_endpoint_creates_reviewable_document(client, tourist_headers, tourist_user):
    tid = tourist_user.tourist_id
    r = client.post(f"/api/tourists/{tid}/itinerary-documents/from-text",
                    json={"text": "visit Taj Mahal tomorrow then Red Fort"}, headers=tourist_headers)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "extracted"
    names = [d["name"] for d in body["extracted"]["destinations"]]
    assert names == ["Taj Mahal", "Red Fort"]
    # the gazetteer places both (offline test mode) -- coordinates are real, not invented
    assert all(d["lat"] is not None for d in body["extracted"]["destinations"])
    assert body["extracted"]["destinations"][0]["date"]

    # confirm writes dated stops into the real itinerary
    c = client.post(f"/api/tourists/{tid}/itinerary-documents/{body['id']}/confirm", headers=tourist_headers)
    assert c.status_code == 200
    me = client.get(f"/api/tourists/{tid}", headers=tourist_headers).json()
    assert [w["name"] for w in me["itinerary"]] == ["Taj Mahal", "Red Fort"]
    assert me["itinerary"][0]["date"]


def test_from_text_with_no_place_is_a_clear_failed_document(client, tourist_headers, tourist_user):
    r = client.post(f"/api/tourists/{tourist_user.tourist_id}/itinerary-documents/from-text",
                    json={"text": "hello"}, headers=tourist_headers)
    assert r.status_code == 201
    assert r.json()["status"] == "failed" and r.json()["error"]


def test_from_text_forbidden_for_other_tourist(client, tourist_headers, db):
    from tests.conftest import make_tourist
    other = make_tourist(db, name="Someone Else")
    r = client.post(f"/api/tourists/{other.id}/itinerary-documents/from-text",
                    json={"text": "Agra"}, headers=tourist_headers)
    assert r.status_code == 403
