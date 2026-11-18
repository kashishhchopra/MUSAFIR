"""What counts as a hidden gem / regional food / homestay in OpenStreetMap data, and
how a raw OSM record becomes a Discovery place.

Pure functions (no network): the same tag logic serves both data sources in
services/discovery.py (Overpass elements, and Nominatim results re-expressed as
tags), so a place looks the same whichever source answered. Nothing here invents
data: every field on a place comes from an OSM tag, or is absent.
"""
from __future__ import annotations

import re

HIDDEN_GEM = "hidden_gem"
REGIONAL_FOOD = "regional_food"
HOMESTAY = "homestay"
CATEGORIES = (HIDDEN_GEM, REGIONAL_FOOD, HOMESTAY)

# Overpass selectors per category, applied to nodes, ways and relations. Every one
# requires a name (an unnamed park is not a "place to discover").
OVERPASS_SELECTORS: dict[str, list[str]] = {
    HIDDEN_GEM: [
        '["tourism"="viewpoint"]["name"]',
        '["historic"~"^(monument|ruins|fort|castle|archaeological_site|memorial|tomb|city_gate|manor|palace|temple|tower|building)$"]["name"]',
        '["leisure"~"^(park|garden)$"]["name"]',
        '["waterway"="waterfall"]["name"]',
        '["natural"~"^(peak|beach|cave_entrance|hot_spring)$"]["name"]',
        '["natural"="water"]["water"="lake"]["name"]',
        '["tourism"~"^(museum|gallery)$"]["name"]',
        '["tourism"="artwork"]["name"]',
        # A generic "attraction" only when it has no Wikipedia/Wikidata entry, i.e. it
        # is not one of the famous ones everybody already knows.
        '["tourism"="attraction"]["name"][!"wikipedia"][!"wikidata"]',
        '["heritage"]["name"]',
    ],
    REGIONAL_FOOD: [
        '["amenity"~"^(restaurant|fast_food|food_court|marketplace)$"]["name"]',
        '["shop"~"^(bakery|confectionery|pastry)$"]["name"]',
    ],
    HOMESTAY: [
        '["tourism"~"^(guest_house|homestay|hostel|chalet|bed_and_breakfast|farm_stay)$"]["name"]',
    ],
}

# Nominatim "special phrase" category searches (used when Overpass is down): each is
# one bounded-box request, so keep the lists short.
NOMINATIM_QUERIES: dict[str, list[str]] = {
    HIDDEN_GEM: ["[tourism=viewpoint]", "[historic=monument]", "[historic=ruins]", "[historic=fort]",
                 "[waterway=waterfall]", "[leisure=garden]", "[leisure=park]", "[tourism=museum]"],
    REGIONAL_FOOD: ["[amenity=restaurant]", "[amenity=fast_food]", "[shop=bakery]",
                    "[shop=confectionery]", "[amenity=marketplace]"],
    HOMESTAY: ["[tourism=guest_house]", "[tourism=hostel]", "[tourism=chalet]"],
}

_KIND_BY_VALUE = {
    # hidden gems
    "viewpoint": "Viewpoint", "museum": "Museum", "gallery": "Art gallery", "artwork": "Public art",
    "attraction": "Local landmark", "park": "Park", "garden": "Garden", "waterfall": "Waterfall",
    "peak": "Peak", "beach": "Beach", "cave_entrance": "Cave", "hot_spring": "Hot spring", "lake": "Lake",
    "monument": "Monument", "ruins": "Ruins", "fort": "Fort", "castle": "Castle", "memorial": "Memorial",
    "archaeological_site": "Archaeological site", "tomb": "Tomb", "city_gate": "Historic gate",
    "manor": "Manor", "palace": "Palace", "temple": "Temple", "tower": "Historic tower", "building": "Historic building",
    # food
    "restaurant": "Restaurant", "fast_food": "Street food / quick bites", "food_court": "Food court",
    "marketplace": "Market", "bakery": "Bakery", "confectionery": "Sweet shop", "pastry": "Pastry shop",
    # stays
    "guest_house": "Guest house", "homestay": "Homestay", "hostel": "Hostel", "chalet": "Chalet",
    "bed_and_breakfast": "Bed & breakfast", "farm_stay": "Farm stay",
}
_HOMESTAY_TOURISM = {"guest_house", "homestay", "hostel", "chalet", "bed_and_breakfast", "farm_stay"}
_FOOD_AMENITY = {"restaurant", "fast_food", "food_court", "marketplace"}
_FOOD_SHOP = {"bakery", "confectionery", "pastry"}


def classify(tags: dict) -> tuple[str, str] | None:
    """-> (category, human-readable kind), or None if this is not a Discovery place."""
    if not tags.get("name"):
        return None
    tourism, amenity, shop = tags.get("tourism"), tags.get("amenity"), tags.get("shop")
    if tourism in _HOMESTAY_TOURISM:
        kind = _KIND_BY_VALUE[tourism]
        if tags.get("guest_house") == "bed_and_breakfast":
            kind = "Bed & breakfast"
        return HOMESTAY, kind
    if amenity in _FOOD_AMENITY:
        return REGIONAL_FOOD, _KIND_BY_VALUE[amenity]
    if shop in _FOOD_SHOP:
        return REGIONAL_FOOD, _KIND_BY_VALUE[shop]
    if tourism == "viewpoint":
        return HIDDEN_GEM, "Viewpoint"
    if tourism in ("museum", "gallery", "artwork"):
        return HIDDEN_GEM, _KIND_BY_VALUE[tourism]
    if tourism == "attraction":
        # Famous places (Wikipedia/Wikidata entry) are exactly what this category avoids.
        if tags.get("wikipedia") or tags.get("wikidata"):
            return None
        return HIDDEN_GEM, "Local landmark"
    if tags.get("historic"):
        return HIDDEN_GEM, _KIND_BY_VALUE.get(tags["historic"], "Historic site")
    if tags.get("waterway") == "waterfall":
        return HIDDEN_GEM, "Waterfall"
    if tags.get("natural") in ("peak", "beach", "cave_entrance", "hot_spring"):
        return HIDDEN_GEM, _KIND_BY_VALUE[tags["natural"]]
    if tags.get("natural") == "water" and tags.get("water") == "lake":
        return HIDDEN_GEM, "Lake"
    if tags.get("leisure") in ("park", "garden"):
        return HIDDEN_GEM, _KIND_BY_VALUE[tags["leisure"]]
    if tags.get("heritage"):
        return HIDDEN_GEM, "Heritage site"
    return None


# ------------------------------------------------------------------ fields
def _clean(v) -> str | None:
    v = (v or "").strip() if isinstance(v, str) else None
    return v or None


def address_from_tags(tags: dict) -> str | None:
    if _clean(tags.get("addr:full")):
        return tags["addr:full"].strip()
    street = " ".join(x for x in (_clean(tags.get("addr:housenumber")), _clean(tags.get("addr:street"))) if x)
    parts = [_clean(tags.get("addr:housename")), street or None, _clean(tags.get("addr:suburb")),
             _clean(tags.get("addr:city")), _clean(tags.get("addr:postcode"))]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None


def pretty_cuisine(tags: dict) -> str | None:
    raw = _clean(tags.get("cuisine"))
    if not raw:
        return None
    return ", ".join(c.strip().replace("_", " ").capitalize() for c in raw.split(";") if c.strip())


def _website(tags: dict) -> str | None:
    w = _clean(tags.get("website") or tags.get("contact:website") or tags.get("url"))
    if not w:
        return None
    if not re.match(r"^https?://", w, re.I):
        # Any other scheme (javascript:, data:, ftp:...) is dropped; "host:8080/x" (a port) is not a scheme.
        if re.match(r"^[a-z][a-z0-9+.\-]*:(?!\d)", w, re.I):
            return None
        w = "http://" + w
    return w if re.match(r"^https?://[^\s]+$", w, re.I) else None


def build_place(osm_type: str, osm_id, lat: float, lng: float, tags: dict, address_hint: str | None = None) -> dict | None:
    """Normalise one OSM record. Only facts present in the tags are carried over."""
    cls = classify(tags)
    if not cls or lat is None or lng is None:
        return None
    category, kind = cls
    desc = _clean(tags.get("description:en") or tags.get("description"))
    return {
        "id": f"osm:{osm_type}/{osm_id}",
        "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "name": tags["name"].strip(),
        "category": category, "kind": kind,
        "lat": float(lat), "lng": float(lng),
        "address": address_from_tags(tags) or address_hint,
        "description": desc[:300] if desc else None,
        "cuisine": pretty_cuisine(tags) if category == REGIONAL_FOOD else None,
        "phone": _clean(tags.get("phone") or tags.get("contact:phone")),
        "website": _website(tags),
        "opening_hours": _clean(tags.get("opening_hours")),
        "source": "osm",
        "_tags": {k: tags[k] for k in ("cuisine", "brand", "brand:wikidata", "name", "shop", "amenity", "wikipedia", "wikidata") if k in tags},
    }


# --------------------------------------------------------- regional food
_INDIA = "IN"
# What each Indian state/UT is known for, in OSM `cuisine` vocabulary. Matched against
# the reverse-geocoded state (and city, for Delhi/Mumbai-type cases).
_IN_REGIONS: dict[str, list[str]] = {
    "delhi": ["north_indian", "punjabi", "mughlai", "chaat", "street_food", "kebab", "awadhi"],
    "rajasthan": ["rajasthani", "north_indian", "marwari"],
    "punjab": ["punjabi", "north_indian"],
    "haryana": ["haryanvi", "punjabi", "north_indian"],
    "uttar pradesh": ["awadhi", "mughlai", "north_indian", "chaat", "kebab"],
    "uttarakhand": ["garhwali", "kumaoni", "pahari", "north_indian"],
    "himachal pradesh": ["himachali", "pahari", "north_indian", "tibetan"],
    "jammu and kashmir": ["kashmiri", "north_indian", "wazwan"],
    "ladakh": ["ladakhi", "tibetan"],
    "gujarat": ["gujarati", "kathiyawadi", "thali"],
    "maharashtra": ["maharashtrian", "malvani", "street_food", "misal"],
    "goa": ["goan", "seafood", "portuguese"],
    "karnataka": ["karnataka", "udupi", "south_indian", "mangalorean", "kodava"],
    "kerala": ["kerala", "malabar", "south_indian", "seafood"],
    "tamil nadu": ["tamil", "chettinad", "south_indian", "idli", "dosa"],
    "andhra pradesh": ["andhra", "telugu", "south_indian", "hyderabadi"],
    "telangana": ["hyderabadi", "telangana", "andhra", "south_indian", "biryani"],
    "west bengal": ["bengali", "street_food", "seafood"],
    "odisha": ["odia", "oriya", "seafood"],
    "bihar": ["bihari", "litti_chokha", "north_indian"],
    "jharkhand": ["jharkhandi", "bihari", "north_indian"],
    "assam": ["assamese", "northeast_indian"],
    "meghalaya": ["khasi", "northeast_indian"],
    "sikkim": ["sikkimese", "tibetan", "momo", "northeast_indian"],
    "madhya pradesh": ["madhya_pradesh", "malwa", "north_indian", "poha"],
    "chhattisgarh": ["chhattisgarhi", "north_indian"],
}
_IN_COUNTRY = ["indian", "regional", "local", "thali", "sweets", "vegetarian", "street_food"]
# Other countries: the cuisine tags that mean "the local food here".
_COUNTRY_CUISINE: dict[str, list[str]] = {
    "FR": ["french", "regional", "crepe", "bistro", "brasserie"], "IT": ["italian", "regional", "pizza", "pasta"],
    "ES": ["spanish", "tapas", "regional", "basque", "catalan"], "PT": ["portuguese", "regional"],
    "DE": ["german", "regional", "bavarian"], "GR": ["greek", "regional", "mediterranean"],
    "TR": ["turkish", "regional", "kebab"], "TH": ["thai", "regional", "street_food"],
    "VN": ["vietnamese", "regional", "street_food", "pho"], "JP": ["japanese", "sushi", "ramen", "regional", "izakaya"],
    "KR": ["korean", "regional", "bbq"], "CN": ["chinese", "regional", "sichuan", "cantonese", "dumplings"],
    "MX": ["mexican", "regional", "tacos"], "NP": ["nepalese", "nepali", "tibetan", "momo", "regional", "indian"],
    "LK": ["sri_lankan", "regional", "indian"], "ID": ["indonesian", "regional", "balinese", "javanese"],
    "MA": ["moroccan", "regional", "arab"], "EG": ["egyptian", "regional", "arab"], "GB": ["british", "english", "regional", "fish_and_chips"],
    "AE": ["arab", "emirati", "lebanese", "regional"],
}
_LOCAL_NAME_HINT = re.compile(
    r"dhaba|bhojan|thali|sweets?|mithai|misthan|halwai|chaat|kebab|kabab|biryani|tandoor|paratha|rasoi|"
    r"dosa|idli|udupi|misal|vada|puri|lassi|kulfi|jalebi|samosa|pakwan|handi|bhandar", re.I)


def region_profile(region: dict | None) -> dict | None:
    """What 'regional food' means at this spot, from the reverse-geocoded area:
    {'region': {cuisines}, 'country': {cuisines}, 'label': str, 'scope': 'state'|'country'} or None
    when the place is unknown or its country has no table (then no cuisine filtering is attempted)."""
    if not region:
        return None
    cc = region.get("country_code")
    if cc == _INDIA:
        key = None
        for cand in (region.get("state"), region.get("city")):
            if cand and cand.lower() in _IN_REGIONS:
                key = cand.lower()
                break
        regional = set(_IN_REGIONS.get(key, []))
        # A region with no entry yet still gets the country-wide Indian cuisines.
        return {"region": regional, "country": set(_IN_COUNTRY), "scope": "state" if regional else "country",
                "label": (key or "india").title() if regional else "Indian"}
    if cc in _COUNTRY_CUISINE:
        return {"region": set(), "country": set(_COUNTRY_CUISINE[cc]), "scope": "country",
                "label": region.get("country") or cc}
    return None


def food_relevance(tags: dict, profile: dict | None) -> tuple[bool, str | None, str | None]:
    """Decide whether a food place belongs under "Regional food" here.
    -> (include, match, why) with match in {'region','country',None}. Chains and
    places whose only listed cuisines are foreign to the area are excluded; places
    that list no cuisine are kept only when they are the local kinds of outlet
    (bakery, sweet shop, market, street-food stall) or are named like one."""
    if tags.get("brand") or tags.get("brand:wikidata"):
        return False, None, None  # a chain, not a local speciality
    raw = tags.get("cuisine")
    cuisines = {c.strip().lower() for c in raw.split(";") if c.strip()} if raw else set()
    if profile is None:
        return True, None, None  # unknown area: do not guess, only exclude chains
    if cuisines:
        if cuisines & profile["region"]:
            return True, "region", ", ".join(sorted(c.replace("_", " ") for c in cuisines & profile["region"]))
        local = cuisines & profile["country"]
        # A broad country-level match ("indian") only counts when most of what the place
        # lists is local: "pizza;italian;crepe;indian" is an international menu, not local food.
        if local and len(local) * 2 >= len(cuisines):
            return True, "country", ", ".join(sorted(c.replace("_", " ") for c in local))
        return False, None, None  # foreign / generic international cuisine
    if tags.get("shop") in _FOOD_SHOP or tags.get("amenity") in ("marketplace", "food_court"):
        return True, None, None
    if tags.get("amenity") == "fast_food" and _LOCAL_NAME_HINT.search(tags.get("name", "")):
        return True, None, None
    if _LOCAL_NAME_HINT.search(tags.get("name", "")):
        return True, None, None
    return False, None, None
