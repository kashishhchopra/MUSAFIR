"""Itinerary document upload: real text extraction from PDF/DOCX/plain-text/
image documents, then a heuristic parse into a structured trip
(destinations, hotels, transport, activities, trip dates).

Text extraction from PDF (pypdf) and DOCX (python-docx) is real -- both are
pure-Python and read the actual file content, no external service involved.
Photos/scans (jpg/png/webp/...) go through real OCR via Tesseract
(pytesseract + a local `tesseract` binary) when one is installed on the
host; this is the same "real API/engine when available, honest fallback
when not" pattern used elsewhere in this project (see services/maps.py,
services/translation.py) -- Tesseract needs no API key, but a minimal
container image may not have the `tesseract-ocr` package installed, so this
is checked at request time, not assumed. When OCR genuinely isn't
available, `ExtractionError` explains why and the tourist falls back to
typing their itinerary in by hand -- never a silent empty result. A scanned
*PDF* (no embedded text layer) still can't be OCR'd here (that needs
rasterizing PDF pages, a heavier dependency not included) -- the tourist is
told to re-upload it as a photo instead, which the OCR path above handles.

The structure parser itself is a set of readable heuristics (date regexes,
"City -> City" / arrow sequences, Hotel/Flight/Train keyword lines), not a
model -- it will not perfectly parse every itinerary format, which is why
every upload lands in front of the tourist for review/edit before it's
confirmed into their profile (see app/api/itinerary.py).
"""
from __future__ import annotations

import io
import re
from datetime import datetime

from app.services import maps

# Formats we can pull real text out of. Anything else is refused with a
# clear reason rather than silently producing nothing.
_SUPPORTED_TEXT_TYPES = {"text/plain"}
_SUPPORTED_PDF_TYPES = {"application/pdf"}
_SUPPORTED_DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_SUPPORTED_IMAGE_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/bmp", "image/tiff",
}
_SUPPORTED_IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "bmp", "tiff", "tif"}


class ExtractionError(Exception):
    """Raised when a document's text can't be pulled out at all -- the
    caller should surface `str(err)` to the tourist and offer manual entry."""


def _ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def extract_text(filename: str, content: bytes, content_type: str) -> str:
    """Pull raw text out of an uploaded itinerary file. Real extraction for
    .txt/.pdf/.docx; anything else (image formats especially) raises
    ExtractionError explaining why, rather than returning nothing silently."""
    ext = _ext_of(filename)

    if content_type in _SUPPORTED_TEXT_TYPES or ext == "txt":
        try:
            return content.decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 -- surfaced to the tourist, not swallowed
            raise ExtractionError(f"Could not read this file as text: {e}") from e

    if content_type in _SUPPORTED_PDF_TYPES or ext == "pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(pages).strip()
            if not text:
                raise ExtractionError(
                    "This PDF has no extractable text (it may be a scanned image) -- "
                    "OCR is not available in this deployment. Please enter your itinerary manually."
                )
            return text
        except ExtractionError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ExtractionError(f"Could not read this PDF: {e}") from e

    if content_type in _SUPPORTED_DOCX_TYPES or ext == "docx":
        try:
            import docx
            document = docx.Document(io.BytesIO(content))
            text = "\n".join(p.text for p in document.paragraphs).strip()
            if not text:
                raise ExtractionError("This document appears to be empty.")
            return text
        except ExtractionError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ExtractionError(f"Could not read this DOCX file: {e}") from e

    if content_type in _SUPPORTED_IMAGE_TYPES or ext in _SUPPORTED_IMAGE_EXTS or content_type.startswith("image/"):
        return _extract_image_text(content)

    raise ExtractionError(f"Unsupported file type ({content_type or ext or 'unknown'}).")


def _extract_image_text(content: bytes) -> str:
    """OCR a photographed/scanned itinerary via Tesseract. Real when a local
    `tesseract` binary is installed; otherwise raises ExtractionError with a
    clear explanation rather than pretending to have read the image."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise ExtractionError(
            "Photo/scan reading (OCR) isn't available in this deployment -- "
            "please enter your itinerary manually, or upload a text/PDF/DOCX version instead."
        ) from e

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except Exception as e:  # noqa: BLE001 -- surfaced to the tourist, not swallowed
        raise ExtractionError(f"Could not read this image file: {e}") from e

    try:
        text = pytesseract.image_to_string(image).strip()
    except pytesseract.TesseractNotFoundError as e:
        raise ExtractionError(
            "Photo/scan reading (OCR) isn't available in this deployment -- "
            "please enter your itinerary manually, or upload a text/PDF/DOCX version instead."
        ) from e
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"Could not read text from this image: {e}") from e

    if not text:
        raise ExtractionError(
            "No readable text was found in this photo -- try a clearer, well-lit shot, "
            "or enter your itinerary manually."
        )
    return text


# ---------------------------------------------------------------- parsing
_DATE_PATTERNS = [
    # "01 Sep 2026", "1 September 2026"
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
    # "2026-09-01"
    r"\b(\d{4}-\d{2}-\d{2})\b",
    # "01/09/2026" or "09/01/2026"
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
]
_HOTEL_KEYWORDS = re.compile(r"\b(hotel|inn|resort|residency|lodge|guest\s?house|homestay)\b", re.I)
_TRANSPORT_KEYWORDS = re.compile(r"\b(flight|train|bus|cab|taxi|pnr|boarding|departure|arrival)\b", re.I)
_CHECKIN_KEYWORDS = re.compile(r"check[-\s]?in", re.I)
_CHECKOUT_KEYWORDS = re.compile(r"check[-\s]?out", re.I)
_ARROW_SPLIT = re.compile(r"\s*(?:→|->|-{2,}>|,|→)\s*")
_DAY_HEADER = re.compile(r"^\s*day\s*\d+\s*[:.\-]?\s*(.*)$", re.I)
# OCR on a real photo sometimes reads several "Day N" headers onto one
# physical line (columns/labels running together) -- a body that's nothing
# but repeated "Day N" tokens isn't a real destination name, it's noise, and
# geocoding it just wastes a lookup and never resolves to anywhere real.
_DAY_NOISE = re.compile(r"^(?:day\s*\d+\s*[:.\-]?\s*)+$", re.I)


def _is_day_noise(s: str) -> bool:
    return bool(_DAY_NOISE.match(s.strip()))


def _try_parse_date(token: str) -> str | None:
    formats = ["%d %b %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]
    for fmt in formats:
        try:
            return datetime.strptime(token, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_dates(text: str) -> list[str]:
    found = []
    for pattern in _DATE_PATTERNS:
        for m in re.finditer(pattern, text):
            parsed = _try_parse_date(m.group(1))
            if parsed and parsed not in found:
                found.append(parsed)
    return sorted(found)


def parse_itinerary_text(text: str) -> dict:
    """Heuristic structured parse of raw itinerary text -- destinations
    (an arrow-separated or "Day N:" sequence), hotels, transport legs, and
    loose activity lines, plus any dates found anywhere in the document.
    Always returns a dict (never raises) -- an itinerary that doesn't match
    any heuristic just comes back mostly empty, for the tourist to fill in
    by hand on the review step."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    destinations: list[dict] = []
    hotels: list[dict] = []
    transport: list[dict] = []
    activities: list[str] = []

    for line in lines:
        day_match = _DAY_HEADER.match(line)
        body = day_match.group(1) if day_match else line

        if _HOTEL_KEYWORDS.search(body):
            hotels.append({
                "name": body,
                "check_in": bool(_CHECKIN_KEYWORDS.search(body)),
                "check_out": bool(_CHECKOUT_KEYWORDS.search(body)),
            })
            continue

        if _TRANSPORT_KEYWORDS.search(body):
            transport.append({"detail": body})
            continue

        # "Delhi -> Agra -> Jaipur" or comma-separated city sequences read as
        # a destination chain; a plain single short line under a "Day N:"
        # header is treated as one destination for that day.
        parts = [p.strip() for p in _ARROW_SPLIT.split(body) if p.strip()]
        if len(parts) > 1:
            for p in parts:
                if len(p) <= 60 and not _is_day_noise(p):
                    destinations.append({"name": p})
            continue

        if day_match and body and len(body) <= 60:
            if _is_day_noise(body):
                activities.append(line)
            else:
                destinations.append({"name": body})
            continue

        if len(body) <= 200:
            activities.append(body)

    # Best-effort coordinates for recognizable destinations (static
    # gazetteer / geocoding service -- see services/maps.py). Unresolved
    # names still show up in the timeline, just without a map pin.
    for dest in destinations:
        located = maps.geocode(dest["name"])
        if located:
            dest["lat"] = located["lat"]
            dest["lng"] = located["lng"]
            dest["location_demo"] = located["demo"]

    dates = _extract_dates(text)
    return {
        "trip_start": dates[0] if dates else None,
        "trip_end": dates[-1] if len(dates) > 1 else None,
        "destinations": destinations,
        "hotels": hotels,
        "transport": transport,
        "activities": activities[:30],  # cap -- avoid dumping the whole document verbatim
    }


# ------------------------------------------------- natural-language input
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_MONTH_RE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_NL_DATE_RES = [
    (re.compile(r"\bday after tomorrow\b", re.I), "dat"),
    (re.compile(r"\btomorrow\b", re.I), "tomorrow"),
    (re.compile(r"\b(?:today|tonight)\b", re.I), "today"),
    (re.compile(r"\b(?:next|on|this)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.I), "weekday"),
    (re.compile(rf"\b(?:on\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})(?:\s+(\d{{4}}))?\b", re.I), "dmy"),
    (re.compile(rf"\b(?:on\s+)?({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b", re.I), "mdy"),
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"), "iso"),
]
_NL_TIME_RE = re.compile(
    r"\b(?:at|around|by|from)?\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\b(?:at|around|by)\s+(\d{1,2}):(\d{2})\b", re.I)
_NL_PART_OF_DAY = re.compile(r"\b(?:in the |this )?(morning|afternoon|evening|night)\b", re.I)
_PART_OF_DAY_TIME = {"morning": "09:00", "afternoon": "14:00", "evening": "18:00", "night": "20:00"}
_NL_SPLIT = re.compile(r"\s*(?:;|\n|->|→|\bthen\b|\band then\b|\bafter that\b|\bafterwards\b|\bfollowed by\b|\bnext\b|\bfinally\b|,|\band\b|\bplus\b)\s*", re.I)
_NL_INTENT = re.compile(
    r"^\s*(?:(?:i|we)\b\s*(?:'d|would|will|'ll|want|wanna|plan|am going|are going|intend|need|have)?\s*"
    r"(?:like\s+)?(?:to\s+)?|let'?s\s+|please\s+|also\s+|first\s+|then\s+)*"
    r"(?:(?:plan(?:\s+a)?(?:\s+trip)?|(?:a\s+)?trip|go|going|head|heading|travel|travelling|traveling|drive|walk|visit|visiting|see|explore|reach|stop(?:\s+by)?|"
    r"take me|tour|check out|stay)\b\s*(?:(?:to|at|in|by|around)\b)?\s*)*", re.I)
_NL_NOT_A_PLACE = {"hello", "hi", "hey", "ok", "okay", "thanks", "thank you", "yes", "no", "help", "me", "there", "here"}
_NL_TRAILING = re.compile(r"\b(?:for\s+(?:about\s+)?\d+\s*(?:hours?|hrs?|days?|minutes?|mins?)|please|too|as well)\b", re.I)


def _resolve_nl_date(kind: str, m: re.Match, today) -> str | None:
    from datetime import timedelta
    try:
        if kind == "today":
            return today.isoformat()
        if kind == "tomorrow":
            return (today + timedelta(days=1)).isoformat()
        if kind == "dat":
            return (today + timedelta(days=2)).isoformat()
        if kind == "weekday":
            target = _WEEKDAYS.index(m.group(1).lower())
            ahead = (target - today.weekday()) % 7 or 7
            return (today + timedelta(days=ahead)).isoformat()
        if kind == "iso":
            return datetime.strptime(m.group(1), "%Y-%m-%d").date().isoformat()
        day, mon, year = (m.group(1), m.group(2), m.group(3)) if kind == "dmy" else (m.group(2), m.group(1), m.group(3))
        month = _MONTHS.index(mon[:3].lower()) + 1
        y = int(year) if year else today.year
        d = datetime(y, month, int(day)).date()
        if not year and d < today:
            d = d.replace(year=y + 1)
        return d.isoformat()
    except (ValueError, IndexError):
        return None


def _pull_date(clause: str, today) -> tuple[str, str | None]:
    for rx, kind in _NL_DATE_RES:
        m = rx.search(clause)
        if m:
            found = _resolve_nl_date(kind, m, today)
            if found:
                return (clause[:m.start()] + " " + clause[m.end():]), found
    return clause, None


def _pull_time(clause: str) -> tuple[str, str | None]:
    m = _NL_TIME_RE.search(clause)
    if m:
        if m.group(3):
            hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
            minute = int(m.group(2) or 0)
        else:
            hour, minute = int(m.group(4)), int(m.group(5))
        if hour < 24 and minute < 60:
            return (clause[:m.start()] + " " + clause[m.end():]), f"{hour:02d}:{minute:02d}"
    m = _NL_PART_OF_DAY.search(clause)
    if m:
        return (clause[:m.start()] + " " + clause[m.end():]), _PART_OF_DAY_TIME[m.group(1).lower()]
    return clause, None


def parse_natural_language(text: str, today=None, geocode=True) -> dict:
    """Turn free text ("I want to visit the Taj Mahal tomorrow, then Agra Fort
    at 5pm") into the same structure `parse_itinerary_text` returns, with each
    destination carrying an optional `date` (ISO) and `time` (HH:MM). A date or
    time stated once carries forward to later stops until another is stated.
    Destination names are geocoded for real (Google/Nominatim); one that can't
    be placed stays unlocated for the tourist to fix -- never guessed. Always
    returns a dict; no destinations found just means an empty list."""
    from datetime import date as _date

    today = today or _date.today()
    # Dates like "5 Dec, 6 Dec" contain commas that the clause splitter would cut
    # apart; protect them by pulling dates out of the whole text first, per clause.
    text = text.strip()
    clauses = [c for c in _NL_SPLIT.split(text) if c and c.strip()]
    destinations: list[dict] = []
    carried_date: str | None = None
    carried_time: str | None = None
    all_dates: list[str] = []

    for raw in clauses:
        clause, d = _pull_date(raw, today)
        clause, tm = _pull_time(clause)
        if d:
            carried_date = d
            all_dates.append(d)
        if tm:
            carried_time = tm
        name = _NL_INTENT.sub("", clause)
        name = _NL_TRAILING.sub("", name)
        name = re.sub(r"^(?:the|a|an|my)\s+", "", name.strip(), flags=re.I)
        name = re.sub(r"\b(?:on|at|in|by|from|around|to)\s*$", "", name.strip(), flags=re.I)
        name = re.sub(r"\s+", " ", name).strip(" .!?:-")
        if name.lower() in _NL_NOT_A_PLACE:
            name = ""
        if not name or len(name) > 60 or len(name) < 2:
            # Pure "tomorrow"/"at 5pm" clauses attach to the stop before them.
            if (d or tm) and destinations and not name:
                if d and not destinations[-1].get("date"):
                    destinations[-1]["date"] = d
                if tm and not destinations[-1].get("time"):
                    destinations[-1]["time"] = tm
            continue
        entry: dict = {"name": name.title() if name.islower() else name}
        if carried_date:
            entry["date"] = carried_date
        if carried_time and (tm or not destinations):
            entry["time"] = carried_time
        destinations.append(entry)

    if geocode:
        for dest in destinations:
            located = maps.geocode(dest["name"])
            if located:
                dest["lat"], dest["lng"] = located["lat"], located["lng"]
                dest["location_demo"] = located["demo"]

    dates = sorted(set(all_dates))
    return {
        "trip_start": dates[0] if dates else None,
        "trip_end": dates[-1] if len(dates) > 1 else None,
        "destinations": destinations, "hotels": [], "transport": [], "activities": [],
    }
