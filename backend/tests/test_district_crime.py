"""Real district crime (NCRB 2017-2022) for the safety score."""
import pytest

from app.services import district_crime

GUWAHATI = (26.1445, 91.7362)       # Kamrup Metro, Assam
NEW_DELHI = (28.6139, 77.2090)      # Connaught Place area
RUDRAPRAYAG = (30.2844, 78.9811)    # quiet hill district, Uttarakhand
BAY_OF_BENGAL = (15.0, 88.0)        # open sea
LONDON = (51.5074, -0.1278)


@pytest.fixture(autouse=True)
def _fresh():
    district_crime.clear_cache()
    yield
    district_crime.clear_cache()


def test_committed_table_covers_most_districts_and_cites_its_sources():
    table = district_crime.load_crime_table()
    assert len(table["districts"]) > 700
    assert table["_meta"]["years"][0] == 2017
    assert "NCRB" in table["_meta"]["sources"]["crime"]
    assert "Census" in table["_meta"]["sources"]["population"]
    for row in list(table["districts"].values())[:50]:
        assert 0 <= row["crime_index"] <= 100
        assert row["rate_per_lakh"] >= 0
        assert row["rate_basis"] in ("district", "state")


def test_a_point_is_placed_in_its_real_district():
    d = district_crime.district_at(*GUWAHATI)
    assert d["district"] == "Kamrup Metro"
    assert d["state"] == "Assam"


def test_points_outside_india_have_no_district():
    assert district_crime.district_at(*BAY_OF_BENGAL) is None
    assert district_crime.crime_at(*LONDON) is None
    assert district_crime.crime_at(None, None) is None


def test_city_crime_ranks_far_above_a_quiet_hill_district():
    city = district_crime.crime_at(*GUWAHATI)
    hills = district_crime.crime_at(*RUDRAPRAYAG)
    assert city["source"].startswith("NCRB")
    assert city["crime_index"] > 90
    assert hills["crime_index"] < 25
    assert city["rate_per_lakh"] > hills["rate_per_lakh"]
    assert city["years"] == [2017, 2022]


def test_delhi_is_among_the_highest():
    assert district_crime.crime_at(*NEW_DELHI)["crime_index"] > 90


def test_district_without_an_ncrb_row_uses_its_state(monkeypatch):
    table = district_crime.load_crime_table()
    code = str(district_crime.district_at(*GUWAHATI)["dist_lgd"])
    trimmed = {**table, "districts": {k: v for k, v in table["districts"].items() if k != code}}
    monkeypatch.setattr(district_crime, "load_crime_table", lambda: trimmed)
    out = district_crime.crime_at(*GUWAHATI)
    assert out["rate_basis"] == "state"
    assert out["state"] == table["states"]["18"]["state"]
    assert out["crime_index"] == table["states"]["18"]["crime_index"]


def test_missing_boundaries_disable_lookup_without_crashing(monkeypatch, tmp_path):
    monkeypatch.setattr(district_crime, "_GEO_PATH", tmp_path / "nope.geojson")
    assert district_crime.crime_at(*GUWAHATI) is None


def test_safety_score_uses_real_district_crime(db):
    from app.services.safety import score_at

    r = score_at(db, *GUWAHATI, hour=12)
    b = r["breakdown"]
    assert b["crime_source"] == "ncrb_district"
    assert b["district"] == "Kamrup Metro"
    assert b["crime_index"] == district_crime.crime_at(*GUWAHATI)["crime_index"]
    assert b["district_crime_rate_per_lakh"] > 0
    assert b["score_method"] == "formula"


def test_safety_score_outside_india_keeps_the_fallback(db):
    from app.services.safety import score_at

    b = score_at(db, *LONDON, hour=12)["breakdown"]
    assert b["crime_source"] == "default"
    assert b["district"] is None
    assert b["crime_index"] == 20.0


def test_same_conditions_score_safer_in_a_low_crime_district(db):
    from app.services.safety import score_at

    city = score_at(db, *GUWAHATI, hour=12)["score"]
    hills = score_at(db, *RUDRAPRAYAG, hour=12)["score"]
    assert hills > city
