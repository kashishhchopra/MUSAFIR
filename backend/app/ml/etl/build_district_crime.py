"""One-off ETL: real district-level crime data for the safety score.

Builds the two committed files that app/services/district_crime.py reads:

  app/data/reference/district_crime_india.json
      per district (LGD code): mean yearly count of crimes a visitor could
      suffer, Census 2011 population, rate per lakh people, and a 0-100
      crime_index (percentile rank of that rate among all districts).
  app/data/reference/india_districts_lgd.geojson
      simplified district boundaries, so a GPS point can be placed in its
      district offline (no API call at request time).

Not run in CI or at request time (excluded from coverage) -- like
build_crime_index.py it is a manual step whose OUTPUT is committed.

Sources (all downloaded as-is, nothing edited by hand):
  * NCRB "Crime in India", district-wise IPC crimes 2017-2022, as published
    on the India Data Portal (Bharti Institute / ISB):
    https://ckandev.indiadataportal.com/dataset/crime-statistics
    resource "Districtwise IPC crimes 2017 onwards".
  * Census of India 2011, Primary Census Abstract, district population, same
    portal: dataset "census-pca-demography", resource "Districts".
  * District boundaries: Local Government Directory (LGD) districts, 785
    polygons, CC0-1.0 / CC-BY-4.0, published by yashveeeeeeer/india-geodata
    (https://github.com/yashveeeeeeer/india-geodata), file LGD_Districts.parquet.

Usage (needs pyarrow, which is NOT a runtime dependency):
    python -m app.ml.etl.build_district_crime NCRB.csv CENSUS.csv LGD_Districts.parquet
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REF = Path(__file__).resolve().parents[2] / "data" / "reference"
OUT_CRIME = REF / "district_crime_india.json"
OUT_GEO = REF / "india_districts_lgd.geojson"

# IPC heads a visitor could plausibly suffer. Deliberately excluded: domestic
# offences (dowry deaths, cruelty by husband), negligence/accident deaths,
# rioting, offences against the state, forgery/counterfeiting, elections, etc.
# -- they are real crime but say little about risk to someone passing through.
VISITOR_CRIME_HEADS = {
    "violent": [
        "murder", "atmpt_cmmt_murder", "clpbl_hmcrd_not_amt_murder", "atmpt_cmmt_clpb_hmcrd",
        "vlntrly_caus_grvus_hurt", "grvus_hrt_dangerous_wepn", "acid_attack", "atmpt_acid_attack",
        "vlntrly_caus_dangers_weapons",
    ],
    "sexual": [
        "rape", "atmpt_rape", "assault_on_women", "sex_hrrsmt_pub_trnsprt_sys",
        "sex_hrrsmt_other_places", "voyeurism", "stalking", "insult_modesty_women",
        "obscene_acts_songs_public_places",
    ],
    "kidnapping": [
        "other_kidnp_abduc", "kidnapping_for_ransom", "kidnp_abdctn_murder", "human_trafficking",
        "wrngful_rest_cnfm",
    ],
    "robbery": [
        "robbery", "atmpt_dacoity_robbery", "dacoity", "dacoity_with_murder",
        "mkng_prprtn_assmbly_cmmttng_dcty", "extortion_and_blackmailing",
    ],
    "theft": ["auto_motor_vehicle_theft", "other_thefts", "day_time_burglary", "night_burglary"],
    "cheating": ["cheating", "cheating_impersonation", "credit_debit_card_fraud", "atm_fraud"],
}
ALL_HEADS = [h for heads in VISITOR_CRIME_HEADS.values() for h in heads]


def load_crime(path: str) -> tuple[pd.DataFrame, list[int]]:
    df = pd.read_csv(path)
    missing = [h for h in ALL_HEADS if h not in df.columns]
    if missing:
        raise SystemExit(f"NCRB file is missing expected columns: {missing}")
    for h in ALL_HEADS:
        df[h] = pd.to_numeric(df[h], errors="coerce").fillna(0)
    df["district_code"] = pd.to_numeric(df["district_code"], errors="coerce")
    df["state_code"] = pd.to_numeric(df["state_code"], errors="coerce")
    df = df.dropna(subset=["district_code", "state_code"])
    df["district_code"] = df["district_code"].astype(int)
    df["state_code"] = df["state_code"].astype(int)
    for cat, heads in VISITOR_CRIME_HEADS.items():
        df[cat] = df[heads].sum(axis=1)
    df["visitor_crimes"] = df[list(VISITOR_CRIME_HEADS)].sum(axis=1)
    # Several police units (city police, GRP railway police, CID...) report
    # under one district: sum them into the district for each year.
    per_year = df.groupby(["year", "state_code", "state_name", "district_code", "district_name"],
                          as_index=False)[["visitor_crimes", *VISITOR_CRIME_HEADS]].sum()
    years = sorted(int(y) for y in per_year["year"].unique())
    return per_year, years


def load_population(path: str) -> pd.DataFrame:
    # Long format. A district's total population is its row with gender=Total
    # and literacy=Total (every other breakdown empty), given separately for
    # Rural and Urban -- and Urban is published twice, at Town and at Ward
    # level, with the same total. So: rural + one urban figure.
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    empty = ["age", "social_group", "working_status", "worker_type", "occupation"]
    mask = (df["gender"] == "Total") & (df["literacy"] == "Total")
    for d in empty:
        mask &= df[d].str.lower().isin(["", "nan"])
    tot = df[mask].copy()
    tot["population"] = pd.to_numeric(tot["population"], errors="coerce")
    tot["district_code"] = pd.to_numeric(tot["district_code"], errors="coerce")
    tot = tot.dropna(subset=["population", "district_code"])
    tot["district_code"] = tot["district_code"].astype(int)
    by_area = tot.groupby(["district_code", "rural_urban"])["population"].max().unstack(fill_value=0)
    pop = by_area.sum(axis=1).rename("population").reset_index()
    return pop[pop["population"] > 0]


def build_crime(ncrb_csv: str, census_csv: str) -> dict:
    per_year, years = load_crime(ncrb_csv)
    pop = load_population(census_csv)

    mean = per_year.groupby(["state_code", "state_name", "district_code", "district_name"],
                            as_index=False).agg(
        visitor_crimes=("visitor_crimes", "mean"),
        years=("year", "nunique"),
        **{c: (c, "mean") for c in VISITOR_CRIME_HEADS},
    )
    # The same LGD code can appear with slightly different spellings across
    # years; keep the latest name.
    latest_names = (per_year.sort_values("year").groupby("district_code").tail(1)
                    .set_index("district_code")["district_name"])
    mean = mean.groupby(["state_code", "district_code"], as_index=False).agg(
        state_name=("state_name", "last"), visitor_crimes=("visitor_crimes", "sum"),
        years=("years", "max"), **{c: (c, "sum") for c in VISITOR_CRIME_HEADS})
    mean["district_name"] = mean["district_code"].map(latest_names)
    mean = mean.merge(pop, on="district_code", how="left")

    # State rate: every reported crime in the state over the state's 2011
    # population. Used for districts formed after 2011 (no census figure).
    state_pop = (mean.dropna(subset=["population"]).groupby("state_code")["population"].sum())
    state_cases = mean.groupby("state_code")["visitor_crimes"].sum()
    state_rate = (state_cases / state_pop * 1e5).replace([np.inf, -np.inf], np.nan)

    mean["rate_per_lakh"] = mean["visitor_crimes"] / mean["population"] * 1e5
    mean["rate_basis"] = np.where(mean["rate_per_lakh"].notna(), "district", "state")
    mean.loc[mean["rate_per_lakh"].isna(), "rate_per_lakh"] = mean["state_code"].map(state_rate)
    mean = mean.dropna(subset=["rate_per_lakh"])

    # 0-100 index = percentile rank among districts. A rank rather than a
    # linear scale so a few extreme metros don't squash everyone else to ~0.
    mean["crime_index"] = (mean["rate_per_lakh"].rank(pct=True) * 100).round(1)

    districts = {}
    for r in mean.sort_values("district_code").itertuples():
        districts[str(r.district_code)] = {
            "district": r.district_name, "state": r.state_name, "state_code": int(r.state_code),
            "visitor_crimes_per_year": round(float(r.visitor_crimes), 1),
            "population_2011": None if pd.isna(r.population) else int(r.population),
            "rate_per_lakh": round(float(r.rate_per_lakh), 2),
            "rate_basis": r.rate_basis,
            "crime_index": float(r.crime_index),
            "by_category_per_year": {c: round(float(getattr(r, c)), 1) for c in VISITOR_CRIME_HEADS},
        }

    rates = mean["rate_per_lakh"]
    state_index = {}
    for code, rate in state_rate.dropna().items():
        pct = float((rates <= rate).mean() * 100)
        name = mean.loc[mean["state_code"] == code, "state_name"].iloc[0]
        state_index[str(int(code))] = {"state": name, "rate_per_lakh": round(float(rate), 2),
                                       "crime_index": round(pct, 1)}
    return {
        "_meta": {
            "title": "Crimes a visitor could suffer, per district of India (NCRB 2017-2022)",
            "sources": {
                "crime": "NCRB 'Crime in India', district-wise IPC crimes, via India Data Portal "
                         "(https://ckandev.indiadataportal.com/dataset/crime-statistics)",
                "population": "Census of India 2011, Primary Census Abstract, via India Data Portal "
                              "(dataset census-pca-demography)",
            },
            "years": years,
            "built": date.today().isoformat(),
            "crime_heads": VISITOR_CRIME_HEADS,
            "method": (
                "Per district: mean yearly count of the listed IPC heads across all police units "
                "reporting in that district, divided by its Census 2011 population (per lakh). "
                "Districts created after 2011 have no census figure and use their state's rate "
                "(rate_basis='state'). crime_index = percentile rank of rate_per_lakh among all "
                "districts (0 = lowest, 100 = highest)."
            ),
            "caveats": [
                "Rates use 2011 population, so they are somewhat overstated where population has "
                "grown since; the percentile index is much less affected than the raw rate.",
                "When a district was split after 2011, the parent's 2011 population still covers "
                "the split-off area, so the parent's rate is understated.",
                "Recorded crime depends on reporting; low numbers can mean under-reporting.",
                "Crimes against all residents, not only tourists: no district-level series "
                "of crimes against foreigners or tourists is published.",
            ],
        },
        "states": state_index,
        "districts": districts,
    }


def build_boundaries(parquet_path: str, tolerance_deg: float = 0.004) -> dict:
    import pyarrow.parquet as pq  # dev-only
    import shapely

    table = pq.read_table(parquet_path).to_pandas()
    geom_col = next(c for c in ("geometry", "geom", "SHAPE", "wkb_geometry") if c in table.columns)
    features = []
    for row in table.itertuples(index=False):
        g = shapely.from_wkb(getattr(row, geom_col))
        if g is None or g.is_empty:
            continue
        g = shapely.simplify(g, tolerance_deg, preserve_topology=True)
        g = shapely.set_precision(g, 0.0001)
        features.append({
            "type": "Feature",
            "properties": {
                "dist_lgd": int(row.dist_lgd), "state_lgd": int(row.state_lgd),
                "district": str(row.dtname).strip(), "state": str(row.stname).strip().title(),
            },
            "geometry": json.loads(shapely.to_geojson(g)),
        })
    return {
        "type": "FeatureCollection",
        "_meta": {
            "source": "Local Government Directory (LGD) district boundaries, via "
                      "yashveeeeeeer/india-geodata (LGD_Districts.parquet)",
            "licence": "CC0-1.0 / CC-BY-4.0",
            "simplified_tolerance_deg": tolerance_deg,
            "built": date.today().isoformat(),
        },
        "features": features,
    }


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        raise SystemExit(1)
    crime = build_crime(sys.argv[1], sys.argv[2])
    OUT_CRIME.write_text(json.dumps(crime, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    geo = build_boundaries(sys.argv[3])
    OUT_GEO.write_text(json.dumps(geo, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    codes = {str(f["properties"]["dist_lgd"]) for f in geo["features"]}
    matched = sum(1 for c in crime["districts"] if c in codes)
    print(f"Wrote {OUT_CRIME.name}: {len(crime['districts'])} districts "
          f"({sum(1 for d in crime['districts'].values() if d['rate_basis'] == 'district')} with "
          f"their own 2011 population), years {crime['_meta']['years']}")
    print(f"Wrote {OUT_GEO.name}: {len(geo['features'])} boundaries, "
          f"{OUT_GEO.stat().st_size / 1e6:.1f} MB; {matched} crime districts have a boundary")
