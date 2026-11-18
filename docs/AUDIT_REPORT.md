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
