"""What counts as a hospital / pharmacy / police facility / transport option in
OpenStreetMap, and how a raw OSM record becomes a Nearby place.

Pure functions, no network. Every field on a place comes from an OSM tag or is absent:
no invented phone numbers, hours, services or schedules. "Open now" is only ever
computed from a plainly parseable `opening_hours` tag; anything else is "unknown".
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

HOSPITAL, PHARMACY, POLICE, TRANSPORT = "hospital", "pharmacy", "police", "transport"
CATEGORIES = (HOSPITAL, PHARMACY, POLICE, TRANSPORT)

OVERPASS_SELECTORS: dict[str, list[str]] = {
    HOSPITAL: [
        '["amenity"~"^(hospital|clinic)$"]',
        '["healthcare"~"^(hospital|clinic|centre)$"]',
        '["emergency"="yes"]["amenity"]',
    ],
    PHARMACY: [
        '["amenity"="pharmacy"]',
        '["healthcare"="pharmacy"]',
        '["shop"="chemist"]',
    ],
    POLICE: [
        '["amenity"="police"]',
    ],
    TRANSPORT: [
        '["railway"~"^(station|halt|tram_stop)$"]["name"]',
        '["amenity"~"^(bus_station|taxi|ferry_terminal)$"]',
        '["highway"="bus_stop"]["name"]',
        '["aeroway"~"^(aerodrome|terminal)$"]["name"]',
        '["public_transport"="station"]["name"]',
    ],
}

# Nominatim special-phrase searches (fallback when Overpass is down), cheapest/most useful first.
NOMINATIM_QUERIES: dict[str, list[str]] = {
    HOSPITAL: ["[amenity=hospital]", "[amenity=clinic]", "[healthcare=centre]"],
    PHARMACY: ["[amenity=pharmacy]", "[shop=chemist]"],
    POLICE: ["[amenity=police]"],
    TRANSPORT: ["[railway=station]", "[amenity=bus_station]", "[highway=bus_stop]", "[amenity=taxi]", "[railway=halt]"],
}

# For ranking "emergency first" on the hospital list.
_RANK = {"Emergency hospital": 0, "Hospital": 1, "Medical centre": 2, "Clinic": 2}


def _clean(v) -> str | None:
    v = v.strip() if isinstance(v, str) else None
    return v or None


def _is_gov(tags: dict) -> str | None:
    t = (tags.get("operator:type") or tags.get("operator_type") or "").lower()
    if t in ("government", "public", "community"):
        return "Government"
    if t in ("private", "private_non_profit", "religious", "ngo"):
        return "Private"
    return None


_ANIMAL = re.compile(r"v[eé]t[eé]rinaire|veterinar|\bvet\b|animal|\bpets?\b|\bdog\b|\bcat\b", re.I)


def _is_for_animals(tags: dict) -> bool:
    spec = (tags.get("healthcare:speciality") or tags.get("healthcare") or "").lower()
    return "veterinary" in spec or bool(_ANIMAL.search(tags.get("name", "")))


# --------------------------------------------------------------- classification
def classify(tags: dict) -> tuple[str, str] | None:
    """-> (category, kind) or None if this is not a Nearby place."""
    am, hc, shop = tags.get("amenity"), tags.get("healthcare"), tags.get("shop")
    if am == "police":
        pol = (tags.get("police") or "").lower()
        name = tags.get("name", "")
        if re.search(r"headquarter|commissionerate|\bhq\b", name, re.I):
            return POLICE, "Police headquarters"
        if pol in ("post", "outpost") or re.search(r"\b(out ?post|chowki|chauki|post)\b", name, re.I):
            return POLICE, "Police post"
        return POLICE, "Police station"
    if am == "pharmacy" or hc == "pharmacy":
        return PHARMACY, "Pharmacy"
    if shop == "chemist":
        return PHARMACY, "Chemist"
    if (am in ("hospital", "clinic") or hc in ("hospital", "clinic")) and _is_for_animals(tags):
        return None  # a veterinary clinic is not what someone looking for a hospital needs
    if am == "hospital" or hc == "hospital":
        if tags.get("emergency") == "yes":
            return HOSPITAL, "Emergency hospital"
        return HOSPITAL, "Hospital"
    if am == "clinic" or hc == "clinic":
        return HOSPITAL, "Clinic"
    if hc == "centre":
        return HOSPITAL, "Medical centre"
    # ---- transport
    rw = tags.get("railway")
    if rw in ("station", "halt"):
        if tags.get("station") in ("subway", "light_rail") or tags.get("subway") == "yes":
            return TRANSPORT, "Metro station"
        return TRANSPORT, "Railway station" if rw == "station" else "Railway halt"
    if rw == "tram_stop":
        return TRANSPORT, "Tram stop"
    if am == "bus_station":
        return TRANSPORT, "Bus terminal"
    if tags.get("highway") == "bus_stop":
        return TRANSPORT, "Bus stop"
    if am == "taxi":
        if tags.get("taxi") == "auto_rickshaw" or re.search(r"auto|rickshaw|tuk", tags.get("name", ""), re.I):
            return TRANSPORT, "Auto-rickshaw stand"
        return TRANSPORT, "Taxi stand"
    if am == "ferry_terminal":
        return TRANSPORT, "Ferry terminal"
    if tags.get("aeroway") in ("aerodrome", "terminal"):
        return TRANSPORT, "Airport" if tags.get("aeroway") == "aerodrome" else "Airport terminal"
    if tags.get("public_transport") == "station":
        return TRANSPORT, "Transport station"
    return None


# ------------------------------------------------------------------- contacts
def phones_from_tags(tags: dict) -> list[str]:
    """Numbers exactly as mapped, cleaned of junk; only values that look like a phone
    number (>= 5 digits) are kept, so a callable link is never built from garbage."""
    raw = ";".join(str(tags.get(k)) for k in ("phone", "contact:phone", "contact:mobile", "phone:emergency") if tags.get(k))
    out: list[str] = []
    for part in re.split(r"[;,/]", raw):
        part = part.strip()
        if re.fullmatch(r"\+?[\d\s\-().]{5,25}", part) and sum(c.isdigit() for c in part) >= 5 and part not in out:
            out.append(part)
    return out[:3]


def website_from_tags(tags: dict) -> str | None:
    w = _clean(tags.get("website") or tags.get("contact:website") or tags.get("url"))
    if not w:
        return None
    if not re.match(r"^https?://", w, re.I):
        if re.match(r"^[a-z][a-z0-9+.\-]*:(?!\d)", w, re.I):
            return None  # javascript:, data:, ftp: ...
        w = "http://" + w
    return w if re.match(r"^https?://[^\s]+$", w, re.I) else None


def address_from_tags(tags: dict) -> str | None:
    if _clean(tags.get("addr:full")):
        return tags["addr:full"].strip()
    street = " ".join(x for x in (_clean(tags.get("addr:housenumber")), _clean(tags.get("addr:street"))) if x)
    parts = [_clean(tags.get("addr:housename")), street or None, _clean(tags.get("addr:suburb")),
             _clean(tags.get("addr:city")), _clean(tags.get("addr:postcode"))]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None


# --------------------------------------------------------------- opening hours
_DAYS = ["mo", "tu", "we", "th", "fr", "sa", "su"]
_TIME = r"(\d{1,2}):(\d{2})"
_RANGE = re.compile(rf"^{_TIME}\s*-\s*{_TIME}$")


def _day_set(spec: str) -> set[int] | None:
    spec = spec.strip().lower()
    days: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        m = re.fullmatch(r"([a-z]{2})(?:\s*-\s*([a-z]{2}))?", chunk)
        if not m or m.group(1) not in _DAYS or (m.group(2) and m.group(2) not in _DAYS):
            return None
        a = _DAYS.index(m.group(1))
        b = _DAYS.index(m.group(2)) if m.group(2) else a
        days.update(range(a, b + 1) if a <= b else list(range(a, 7)) + list(range(0, b + 1)))
    return days


def _times(spec: str) -> list[tuple[int, int]] | None:
    """"09:00-13:00,16:00-21:00" -> [(540, 780), (960, 1260)]; "off"/"closed" -> []."""
    spec = spec.strip().lower()
    if spec in ("off", "closed"):
        return []
    out = []
    for part in spec.split(","):
        m = _RANGE.match(part.strip())
        if not m:
            return None
        a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
        if a > 24 * 60 or b > 24 * 60 or (a == b):
            return None
        out.append((a, b))
    return out


def opening_status(opening_hours: str | None, now_local: datetime | None) -> dict:
    """-> {"open_24h": bool, "open_now": True|False|None}.

    Only plain weekly schedules are understood ("24/7", "Mo-Sa 09:00-21:00; Su off",
    "09:00-22:00", split shifts, overnight ranges). Anything else (public holidays,
    sunrise/sunset, months, week numbers, "by appointment", free text...) -- or no
    local time to compare with -- is `None` = unknown. Never a guess."""
    unknown = {"open_24h": False, "open_now": None}
    if not opening_hours:
        return unknown
    oh = opening_hours.strip()
    if oh.lower() in ("24/7", "mo-su 00:00-24:00", "00:00-24:00"):
        return {"open_24h": True, "open_now": True}
    if now_local is None:
        return unknown
    if re.search(r"ph|sunrise|sunset|week|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\[|appointment|\"|\+", oh, re.I):
        return unknown
    # Rules apply in order, later ones override earlier ones for the days they name.
    per_day: dict[int, list[tuple[int, int]]] | None = {}
    for rule in [r.strip() for r in oh.split(";") if r.strip()]:
        m = re.match(r"^((?:[A-Za-z]{2}(?:\s*-\s*[A-Za-z]{2})?\s*,?\s*)+)\s+(.*)$", rule)
        if m:
            days, rest = _day_set(m.group(1).replace(" ,", ",")), m.group(2)
        else:
            days, rest = set(range(7)), rule
        spans = _times(rest) if days is not None else None
        if days is None or spans is None:
            return unknown
        for d in days:
            per_day[d] = spans
    wd, minute = now_local.weekday(), now_local.hour * 60 + now_local.minute
    for day_idx, offset in ((wd, 0), ((wd - 1) % 7, 1)):  # today's spans, plus yesterday's overnight tail
        for a, b in per_day.get(day_idx, []):
            if offset == 0 and ((a <= minute < b) if a < b else minute >= a):
                return {"open_24h": False, "open_now": True}
            if offset == 1 and a > b and minute < b:
                return {"open_24h": False, "open_now": True}
    return {"open_24h": False, "open_now": False}


def local_time(tz_offset_min: int | None, now_utc: datetime | None = None) -> datetime | None:
    """The wall-clock time where the user is, from the offset their device reported."""
    if tz_offset_min is None or not -14 * 60 <= tz_offset_min <= 14 * 60:
        return None
    now = now_utc or datetime.now(UTC)
    return (now + timedelta(minutes=tz_offset_min)).replace(tzinfo=None)


# -------------------------------------------------------------------- building
def _transport_details(tags: dict) -> dict:
    d = {}
    for label, keys in (("operator", ("operator",)), ("network", ("network",)), ("lines", ("line", "route_ref", "ref")),
                        ("wheelchair", ("wheelchair",))):
        for k in keys:
            if _clean(tags.get(k)):
                d[label] = tags[k].strip()
                break
    return d


def build_place(osm_type: str, osm_id, lat, lng, tags: dict, address_hint: str | None = None,
                now_local: datetime | None = None) -> dict | None:
    cls = classify(tags)
    if not cls or lat is None or lng is None:
        return None
    category, kind = cls
    name = _clean(tags.get("name"))
    if not name:
        # An unnamed hospital/clinic/police/pharmacy is still worth knowing about in an
        # emergency; it is shown as what it is, with the gap stated -- no made-up name.
        if category == TRANSPORT:
            return None
        name = f"{kind} (name not mapped)"
    phones = phones_from_tags(tags)
    oh = _clean(tags.get("opening_hours"))
    status = opening_status(oh, now_local)
    opener = _is_gov(tags) if category == HOSPITAL else None
    place = {
        "id": f"osm:{osm_type}/{osm_id}", "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "name": name, "category": category, "kind": kind,
        "operator_type": opener,
        "emergency": bool(category == HOSPITAL and (tags.get("emergency") == "yes" or kind == "Emergency hospital")),
        "lat": float(lat), "lng": float(lng),
        "address": address_from_tags(tags) or address_hint,
        "phone": phones[0] if phones else None, "phones": phones,
        "website": website_from_tags(tags),
        "opening_hours": oh, **status,
        "details": _transport_details(tags) if category == TRANSPORT else {},
        "rank": _RANK.get(kind, 4) if category == HOSPITAL else 0,
        "source": "osm",
    }
    return place
