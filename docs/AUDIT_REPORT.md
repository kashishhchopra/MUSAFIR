# MUSAFIR – Application Audit & Fix Report

Branch: `claude-app-improvements` (restore point: tag `backup-before-claude-changes`, commit `03d7d53`).
Platform focus: **Android** (iOS code left untouched). Date: 2026-11-18.

## How to read the "Testing" labels

| Label | Meaning |
|---|---|
| **Unit/auto** | Automated tests (pytest backend, vitest frontend) were run and pass. |
| **Live API** | I ran the real backend and called real external services (OpenStreetMap Nominatim, OSRM routing, Open-Meteo, MyMemory) over the internet and checked the answers. |
| **Code-read** | I read the code (and the Capacitor/plugin source where relevant) but did not execute that path. |
| **Not tested** | Could not be exercised in this environment. |

**Not done, and cannot be done from this machine:** there is no Android SDK, emulator or phone here, so **nothing was run on a real Android device**. Microphone, GPS, speech recognition, text-to-speech and "open in Google Maps" are verified only by unit tests with the device APIs faked, plus reading the Capacitor/plugin source. They need one pass on a phone (checklist at the end).

## 1. The four requested features

| Feature | Original status | Issues found | Fixes implemented | Testing | Final status |
|---|---|---|---|---|---|
| **Fair Price – location** | Pickup was `me.last_lat ?? <fixed Guwahati map centre>`; with `SIMULATE_GPS` defaulting to `true` the "current location" could be a fake random walk. Destination was a free-text guess geocoded once, no way to pick the right match. Distance had no indication whether it was routed or a straight line. No Maps link. | Hard-coded fallback location; fake GPS default; unverifiable destination; silent estimate. | Pickup is now a **fresh real GPS fix** (`useCurrentLocation`), requested only after an explanation + tap (auto only if already granted). Destination = real place search → pick from candidates (`PlaceSearch`). Backend uses **real road routing** (OSRM / Google) and now stores/returns `route_estimated` so a straight-line fallback is labelled. "Open route in Maps" button per check. Handles denied / GPS off / timeout / unsupported with specific messages and retry. Check button disabled until a real fix + destination + fare exist. Default `SIMULATE_GPS` flipped to **false**. | Unit/auto (9 FE + 4 BE tests), Live API (real routing + fare verdict) | **Working** (code + live API). GPS/Maps hand-off need device pass. |
| **Two-Way Voice Translator** | "Not requesting microphone permission". | (1) STT locale was `` `${lang}-IN` `` → `fr-IN`, `ja-IN`, `ar-IN` … invalid, recognition fails for every non-Indian language. (2) No up-front permission/explanation; permission errors indistinguishable from "mic broken". (3) If the server could not translate it returned the **original text with `demo:true`** and the card read it aloud as if translated. (4) No stop/replay controls; TTS "no voice for this language" failed silently; both speakers could listen at once. (5) Only 9 languages offered. (6) iOS has no speech plugin (out of scope now). | Correct locales (`speechLocale`). New `lib/micPermission.js` (check/request, native + browser). Permission explanation panel + "Allow microphone" button; settings guidance when blocked. Hook now checks `available()` (recogniser installed), requests the Android runtime permission, and classifies errors (`permission / no-mic / network / no-speech / language / unavailable`) each with its own message. Translation result with `demo:true` is shown as an **error, never spoken**. Stop button, "Said" vs "Hears (translated)", ▶ Play again / ⏹ Stop audio, "no voice installed" message, same-language guard, only one side can hold the mic. 21 languages via new `/translate/live-languages` (phrasebook list untouched). Android manifest declares RECORD_AUDIO, MODIFY_AUDIO_SETTINGS and `<queries>` for the speech recogniser + TTS engine (required for Android 11+). | Unit/auto (22 FE tests, 3 native-path tests), Live API (translation en↔hi real) | **Code complete.** Real recognition + speaking **need an Android phone**. |
| **Itinerary Tracker – type or upload** | Upload only. | No manual entry; no dates/times; no ordering; no travel info; confirmed itinerary showed only names. | **Two modes** (tabs). *Upload* unchanged (PDF/DOCX/TXT/photo-OCR) and still passes its tests. *Type your plan*: natural language ("visit the Taj Mahal tomorrow morning, then Agra Fort at 5pm") → new `POST /tourists/{id}/itinerary-documents/from-text` parses stops, relative/absolute dates, times and parts of day, **geocodes each stop for real**, lands in the same review screen. Review: edit names, add/remove, **reorder (▲▼)**, per-stop date + time, auto-locate. New **Travel time & distance** panel (`TripTravelInfo`): real GPS start → each stop, per-leg and total time/distance, arrival estimate, modes **Drive / Walk / Cycle** (Public transport only if a Google key is configured – never faked), route **map** with numbered stops and the real road line, **Navigate in Google Maps** (through all stops), **Save route**. Confirmed itinerary shows dates/times and the same travel panel. | Unit/auto (parser 6 BE, endpoint 3 BE, 4 + 6 FE), Live API (typed text → real coordinates → real multi-stop times) | **Working** (code + live API). Parser is rule-based, not an LLM: unusual phrasing may miss a stop (the review screen lets the user fix it). |
| **Save Route** | There was **no save-route implementation at all** (the safe-route picker could only display a recommendation). | Nothing persisted; no list; no delete. | New `saved_routes` table + Alembic migration, `POST/GET/DELETE /tourists/{id}/saved-routes`, `POST …/{id}/open`. **Save** from the safe-route picker and from the travel panel (start, destination, stops, mode, distance/time, route line, risk level, timestamps). New **Saved routes** card: list from the DB, **View on map**, **Navigate** (Google Maps from the current location through the saved stops), **Delete** (two-step confirm), empty/loading/error states, reloads after a save. Validation + ownership checks + DB-error handling (rollback → clear message). | Unit/auto (4 BE + 8 FE + 3 FE), Live API incl. **persistence across a real server restart** and delete | **Working.** |

## 2. Other hard-coded / dummy behaviour found and fixed

| Area | Problem | Fix | Testing |
|---|---|---|---|
| Weather (safety score) | With no OpenWeather key the "weather risk" was `abs(lat*100+lng*100) % 60` – a fabricated number. Disaster weather advisories were silently disabled. | Real, keyless **Open-Meteo** is now the default source (OpenWeather still used if keyed). The deterministic mock only runs if no weather service is reachable. | Unit/auto (6 new), Live API (real conditions vs mock: 5.0 vs 21.0) |
| Routing/ETA | Without a Google key every distance/ETA was a straight line at 25 km/h. | Real **OSRM** road routing (driving/walking/cycling, turn-by-turn, route geometry), 5-min cache, parallel multi-stop legs; straight-line only as a labelled last resort. | Unit/auto, Live API |
| Explore tab | Discovery, Offline trip pack and Festivals used a **default Guwahati map centre** when the tourist's position was unknown. | They now need a real position (stored GPS fix or on-request device fix) and otherwise show a location prompt. | Unit/auto (3) |
| SOS | (1) With no stored position the SOS posted `null` coordinates (server rejects → nothing sent). (2) A server error became an unhandled promise rejection – **no user feedback**. | SOS first takes a real device fix; if none, a clear "call 112" toast and no pointless request; server failures now show a toast. Emergency number injected (not hard-coded in translations). | Unit/auto (3) |
| Open-in-Maps / links | Permit "open official portal" used `window.open`, which does nothing inside the Android WebView. | Shared `openUrl()` – in the app, navigation is handed to Android (Maps/browser opens, app stays); browser uses a new tab (+ fixed a `noopener` bug in my first draft). | Unit/auto, Code-read of Capacitor `Bridge.launchIntent` |
| GPS default | `VITE_SIMULATE_GPS` defaulted to `true` (fake random walk) unless an env file overrode it. | Default is now real GPS; demo mode is opt-in (`VITE_SIMULATE_GPS=true`). Tests pin the old behaviour explicitly. | Unit/auto |

## 3. Audit of every other feature

Method used for all of these: (a) full backend suite (pytest) and frontend suite (vitest) run; (b) **API-contract check** – every `api.get/post/…` URL in the frontend (231) was matched to a route registered in FastAPI → 230 match, 1 is a dynamic template path (`/simulation${path}`) that is correct; (c) searched for dead buttons (`onClick={()=>{}}`, `href="#"`), leftover `alert/console.log`, TODO/FIXME/dummy → none in app code; (d) code reading of the cards/hooks listed below. **I did not click through each screen in a UI or on a device.** No defect found beyond those fixed above, at that depth.

| Feature / module | Original status | Issues identified | Fixes | Testing | Final status |
|---|---|---|---|---|---|
| Login / Register / Forgot password | Working | None found | – | Unit/auto, Live API (form login, seeded users) | Working |
| Digital Tourist ID + QR + hash chain | Working | None found | – | Unit/auto | Working |
| Safety score / zones / geofencing | Working | Weather input was fake (fixed above) | Open-Meteo | Unit/auto | Working |
| SOS / panic / dispatch / Emergency mode | Working | Null-coordinate SOS, silent failure (fixed above) | see §2 | Unit/auto | Working (needs device GPS pass) |
| Fall & crash detection | Working | None found | – | Unit/auto (device sensors **not tested**) | Needs real-device test |
| Walk-Me-Home / guardians / check-ins | Working | None found | – | Unit/auto | Working |
| AI anomaly detection, forecasts, ML evaluation | Working | None found | – | Unit/auto | Working |
| Smart Trip Planner, Crowd & Queue forecast | Working | Used fake weather (fixed) | Open-Meteo | Unit/auto | Working |
| Discovery (OSM Overpass), Offline trip pack, Festival calendar | Working | Default map centre (fixed); holidays need Calendarific key | Location gate | Unit/auto | Working (holidays blocked without key) |
| Multilingual phrasebook, Cultural etiquette, Currency | Working | None found | – | Unit/auto | Working |
| Permit & E-Pass | Working | Portal link did nothing in app WebView (fixed) | `openUrl` | Unit/auto | Working |
| Women's safety / safety reports, Medical profile + QR, Privacy (DPDP), Notifications (push/SMS), Response mgmt | Working | None found | – | Unit/auto; push/SMS need FCM/Twilio credentials, **not tested end-to-end** | Working / credentials needed |
| Admin dashboard, incidents, live emergencies, police network, CCTV, simulation, analytics, audit log, devices, zones | Working | None found | – | Unit/auto | Working |
| Responder console, Guardian view | Working | None found | – | Unit/auto | Working |
| Voice assistant / Safety copilot | Working | Needs an LLM (Ollama/OpenAI/Anthropic) for open-ended answers; falls back honestly | – | Unit/auto | Working (LLM optional) |

## 4. Code changes

**Created (backend):** `api/saved_routes.py`, `models/saved_route.py`, `schemas/saved_route.py`, migrations `j7e8f9a0b1c2_add_saved_routes`, `k8f9a0b1c2d3_fare_check_route_estimated`; tests `test_saved_routes.py`, `test_itinerary_text.py`, `test_fare_api.py`.
**Modified (backend):** `services/maps.py` (OSRM tier, travel modes, place search, trip legs, cache), `services/itinerary_extract.py` (natural-language parser), `services/weather.py` (Open-Meteo), `services/translation.py` + `api/translate.py` (live languages), `api/maps.py` (`/search`, `/modes`, `/route`, `/trip`), `api/itinerary.py` (`/from-text`, dated waypoints), `api/fare.py` + `schemas/fare.py` + `models/fare_check.py`, `schemas/tourist.py` + `schemas/itinerary.py` (optional `date`/`time`), `main.py`, `.env.example`, `tests/test_maps_api.py` (original 3 geocode tests kept), `tests/test_weather.py`, `tests/test_translate_api.py`.
**Created (frontend):** `hooks/useCurrentLocation.js`, `lib/mapsLinks.js`, `lib/routeService.js`, `lib/micPermission.js`, `components/LocationStatus.jsx`, `PlaceSearch.jsx`, `TripMap.jsx`, `TripTravelInfo.jsx`, `SavedRoutesCard.jsx` + 8 test files.
**Modified (frontend):** `FairPriceCard`, `TwoWayVoiceTranslator`, `ItineraryUploadCard`, `RoutePicker`, `PermitCard`, `PlanTab`, `ExploreTab`, `HomeTab`, `TouristApp`, `useTouristData`, `useSpeechRecognition`, `voiceService`, `config.js`, `vite.config.js` (test env), `AndroidManifest.xml`, all 21 locale files (new strings).
**Removed:** nothing.
**New dependencies:** none (OSRM/Nominatim/Open-Meteo are plain HTTP calls).

## 5. Configuration

* **No key is required** for any of the new functionality – it uses free services (OpenStreetMap Nominatim, OSRM at `routing.openstreetmap.de`, Open-Meteo, MyMemory). These are shared public services with fair-use limits; for a real launch, set the keys below.
* `GOOGLE_MAPS_API_KEY` (enable **Geocoding API** + **Directions API**): better routing/search quality and **unlocks "Public transport"**. Without it that mode is hidden. *Not tested – I have no key.*
* `GOOGLE_TRANSLATE_API_KEY` (Cloud Translation API): higher-quality translation; otherwise MyMemory (free, rate-limited).
* `OPENWEATHER_API_KEY`: optional now (Open-Meteo is used otherwise).
* `CALENDARIFIC_API_KEY`: unchanged – public holidays stay absent without it.
* Frontend: `VITE_SIMULATE_GPS` now defaults to **false**. Android build still needs `frontend/.env.android` (copy `.env.android.example`) – it does **not** exist in the repo yet.
* **Database migration required on deploy:** `alembic upgrade head` (adds `saved_routes`, `fare_checks.route_estimated`). Verified on a copy of your real DB and on a fresh DB.
* Android permissions are now declared explicitly in `AndroidManifest.xml` (location, microphone, audio settings, `<queries>` for speech/TTS/maps). Runtime prompts appear on first use of each feature.

## 6. Testing results

**Automated:** backend `pytest` – **1451 passed, 0 failed** (includes ~60 new tests; one real failure was caught and fixed along the way: the new `saved_routes` table had to be registered in the DPDP personal-data registry); frontend `vitest` – **815 passed** (81 files); `eslint` – 0 errors (8 pre-existing warnings); `vite build` – OK; `cap sync android` – OK (6 plugins).
**Live (real server + real internet):** place search, road routing in 3 modes, multi-stop trip, typed itinerary → coordinates → confirm, fare check with real route, saved route create → server restart → still there → delete, Open-Meteo weather, live translation both directions.

**Fully working (code + automated + live API):** saved routes, typed itinerary + travel info, real routing/ETA, Fair Price logic, weather, translation endpoint.
**Needs a real Android device:** GPS prompt and fix, microphone prompt + speech-to-text, text-to-speech voices, "Open in Google Maps" hand-off, fall detection sensors, push notifications.
**Needs credentials:** Google Maps/Translate (optional upgrades; transit routing), Calendarific, FCM push, Twilio SMS, an LLM for open-ended assistant answers.
**Not tested at all:** the Android APK build itself (no SDK), iOS (out of scope), load/performance, slow-network behaviour beyond timeouts + error messages in code.

## 7. Known limitations (honest list)

1. New UI strings exist in all 21 locale files but are **English text** in the 20 non-English files (parity tests require the keys). They need translating.
2. The natural-language parser is rule-based; it handles common phrasing (dates like tomorrow / Friday / 22 Nov, times like 5pm, morning/evening, "then/and/->" chains) but not everything. Failures are visible and editable on the review screen.
3. OSRM public servers' walking/cycling profiles are community-run; if they are down the app shows a labelled straight-line estimate.
4. SOS still needs coordinates (server requirement). With GPS off and no stored fix it tells the user to call 112 instead of sending.
5. Public-transport times exist only with a Google Maps key.

## 8. Phone test checklist (10 minutes)

1. Install the Android build; open **Explore → Fair Price**: tap *Use my current location* → system prompt → coordinates appear; search "Taj Mahal" → pick → enter a fare → verdict; tap *Open route in Maps*.
2. **Explore → Two-Way Voice Translator**: allow microphone; tap *Tap to speak* and speak English → Hindi translation appears and is read aloud; ▶ replay; deny mic once and confirm the guidance text.
3. **Plan → Add Your Itinerary → Type your plan**: "visit Taj Mahal tomorrow, then Agra Fort at 5pm" → review → *Calculate travel time* → map + times → *Save route*.
4. **Plan → Saved routes**: open on map, *Navigate*, delete; force-close and reopen the app – routes still listed.
5. Turn GPS off → repeat step 1/3 and confirm the specific messages.

---

## Addendum: Walk-Me-Home upgrade (2026-11-18)

**Before:** destination was a one-shot name lookup ("Place not found — the name alone will be shared"); the journey time was
guessed by the user from fixed buttons; no route, mode, map route, deviation or arrival logic; guardian list could show
duplicate entries and accepted any text as a phone; no SOS inside the journey; the ended journey kept the last GPS position.

**Now:**
| Area | Change | Verified by |
|---|---|---|
| Destination search | Live type-ahead (`GET /api/maps/suggest`): Photon (OSM, built for autocomplete, tolerates partial words/typos) → Nominatim/Google fallback. 450 ms debounce, ≥3 chars, request cancel, client + server cache. Worldwide. Real "not found" message only after a genuine miss. | unit + real-service run ("Leela Ambience", "AIIMS Delhi", "Louvre", typo "ambiance", gibberish → none) |
| Route + duration | Device GPS → OSRM road route for walk / cycle / drive (transit only with a Google key). Distance, travel time, ETA. Deadline = travel + `max(25 %, 5 min)` buffer (server-configurable). Recalculates on destination/mode change. Failure/estimate-only is labelled; manual time is an explicit fallback. | unit + live (Delhi: walk 2.95 km/39 min → 50 min deadline) |
| Contacts | Phone/email validation + normalisation, duplicate refusal (409), edit, remove (confirm), server reason shown. Per-journey selection; only selected contacts' links resolve. | unit + live |
| Live journey | Route + live dot on map, km/min left along the route, off-route prompt (2 precise fixes > 200 m) with recalc, arrival prompt (2 fixes ≤ 100 m; auto-end only if the user turns it on), offline banner. | unit (jsdom) |
| SOS | Two-tap SOS in the journey; sends journey details through the normal SOS pipeline; shows sent (incident id) / queued / failed — never "sent" unless the server accepted it. | unit + live (incident created) |
| Privacy | Position wiped when a journey ends; "Clear history" endpoint + button. | unit + live |

**Not verified:** anything on a real phone (GPS, background behaviour), SMS/push delivery to guardians (needs Twilio/FCM
credentials), check-in reminders as OS notifications (the in-app timer and server escalation are tested; browser
notifications are not implemented), transit routing (needs `GOOGLE_MAPS_API_KEY`).
**Limits:** public Photon/Nominatim/OSRM are fair-use — self-host and set `PHOTON_URL` / `NOMINATIM_URL` for real traffic.
The route plan (geometry) is stored on the device only, so a journey resumed on a different device has no route line.
New UI strings are English in the other 20 languages.

---

## Addendum: phone navigation bar (Back / Home / Recent screens)

Tourist app, phone widths only (`md:hidden`; desktop layout untouched). Screens are now real URLs on the existing React
Router (`/app`, `/app?tab=plan|explore|help|me`), so browser back, refresh and deep links work; unknown `tab` values fall
back to Home. `hooks/useTouristNav.js` keeps an in-app history stack, a de-duplicated recent-screens list (sessionStorage,
screen names only, wiped on logout) and per-screen scroll position. `lib/navBus.js` lets Back close the topmost open
sheet/panel before changing screen and lets Home close them all without touching data, SOS or a running journey; the
Android hardware back key uses the same path and only exits the app from the root. A logged-out visit to a protected URL
goes to login and returns to it afterwards (tourist `/app…` paths only). Pinned controls (tab bar, SOS, assistants,
journey strip, emergency card) reserve the bar's height through `--sysnav-h`.

**Tested:** 37 new automated tests (jsdom) covering all three buttons, history, duplicates, deep links, overlay-first back,
Home during an active SOS, recents, login return/redirect safety, hardware back; generated CSS checked.
**Not tested:** visual layout on a real phone/emulator and the PWA install; Chrome device-mode check recommended.
**Known limit:** tab *content* state (e.g. half-typed form text) is not kept when switching screens, only scroll position;
journeys, SOS and data live above the tabs and are unaffected.

---

## Addendum: Discovery (hidden gems / regional food / homestays)

**Why it showed "No places found nearby yet" (three causes):**
1. The live OpenStreetMap lookup (Overpass) is currently failing on the public servers (HTTP 502 "at capacity" / timeouts), and the
   code swallowed that error and fell back to a database table holding a handful of *seeded demo rows for Guwahati* -> empty anywhere else.
2. The tab used the *stored profile* coordinates, not the phone's GPS, so searches could be for the wrong city.
3. Nothing told the user about any of it: a failure and "nothing here" looked identical.

**What changed:**
| Area | Change |
|---|---|
| Data | Overpass (several endpoints, failing ones skipped 2 min) -> automatic fallback to Nominatim category searches inside the radius -> stale cached copy -> otherwise HTTP 503. No seeded/demo rows. Cached 1 h per ~1 km cell. |
| Honesty | Outage = error with Retry; backup source and stale copies are labelled; empty = "No X within N km" with *Search wider* / *Change location*. Descriptions, cuisine, phone, website, hours appear only if OSM has them. No ratings, no availability claims (homestays carry a "not confirmed" note). |
| Hidden gems | viewpoints, historic sites, parks/gardens, lakes, waterfalls, peaks/beaches/caves, museums/galleries, public art, heritage, and "attractions" only when they have no Wikipedia/Wikidata entry (i.e. not the famous ones). |
| Regional food | restaurants, street food, bakeries, sweet shops, markets. Region from reverse geocoding (Indian states + ~20 countries): chains and foreign-cuisine places are dropped, local ones badged; areas/countries without a table are only de-chained. |
| Homestays | guest houses, homestays, B&Bs, hostels, chalets, farm stays (hotels excluded) with address/phone/website when present. |
| Location | device GPS on open (permission prompt), manual place search when refused/off/timed out, "You are near <area>" (reverse geocoded). |
| UI | filters with counts, radius 2/5/10/25/50 km, sort (nearest / best local match / name), text search, list + Leaflet map (pin per category, popups, fit-to-results), skeletons, Directions (Google Maps link, no key), refresh, paging. |

**Tested:** 26 backend tests (tag logic, region filtering, fallback order, cache, circuit breaker, 503 vs empty, API validation) + 37 frontend tests (GPS accept/deny/timeout/unsupported, manual location, every filter, radius debounce, sort, search, map pins, directions URLs, empty/error/offline/partial). **Live** against real OSM services for Delhi, Jaipur, Manali, Paris: only the requested category, nothing outside the radius, distances match an independent haversine, nearest-first.
**Not tested / limits:** not on a real phone; the public Overpass was down during testing, so the live checks exercised the Nominatim fallback (cold start ~20-30 s for all three categories; results stream in per category) -- run your own Overpass for speed and volume. OSM coverage varies: many restaurants carry no `cuisine` tag, so regional matching is conservative and small towns can legitimately return little. "Hidden" is inferred from tags (no popularity data exists), so a well-known museum can still appear. New UI strings are English-only in the other 20 languages.

---

## Addendum: Nearby (hospital / pharmacy / police / transport)

**Why it said "Nothing found nearby yet":**
1. The backend ignored the phone's location and read the *stored profile coordinate*; when that was empty (`last_lat is None`) it returned `[]`,
   which the screen showed as "nothing nearby". The frontend never sent any position at all.
2. Pharmacy and transport read *seeded demo rows* (Guwahati); hospital and police read a local table of an old OSM import. No live lookup anywhere.
3. A failure and "nothing here" were indistinguishable.

**What changed:**
| Area | Change |
|---|---|
| Position | Device GPS on open (permission prompt), place search when refused / off / timed out, "You are near <area>" (reverse geocoded); every failure named. New `GET /api/nearby?lat&lng&category&radius_km` -- the old stored-position endpoint is removed. |
| Data | Overpass (several endpoints, failing ones skipped 2 min) -> Nominatim category searches in widening boxes (nearest first, one retry on a timeout) -> for hospital/police only, the saved OSM import (labelled "saved copy", no phone numbers) -> otherwise HTTP 503. Shared with Discovery in `services/osm_source.py`. Cached per cell/radius/category. |
| Hospital | hospitals, emergency hospitals, clinics, medical centres (not single-doctor practices, not veterinary); government/private when mapped; "Emergency first" or nearest sort. |
| Pharmacy | pharmacies/chemists; "Open 24 hours" for 24/7; "Open now / Closed now" **only** when `opening_hours` is plainly parseable and the user's own clock is known (live position); otherwise nothing is claimed. "Only confirmed open" filter. |
| Police | stations, posts, headquarters. |
| Transport | metro, railway, bus stops/terminals, taxi and auto-rickshaw stands, ferry, airports, with operator/network/lines when mapped; no timetable or "running now" claims. |
| Actions | Call button only for a number actually mapped; Directions (Google Maps link, no key); "Time & route" (OSRM distance + duration, walk/drive, line on the map); Nearest hospital / Nearest police shortcuts; emergency-number call link always visible; refresh. |
| UI | 4 category filters with counts, radius 1/2/5/10/25/50 km, search, sort, list + Leaflet map (own pin per category), skeletons, honest empty/error/offline notes. |

**Tested:** 30 backend tests (tags, phones, opening hours, region, fallback order incl. saved data, caching, 503 vs empty, validation) + 40 frontend tests
(GPS accept/deny/timeout, manual place, each category, radius, open-now, call/directions/route, map pins, empty/error/offline). **Live** against the real map
services -- Delhi (all four categories), Paris (hospital), Manali (pharmacy): only the requested kind, nothing outside the radius, distances match an independent
haversine, nearest-first, no duplicate ids, phones only where mapped. Bugs found by testing and fixed: a crash while a category was first loading; a single Nominatim
timeout failing a whole police lookup (now retried once); veterinary clinics and single doctors in the hospital list.
**Not tested / limits:** not on a real phone. The public Overpass was down during testing, so live runs exercised the Nominatim fallback (first search 5-25 s) --
run your own Overpass for speed and volume. OpenStreetMap data is volunteer-made: many places have no phone/hours, some are mis-tagged (e.g. police-run
booths mapped as police), and "open now" is unknown for any hours the parser does not fully understand. Distances are straight-line; the route button gives road
time without traffic. New UI strings are English-only in the other 20 languages.

---

## Addendum: features removed from the app (2026-11-18, on request)

Removed from the tourist app UI and frontend code: **Saved routes** (card plus the "Save route" buttons in the Safe-route picker and the itinerary
travel panel, which only fed that list), **Festival & Local Events**, and the **Multilingual Guide** phrasebook. Their components, tests, client code and
translation strings (21 languages) are deleted. Everything else in those screens (route finding, "Navigate in Google Maps", travel times, the Translate
card and the two-way voice translator) is unchanged.

**Left in place on purpose (now unused by the app):** the backend endpoints and data -- `/tourists/{id}/saved-routes` + the `saved_routes` table (registered in
the DPDP personal-data registry; existing rows are untouched), `/festivals`, and `/translate/guide/*`. Removing them would be a database migration and
needs a decision about existing saved data; nothing calls them. Say the word to remove them as well.

---

## Addendum: Crowd forecast made real (now "Crowd Outlook")

**What was hardcoded:** the card listed the app's own *fixture zones* ("City Center Safe Zone", "Auto Hot-Zone #0 (DBSCAN)"...) -- the same list for every user, nothing to do with
where they are -- and gave each the same sentence, because the "forecast" added fixed bumps (+1 peak season, +1 fair weather...) so almost everything came out "High".

**The constraint:** no free source of live crowd counts exists anywhere (Google's "popular times" is paid; OpenStreetMap has no footfall). A measured crowd level is therefore not
possible; what *is* possible is an honest, explainable estimate built from real inputs about the user's actual position -- and it says so on screen.

**What it is now** (`services/crowd_outlook.py`, `GET /api/crowd/outlook?lat&lng`): for the device's GPS position (or a typed place), the next 12 hours as quiet / moderate / busy /
very busy, "right now", the quietest daytime hour in the next 24 h, and every factor named with its direction. Inputs: the real local time at that spot; real hourly weather
(Open-Meteo: rain chance, heat, cold, storms); real public holidays; what OpenStreetMap shows within 1 km (attractions, markets, stations -> few / some / many); weekday vs weekend;
India's Oct-Mar season (India only, labelled a general assumption). The one fixed assumption is the daily activity curve (people are out by day and early evening). The three slow
free services are asked in parallel; the surroundings lookup has a 9 s budget and finishes in the background (the app re-checks), so the card is never blocked on it.
Missing inputs are said on screen (weather / holidays / surroundings), never silently ignored.

**Tested:** 21 backend tests (model responds to hour, weekend, holiday, rain/heat/cold/storm, area; one line per weather kind; India-only season; fallbacks; slow-lookup budget; API) + 17 frontend
tests. **Live** (real weather + OSM, Delhi / Tokyo / Sydney / Paris / Manali): correct local time per place, different results per place and hour.
**Limits:** it is an estimate, not a count; the busyness ceiling and the factor weights are modelling choices, not calibrated against real footfall. **Public holidays need one optional
package:** the code uses the open-source `python-holidays` library if installed (covers India and ~200 countries, no key) and otherwise the Calendarific-key service; with neither, the card
states that holidays are not factored in. Install with `pip install holidays` and add it to `requirements.txt`. The old per-zone endpoints (`/crowd/forecast`, queue estimates) remain on the server but the app no longer uses them.
