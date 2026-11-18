"""Application configuration — fully environment-driven, safe defaults for dev.

Nothing security-sensitive is hardcoded. In production (ENVIRONMENT=production)
the app refuses to start unless a strong SECRET_KEY is provided.
"""
from __future__ import annotations

import os
import secrets
import warnings
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_SEED_PASSWORD_VARS = ("SEED_ADMIN_PASSWORD", "SEED_TOURIST_PASSWORD", "SEED_RESPONDER_PASSWORD")
# Demo/default credentials that must never be live in production.
KNOWN_WEAK_PASSWORDS = frozenset({
    "admin123", "tourist123", "responder123", "password", "password123",
    "admin", "123456", "12345678", "changeme", "change_this_strong_password",
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ---- environment ----
    ENVIRONMENT: str = "development"  # development | production
    PROJECT_NAME: str = "MUSAFIR — Smart Tourist Safety Monitoring & Incident Response System"
    API_V1_PREFIX: str = "/api"

    # ---- database ----
    # SQLite for dev; set DATABASE_URL=postgresql+psycopg://user:pass@host/db in prod
    DATABASE_URL: str = "sqlite:///./tourist_safety.db"
    DB_POOL_SIZE: int = 20
    DB_POOL_MAX_OVERFLOW: int = 20

    # ---- auth / JWT ----
    # Leave empty to auto-generate an ephemeral key in dev (tokens reset on restart).
    SECRET_KEY: str = ""
    # Where a generated dev key is cached so it survives restarts (dev only).
    DEV_SECRET_FILE: str = ".dev_secret"
    ALGORITHM: str = "HS256"
    # Short-lived on purpose: the refresh token (below) is what's actually
    # revocable, so access tokens should expire quickly rather than need
    # per-request denylist checks.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # ---- refresh-token cookie ----
    # The refresh token is set as an httpOnly cookie so it's never readable
    # by JS (unlike the access token, which the frontend keeps in memory) --
    # closes the XSS-exfiltration path on the one token that's actually
    # long-lived. Secure should be True behind HTTPS (i.e. always in prod);
    # False here only so plain-HTTP local dev still works.
    REFRESH_COOKIE_NAME: str = "refresh_token"
    REFRESH_COOKIE_PATH: str = "/api/auth"
    REFRESH_COOKIE_SECURE: bool = False
    REFRESH_COOKIE_SAMESITE: str = "strict"

    # ---- password policy ----
    MIN_PASSWORD_LENGTH: int = 8

    # ---- rate limiting (per client IP) ----
    RATE_LIMIT_ENABLED: bool = True
    LOGIN_RATE_LIMIT: int = 10          # attempts
    LOGIN_RATE_WINDOW_SECONDS: int = 300
    GLOBAL_RATE_LIMIT: int = 240        # requests
    GLOBAL_RATE_WINDOW_SECONDS: int = 60
    REGISTRATION_RATE_LIMIT: int = 5    # public digital-ID registrations
    REGISTRATION_RATE_WINDOW_SECONDS: int = 3600

    # ---- request hardening ----
    MAX_REQUEST_BODY_BYTES: int = 1_000_000  # 1 MB

    # ---- ML ----
    ML_MODELS_DIR: str = "ml_models"

    # ---- notifications ----
    # "console" logs instead of sending (default -- no external service
    # needed). See app/services/notifications.py for the extension point.
    NOTIFICATION_CHANNEL: str = "console"   # console | twilio (SMS)
    # Public origin of the frontend, used for deep links inside push/SMS text.
    PUBLIC_BASE_URL: str = "http://localhost:5173"

    # ---- Web Push (VAPID) -- services/notifications.py ----
    # Generate once:  python -m app.scripts.generate_vapid_keys
    # Left empty in development the server creates (and persists) a key pair
    # automatically so push works locally; in production push stays disabled
    # until real keys are configured.
    PUSH_ENABLED: bool = True
    VAPID_PUBLIC_KEY: str = ""
    VAPID_PRIVATE_KEY: str = ""
    VAPID_SUBJECT: str = "mailto:alerts@musafir.example"
    # ---- Firebase Cloud Messaging -- push to the Android app ----
    # Path to a Firebase service-account JSON key (Firebase console -> Project
    # settings -> Service accounts). Empty = the Android app gets no push.
    FCM_CREDENTIALS_FILE: str = ""

    # ---- Email (SMTP) -- password-reset codes; services/notifications.py ----
    # Defaults suit Gmail: set SMTP_USERNAME to the Gmail address and
    # SMTP_PASSWORD to a Google *App Password* (Google Account -> Security ->
    # 2-Step Verification -> App passwords), never the normal Gmail password.
    # Left empty, emails are not sent (in development they are logged instead).
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587                     # STARTTLS
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""                      # defaults to SMTP_USERNAME
    SMTP_FROM_NAME: str = "MUSAFIR"
    SMTP_TIMEOUT_SECONDS: int = 20

    # ---- Native iOS push (APNs) -- services/apns.py. Needs a paid Apple Developer
    # account: create an APNs Auth Key (.p8) and paste its contents (\n allowed).
    APNS_KEY_ID: str = ""
    APNS_TEAM_ID: str = ""
    APNS_BUNDLE_ID: str = "com.musafir.app"   # same app id as Android
    APNS_AUTH_KEY: str = ""
    APNS_USE_SANDBOX: bool = True            # True for Xcode/TestFlight-debug builds, False for App Store/TestFlight

    # ---- Twilio SMS -- used when NOTIFICATION_CHANNEL=twilio ----
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""             # E.164, e.g. +15551234567
    TWILIO_MESSAGING_SERVICE_SID: str = ""   # alternative to FROM_NUMBER
    SMS_DEFAULT_COUNTRY_CODE: str = "+91"    # for numbers stored without a +country prefix
    # Comma-separated E.164 numbers. When set, SMS is sent ONLY to these (use it
    # while testing so a seeded/demo contact number can never be texted).
    SMS_ALLOWLIST: str = ""

    # ---- delivery pipeline ----
    NOTIFICATIONS_SYNC: bool = False         # send inline instead of on a worker thread (tests set this)
    NOTIFICATION_MAX_ATTEMPTS: int = 3
    NOTIFICATION_RETRY_BASE_SECONDS: int = 30   # backoff: 30s, 120s, 480s ...
    NOTIFICATION_RETRY_TICK_SECONDS: int = 15
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # ---- Fair Price: local transport fare rate card (services/fare.py) ----
    # Base fare (INR) + per-km + per-minute rate for each transport type.
    # Defaults are broadly representative starting rates, NOT any specific
    # city's official tariff -- override with the real RTO/state-published
    # tariff for your deployment's city so estimates are locally accurate.
    FARE_AUTO_BASE: float = 30.0
    FARE_AUTO_PER_KM: float = 15.0
    FARE_AUTO_PER_MIN: float = 1.0
    FARE_TAXI_BASE: float = 50.0
    FARE_TAXI_PER_KM: float = 20.0
    FARE_TAXI_PER_MIN: float = 1.5
    FARE_CAB_BASE: float = 60.0
    FARE_CAB_PER_KM: float = 18.0
    FARE_CAB_PER_MIN: float = 1.5
    FARE_BIKE_BASE: float = 20.0
    FARE_BIKE_PER_KM: float = 8.0
    FARE_BIKE_PER_MIN: float = 0.5

    # ---- public holidays (Calendarific -- free tier, key required) ----
    # Used by Crowd & Queue Forecast and Festival & Local Event Intelligence
    # for a real holiday-travel-window signal instead of a hand-maintained
    # date table. Empty = that signal is simply absent (never a fabricated
    # holiday) -- get a free key at https://calendarific.com/signup.
    # ISO 3166-1 alpha-2; India by default.
    # Simulation Core (services/simulation.py): admin-only virtual tourists +
    # scripted events for demos. Single-process (in-memory clock) -- run with
    # WEB_CONCURRENCY=1. Set false to disable the endpoints entirely.
    SIMULATION_ENABLED: bool = True
    # Where AI/ML Evaluation Dashboard runs are stored (services/evaluation.py).
    EVALUATION_DIR: str = "evaluation_reports"
    CALENDARIFIC_API_KEY: str = ""
    HOLIDAYS_COUNTRY_CODE: str = "IN"
    HOLIDAYS_API_TIMEOUT_SECONDS: float = 4.0
    HOLIDAYS_CACHE_TTL_SECONDS: int = 86_400  # a year's holiday list is static; refresh daily

    # ---- Discovery: live OpenStreetMap Overpass lookups ----
    # Real hidden-gem/regional-food/homestay places near the tourist, fetched
    # live from the free public Overpass API (no key). Off by default at
    # request time is NOT the default here -- see services/discovery.py's
    # module docstring for why a short timeout + cache keep this safe to call
    # synchronously (falls back to the seeded/cached DB rows on any failure).
    DISCOVERY_LIVE_ENABLED: bool = True
    OVERPASS_API_URL: str = "https://overpass-api.de/api/interpreter"
    # Comma-separated fallbacks tried in order after OVERPASS_API_URL. The public
    # instances are fair-use and are often at capacity (HTTP 502/timeouts); an
    # endpoint that fails is skipped for a couple of minutes. For real traffic run
    # your own Overpass and put it first.
    OVERPASS_API_URLS: str = "https://overpass.private.coffee/api/interpreter"
    # Nearby (hospital / pharmacy / police / transport) lookups, same free OSM services.
    NEARBY_LIVE_ENABLED: bool = True
    NEARBY_CACHE_TTL_SECONDS: int = 1800
    # The public instance is shared/rate-limited and can be slow; a request
    # here blocks the Discovery API call, so this is a real timeout, not
    # a knob to raise indefinitely -- a slow response falls back to the
    # database rather than making the tourist wait.
    OVERPASS_TIMEOUT_SECONDS: float = 8.0
    DISCOVERY_CACHE_TTL_SECONDS: int = 3600

    # ---- weather (safety-score input) ----
    # Empty = use the deterministic mock (no network needed, works offline).
    # Set to a real OpenWeatherMap API key (free tier) to use live conditions.
    OPENWEATHER_API_KEY: str = ""
    OPENWEATHER_TIMEOUT_SECONDS: float = 3.0
    WEATHER_CACHE_TTL_SECONDS: int = 600
    # Open-Meteo (https://open-meteo.com): free, no API key, CC-BY 4.0. Real
    # current weather for the safety score when no OpenWeatherMap key is set
    # (or it fails). Only when this is also unavailable does the score fall
    # back to the deterministic mock.
    OPEN_METEO_ENABLED: bool = True
    OPEN_METEO_URL: str = "https://api.open-meteo.com/v1/forecast"
    OPEN_METEO_TIMEOUT_SECONDS: float = 3.0
    # How the 0-100 safety score is computed:
    #   "formula" -- the published weighted formula (services/ml_service.py),
    #                with exact per-factor contributions. Default: the model was
    #                only ever trained to reproduce this formula, so the formula
    #                itself is the honest, explainable version.
    #   "model"   -- the RandomForest in ml_models/safety_rf.joblib (if present).
    SAFETY_SCORE_MODE: str = "formula"

    # ---- dispatch / escalation ----
    # Assumed average travel speed for a responding unit, used only to turn a
    # distance into a rough ETA estimate for the dispatch-ranking UI -- not a
    # claim about real traffic conditions.
    DISPATCH_ASSUMED_SPEED_KMH: float = 30.0
    # How often the background escalation job re-checks open incidents.
    ESCALATION_TICK_SECONDS: int = 30
    # How long an incident sits at each escalation stage before auto-advancing
    # to the next one if nobody has acknowledged it.
    ESCALATION_STAGE_TIMEOUT_SECONDS: int = 120

    # ---- intelligent response management ----
    # severity_classifier.py -> response_policy.py -> escalation.py ->
    # response_metrics.py. Everything below is a DEMONSTRATION default, not an
    # emergency-service standard. Response targets, stage timing and the global
    # timeout can also be changed at runtime by an admin (PUT /response/config);
    # a stored override wins over these values.
    #
    # Master switch: when false no incident is classified, no response target
    # is set and nothing escalates by this engine (incidents behave as before).
    RESPONSE_MANAGEMENT_ENABLED: bool = True
    # Seconds a responder has to ACKNOWLEDGE, by severity (illustrative).
    RESPONSE_TARGET_CRITICAL_SECONDS: int = 60
    RESPONSE_TARGET_HIGH_SECONDS: int = 180
    RESPONSE_TARGET_MEDIUM_SECONDS: int = 300
    RESPONSE_TARGET_LOW_SECONDS: int = 600
    # Stage 1 (primary responder) lasts until the response deadline above.
    # Stage 2 (supervisor) then lasts this long before stage 3 (higher authority).
    RESPONSE_STAGE2_SECONDS: int = 120
    # One fixed upper bound for the whole escalation lifecycle, measured from
    # when the workflow started. Never reset when a stage advances.
    RESPONSE_GLOBAL_TIMEOUT_SECONDS: int = 1800
    # Who is notified at each stage / at global timeout. Tokens: assigned_unit
    # (responders of the incident's unit; all responders when none is assigned),
    # responders (every responder account), admins. MUSAFIR has no separate
    # supervisor role, so stage 2 reaches the responder pool and stage 3 the
    # admin control room.
    RESPONSE_STAGE1_AUDIENCE: str = "assigned_unit"
    RESPONSE_STAGE2_AUDIENCE: str = "responders"
    RESPONSE_STAGE3_AUDIENCE: str = "admins"
    RESPONSE_GLOBAL_TIMEOUT_AUDIENCE: str = "admins"
    RESPONSE_STAGE_LABELS: str = "Primary responder,Supervisor,Higher authority"
    # Incident types whose own notification fan-out already reaches the
    # stage-1 audience (SOS pushes the dispatched unit): skip the extra
    # "assigned" push for them so responders are not notified twice.
    RESPONSE_SKIP_ASSIGNED_NOTIFY_TYPES: str = "sos"
    # Push a "deadline approaching" notice this many seconds before the
    # response deadline. 0 disables it.
    RESPONSE_WARNING_SECONDS: int = 0
    # A tightened deadline after an UPGRADE is min(old deadline, change time +
    # new target); it can never give a responder more time. A downgrade (admin
    # only) recomputes from detection time.
    # Re-evaluation: look at the tourist's other incidents this far back.
    SEVERITY_PRIOR_INCIDENT_WINDOW_MINUTES: int = 60
    # How recent a safe check-in must be to count as conflicting evidence.
    SEVERITY_SAFE_SIGNAL_WINDOW_SECONDS: int = 120
    # tourist.safety_score (0-100, higher = safer; bands in services/safety.py:
    # 75 safe, 50 caution, 25 risky) at or below this adds one risk indicator.
    SEVERITY_LOW_SAFETY_SCORE: float = 25.0
    # Each full group of this many corroborating indicators raises the
    # baseline by one level (max +2).
    SEVERITY_POINTS_PER_LEVEL: int = 2
    SEVERITY_MAX_LEVEL_BUMP: int = 2
    # When true a producer's declared severity is a FLOOR: the classifier may
    # raise it but never silently lowers it (only an admin override can).
    SEVERITY_SOURCE_FLOOR_ENABLED: bool = True
    # Baseline severity by incident type (JSON). Unlisted types use the default.
    SEVERITY_BASELINE_BY_TYPE: str = (
        '{"sos":"critical","missing_person":"critical","fall_detected":"high",'
        '"crash_detected":"high","anomaly":"high","missed_checkin":"medium",'
        '"geofence":"medium","safety_report":"medium","field_report":"medium",'
        '"route_deviation":"low","gps_signal_lost":"low"}'
    )
    SEVERITY_DEFAULT_BASELINE: str = "medium"
    # Tourist-reported urgency -> severity (JSON); the higher of this and the
    # baseline is used.
    SEVERITY_URGENCY_MAP: str = '{"critical":"critical","high":"high","medium":"medium","low":"low"}'
    # Incident types detected by a motion sensor: they can never reach
    # CRITICAL on sensor evidence alone (see severity_classifier.py).
    SEVERITY_SENSOR_TYPES: str = "fall_detected,crash_detected"
    # Types whose escalation reflects an unanswered safety check (no_response).
    SEVERITY_UNRESPONSIVE_TYPES: str = "fall_detected,crash_detected,missed_checkin"

    # ---- SLO reporting (services/response_metrics.py) ----
    SLO_DEFAULT_RANGE_DAYS: int = 30
    SLO_MAX_RANGE_DAYS: int = 366
    # Upper edges (seconds) of the acknowledgment-time histogram buckets.
    SLO_DISTRIBUTION_BUCKETS_SECONDS: str = "30,60,120,300,600,1800"

    # ---- SOS live location sharing (see services/emergency_location.py) ----
    # A ping older than this is still "recent enough" to call LIVE; older
    # than OFFLINE and the police dashboard must not claim it's live at all.
    EMERGENCY_LOCATION_LIVE_SECONDS: int = 15
    EMERGENCY_LOCATION_STALE_SECONDS: int = 45
    # Bounded movement trail per incident -- "recent path", not a permanent
    # location history. Enforced by trimming older pings on every insert.
    EMERGENCY_LOCATION_TRAIL_MAX_POINTS: int = 60
    # A implied speed above this between two consecutive pings is physically
    # implausible (teleportation/spoofing) -- flagged, never auto-escalated.
    EMERGENCY_LOCATION_MAX_PLAUSIBLE_KMH: float = 250.0

    # ---- check-in / check-out ----
    # How often the background job re-checks planned check-ins.
    CHECKIN_TICK_SECONDS: int = 30
    # Grace period after a missed check-in deadline before it escalates from
    # an alert to an incident -- "well before an SOS is ever pressed."
    CHECKIN_GRACE_MINUTES: int = 30

    # ---- Walk-Me-Home (services/walk_home.py) ----
    # A monitored journey reuses the check-in engine above; these only bound
    # what a tourist may ask for and are served to the app (GET /walk-home/config),
    # so the browser carries no hardcoded values.
    WALKHOME_ENABLED: bool = True
    WALKHOME_MIN_DURATION_MINUTES: int = 2
    WALKHOME_MAX_DURATION_MINUTES: int = 720
    WALKHOME_DEFAULT_DURATION_MINUTES: int = 30
    WALKHOME_MIN_INTERVAL_MINUTES: int = 1
    WALKHOME_MAX_INTERVAL_MINUTES: int = 120
    WALKHOME_DEFAULT_INTERVAL_MINUTES: int = 10
    # Grace after a missed check-in before an incident is opened. Shorter than
    # the 30 min an outing gets: someone walking home alone should not wait that long.
    WALKHOME_DEFAULT_GRACE_MINUTES: int = 5
    WALKHOME_MIN_GRACE_MINUTES: int = 1
    WALKHOME_MAX_GRACE_MINUTES: int = 30
    WALKHOME_MAX_EXTENSIONS: int = 3
    WALKHOME_MAX_EXTENSION_MINUTES: int = 60
    # Quick-pick buttons in the setup form (comma-separated minutes).
    WALKHOME_DURATION_PRESETS: str = "15,30,45,60,120"
    WALKHOME_INTERVAL_PRESETS: str = "5,10,15,30"
    # How often the open app sends its position while a journey is active, and
    # how old a fix may get before guardians are told it is stale, not live.
    WALKHOME_LOCATION_INTERVAL_SECONDS: int = 15
    WALKHOME_LOCATION_STALE_SECONDS: int = 120
    WALKHOME_MAX_FIX_AGE_SECONDS: int = 300
    WALKHOME_MAX_GUARDIANS: int = 10
    # critical so the control room is pushed (notifications.notify_incident_opened).
    WALKHOME_ESCALATION_SEVERITY: str = "critical"

    # ---- fall & crash detection (services/fall_detection.py) ----
    # Master switch. When false every fall-detection endpoint answers 404 and
    # the tourist app hides the feature (the client reads this from
    # GET /fall-detection/config).
    FALL_DETECTION_ENABLED: bool = True
    # The "Are you OK?" window. The SERVER owns the clock: the deadline is
    # detected_at + this, so a closed/crashed/offline app still escalates.
    FALL_COUNTDOWN_SECONDS: int = 30
    # Extra time the background sweep waits past the deadline before it raises
    # the incident itself, so a "I AM SAFE" tap that is still in flight when
    # the countdown ends is not beaten by the sweep. (A client confirm at the
    # deadline does not wait for this.)
    FALL_CONFIRM_GRACE_SECONDS: int = 5
    FALL_SWEEP_TICK_SECONDS: int = 5
    # While a detection is pending, or after one raised an incident inside
    # this window, a further detection for the same tourist is folded into it
    # instead of creating a second incident.
    FALL_DEDUPE_WINDOW_SECONDS: int = 120
    # A detection timestamped further back than this is not trusted as the
    # start of a countdown (clock skew / replayed request): it is clamped.
    FALL_MAX_EVENT_AGE_SECONDS: int = 120
    # Demo mode. Simulated detections are accepted, labelled "[SIMULATED]" on
    # the incident/alert, and never presented as sensor-based. Turn off in a
    # production deployment.
    FALL_SIMULATION_ENABLED: bool = True
    FALL_HISTORY_LIMIT: int = 20
    # ---- on-device detector thresholds (served to the client, see
    # GET /fall-detection/config; the browser has no hardcoded values) ----
    # Accelerometer magnitude in g (1 g = resting). Free-fall: magnitude stays
    # below FREEFALL_G for at least FREEFALL_MIN_MS.
    # 0.7 g / 40 ms (was 0.4 g / 80 ms), chosen on REAL falls: a phone in a
    # trouser pocket rarely reaches true weightlessness while its owner falls
    # (median low point before impact 0.30 g, often only briefly). On UMAFall
    # (746 recordings, 19 people) falls caught rose from 49% to 85% with false
    # alarms on everyday activities 2.6% -> 4.8%; held-out-people checks agree.
    # See docs/FALL_DETECTION_EVALUATION.md (frontend/scripts/evaluate-fall-detector.mjs).
    FALL_FREEFALL_G: float = 0.7
    FALL_FREEFALL_MIN_MS: int = 40
    # A fall = free-fall followed, within FREEFALL_IMPACT_WINDOW_MS, by an
    # impact of at least IMPACT_G.
    FALL_IMPACT_G: float = 2.5
    FALL_FREEFALL_IMPACT_WINDOW_MS: int = 600
    # A crash = a hard impact of at least CRASH_IMPACT_G while the GPS speed
    # was at least CRASH_MIN_SPEED_KMH (needs a real speed reading).
    FALL_CRASH_IMPACT_G: float = 4.0
    FALL_CRASH_MIN_SPEED_KMH: float = 20.0
    # After an impact the phone must then be (nearly) motionless: magnitude
    # within STILLNESS_DEV_G of 1 g and rotation below STILLNESS_GYRO_DPS,
    # continuously for STILLNESS_MS, observed for at most OBSERVE_MS. A phone
    # that keeps moving (walking, picked up again) is not an emergency.
    FALL_STILLNESS_DEV_G: float = 0.15
    FALL_STILLNESS_GYRO_DPS: float = 40.0
    FALL_STILLNESS_MS: int = 2000
    FALL_POST_IMPACT_OBSERVE_MS: int = 8000
    # Minimum gap between two on-device detections.
    FALL_DETECTOR_COOLDOWN_SECONDS: int = 60

    # ---- privacy & consent ----
    # How often the background job purges location history past its
    # retention window (see services/privacy.py).
    RETENTION_PURGE_TICK_SECONDS: int = 3600
    # How often expired revoked-token rows are purged (see api/auth.py).
    TOKEN_PURGE_TICK_SECONDS: int = 3600

    # ---- scheduler / cross-worker job locking ----
    # False disables the scheduler entirely (e.g. a worker that should never
    # run background ticks). True is the default for a single-worker deploy;
    # with WEB_CONCURRENCY > 1 the job lock (app/core/joblock.py) still
    # prevents duplicate execution even though every worker has this True.
    SCHEDULER_ENABLED: bool = True
    JOB_LOCK_TTL_SECONDS: int = 120

    # ---- disaster & weather alert feeds ----
    # How often the background job refreshes area-level hazard advisories.
    DISASTER_TICK_SECONDS: int = 120
    # Name of a real disaster-advisory provider, if one is ever wired up.
    # "" (default): no real feed; the deterministic simulator runs in
    # development/test only -- never in production. "simulator" turns it on
    # explicitly (demos). "cap": a real CAP feed. See services/disaster.py.
    DISASTER_FEED_PROVIDER: str = ""
    # A CAP 1.2 feed URL to poll when DISASTER_FEED_PROVIDER is set. No
    # default is assumed live/stable -- NDMA SACHET's public feed is served
    # by a JS SPA with no documented XML/JSON endpoint discoverable without
    # provider cooperation (see services/cap.py's module docstring). Point
    # this at whatever CAP 1.2 source is actually available to you.
    DISASTER_FEED_URL: str = ""

    # ---- CCTV network (services/cctv.py) ----
    # Where camera records come from. "" (default) means the database is the
    # only source: an operator registers each camera and its real stream URL
    # through POST /police-network/cameras, exactly as a real police network
    # would. Setting a provider lets POST /cctv/refresh additionally import
    # camera metadata from an external open-data/public-camera API.
    #
    # Nothing here is populated with invented cameras or invented stream
    # URLs: with no provider configured and nothing registered, /cctv simply
    # returns an empty list and the dashboard says so.
    CCTV_PROVIDER: str = ""            # "" (db only) | "json"
    CCTV_PROVIDER_URL: str = ""        # feed URL when CCTV_PROVIDER is set
    CCTV_API_KEY: str = ""             # sent as the provider's API key when required
    # Upper bound on one import, so pointing at a national feed of tens of
    # thousands of cameras can't flood the directory (or the probe budget).
    CCTV_PROVIDER_MAX_CAMERAS: int = 40
    # Only import cameras inside this area of responsibility, as
    # "minLat,minLng,maxLat,maxLng". Empty = import from anywhere in the
    # feed. A national feed covers far more ground than one force polices.
    CCTV_PROVIDER_BBOX: str = ""
    # Probe each candidate during import and store only the ones that
    # actually answer, so the directory doesn't fill with dead URLs the feed
    # still lists. Bounded by CCTV_PROVIDER_PROBE_BUDGET.
    CCTV_PROVIDER_VALIDATE: bool = True
    CCTV_PROVIDER_PROBE_BUDGET: int = 120
    # A camera's LIVE/OFFLINE state is probed against its real stream, never
    # read off the stored row. These bound that probe and how long its result
    # is reused before re-probing.
    CCTV_PROBE_TIMEOUT_SECONDS: float = 4.0
    CCTV_STATUS_CACHE_SECONDS: int = 30
    # Hard ceiling on how long the camera listing will wait for stale
    # statuses to re-probe. Anything slower keeps its previous state and is
    # picked up by the next poll -- the dashboard must never hang on the
    # slowest camera in the network.
    CCTV_LIST_PROBE_DEADLINE_SECONDS: float = 6.0
    # A still-image camera whose frame is this dominated by a single shade is
    # an operator "camera unavailable" notice card, not a view. Measured on
    # real feeds: notice cards ~0.79, actual camera frames 0.02-0.05.
    CCTV_IMAGE_FLAT_RATIO: float = 0.5
    # How often the dashboard re-polls camera status (seconds), served to the
    # frontend so the interval is configured in one place.
    CCTV_REFRESH_INTERVAL_SECONDS: int = 30

    # ---- external services: maps / translation / speech ----
    # All blank by default -- every feature that would use these has a
    # deterministic demo/mock fallback (see services/maps.py,
    # services/translation.py) so the app runs fully offline with no keys.
    # A response built from the fallback is always marked demo/mock in its
    # payload so the frontend can say so, not silently pass mock data off
    # as live.
    GOOGLE_MAPS_API_KEY: str = ""
    # Place search (services/place_search.py, GET /maps/search): Google Places
    # (New) Text Search when GOOGLE_MAPS_API_KEY is set; otherwise free OSM
    # search (Photon + Nominatim), ranked near the tourist.
    PLACE_SEARCH_LIVE_ENABLED: bool = True
    PHOTON_URL: str = "https://photon.komoot.io/api/"
    PLACE_SEARCH_TIMEOUT_SECONDS: float = 5.0
    PLACE_SEARCH_BIAS_RADIUS_M: int = 50_000
    # Nominatim (explicit place search / reverse geocoding), used with the Photon type-ahead above.
    # The public instances are fair-use only (Nominatim forbids type-ahead; Photon asks for modest
    # traffic), so a production deployment with many users should point these at its own instances.
    # Leave PHOTON_URL empty to disable Photon.
    NOMINATIM_URL: str = "https://nominatim.openstreetmap.org/search"
    # Walk-Me-Home deadline = routed travel time + this buffer (the larger of the
    # percentage and the minimum), so small delays do not trigger an alert.
    WALKHOME_ROUTE_BUFFER_PERCENT: int = 25
    WALKHOME_ROUTE_BUFFER_MIN_MINUTES: int = 5
    GOOGLE_TRANSLATE_API_KEY: str = ""
    # Optional self-hosted LibreTranslate (https://libretranslate.com, open source): its URL
    # (e.g. http://localhost:5000) and, if you set one, its API key. Tried before the free fallbacks.
    LIBRETRANSLATE_URL: str = ""
    LIBRETRANSLATE_API_KEY: str = ""
    # Last-resort free fallback: Google Translate's public web endpoint (no key). It is NOT an
    # official API -- no guarantee, may be rate-limited or blocked, and its terms are for Google's
    # own apps -- so it is a stop-gap for development/demos. For real use set GOOGLE_TRANSLATE_API_KEY
    # (official) or LIBRETRANSLATE_URL (your own server) and switch this off.
    TRANSLATE_ALLOW_UNOFFICIAL_GOOGLE: bool = True
    SPEECH_TO_TEXT_API_KEY: str = ""
    TEXT_TO_SPEECH_API_KEY: str = ""
    # Itinerary document upload: max file size accepted for extraction.
    ITINERARY_DOCUMENT_MAX_BYTES: int = 5_000_000  # 5 MB

    # ---- open-ended assistant (see services/llm.py) ----
    # The intent router answers safety-critical questions from real DB rows;
    # this backs everything else, so the assistant isn't limited to a fixed
    # command list. Default "auto": a cloud key if one is configured, else a
    # local Ollama model (free, no key, nothing leaves the machine), else
    # nothing -- in which case the assistant simply says what it can do.
    LLM_ENABLED: bool = True
    LLM_PROVIDER: str = "auto"  # auto | ollama | openai | anthropic | none
    LLM_TIMEOUT_SECONDS: float = 30.0
    LLM_MAX_TOKENS: int = 250  # answers are read aloud -- keep them short
    LLM_TEMPERATURE: float = 0.3
    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    # Lets an OpenAI-compatible provider (Groq, OpenRouter, Together, etc.)
    # stand in for OpenAI itself -- they all speak the same
    # /chat/completions shape, just at a different host and with a free key.
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-5"

    # ---- live-feed fallback ladder (see services/feeds.py) ----
    # False = snapshot-only, no network calls at all -- the demo-venue kill
    # switch (e.g. a hackathon hall with no reliable internet).
    FEEDS_ENABLED: bool = True
    FEED_CACHE_DIR: str = "feed_cache"
    FEED_TIMEOUT_SECONDS: int = 600

    # ---- external hash-chain anchoring ----
    # How often the background job anchors the chain's current root hash.
    ANCHOR_TICK_SECONDS: int = 1800
    # Where anchors are published. "local" (the default) appends to a local,
    # append-only JSON ledger file standing in for an external timestamping
    # service -- see services/anchoring.py for why, and how to point this at
    # a real one.
    ANCHOR_TARGET: str = "local"
    # Where the "external" ledger file lives -- stands in for a real public
    # timestamping service. A real deployment would point this integration
    # at an actual external store instead; see services/anchoring.py.
    ANCHOR_LEDGER_PATH: str = "anchor_ledger.jsonl"

    # ---- domain thresholds ----
    ROUTE_DEVIATION_THRESHOLD_M: float = 2000.0
    ANOMALY_INCIDENT_DEDUPE_MINUTES: int = 5
    # Digital Tourist Safety ID: a trip within this many hours of its end
    # shows as "expiring_soon" rather than "active" -- see services/tourist_id.py.
    ID_EXPIRING_SOON_HOURS: float = 24.0
    # ---- Emergency Medical Profile (services/medical_profile.py) ----
    # Optional lifetime (days) of a newly issued QR token. 0 = no token-level
    # expiry (the QR still stops working when the trip ends, see id_status).
    MEDICAL_QR_TOKEN_TTL_DAYS: int = 0
    # Per responder: medical lookups allowed per window, and -- separately --
    # how many *failed* lookups (unknown/revoked/expired token) before that
    # responder is locked out for the window (token-guessing protection).
    MEDICAL_ACCESS_RATE_LIMIT: int = 30
    MEDICAL_ACCESS_RATE_WINDOW_SECONDS: int = 60
    MEDICAL_FAILED_LOOKUP_LIMIT: int = 6
    MEDICAL_FAILED_LOOKUP_WINDOW_SECONDS: int = 300
    # Access purposes a responder may declare (JSON list). Recorded in the
    # audit trail; MEDICAL_ACCESS_PURPOSE_REQUIRED makes declaring one mandatory.
    MEDICAL_ACCESS_PURPOSES: str = (
        '["medical_emergency","accident","unresponsive_person","routine_verification","other"]'
    )
    MEDICAL_ACCESS_PURPOSE_REQUIRED: bool = False
    # Input bounds for the tourist-entered profile.
    MEDICAL_MAX_ALLERGIES: int = 30
    MEDICAL_MAX_CONDITIONS: int = 30
    MEDICAL_MAX_MEDICATIONS: int = 30
    MEDICAL_NOTES_MAX_CHARS: int = 1000
    # Suggestions offered in the form (JSON list) -- not a restriction.
    MEDICAL_SUGGESTED_CONDITIONS: str = '["Asthma","Diabetes","Heart condition","Epilepsy"]'
    # ---- DPDP-oriented privacy management (services/privacy_*.py) ----
    # Everything legal or operational below is CONFIGURATION, not a statement of
    # law: durations, wording and purposes must be confirmed by legal review
    # before go-live. Nothing here is a compliance claim.
    PRIVACY_NOTICE_VERSION: str = "1.0"
    PRIVACY_NOTICE_UPDATED: str = "2026-11-15"
    # Shown on the dashboard so users/operators are not told every provision is
    # already in force. Verify commencement notifications before relying on it.
    PRIVACY_LEGAL_NOTE: str = (
        "This system is built to support the Digital Personal Data Protection Act, 2023 "
        "and the DPDP Rules, 2025. Provisions commence in phases; confirm the current "
        "commencement dates, amendments and notifications with legal counsel. "
        "This page is not a certificate of legal compliance."
    )
    PRIVACY_CONTACT_EMAIL: str = ""          # grievance / privacy contact shown to users
    PRIVACY_GRIEVANCE_RESPONSE_DAYS: int = 30  # internal target, NOT a statutory figure -- verify
    # JSON list of processing purposes. kind: "consent" = user can grant/withdraw;
    # "notice" = informational (required for the service, or a non-consent basis
    # to be confirmed by legal review). withdraw_effect is shown before withdrawing.
    PRIVACY_PURPOSES: str = (
        '[{"key":"basic_profile","title":"Basic profile & Digital ID","kind":"notice",'
        '"description":"Your name, ID document, phone, trip dates and photo are used to issue and verify your Digital Tourist Safety ID.",'
        '"data_categories":["identity","contact","identity_document","photo","trip"],'
        '"shared_with":"Authorised police/responder accounts when your QR is scanned (role-limited view).",'
        '"retention_category":"tourist_profile","basis_note":"Needed to provide the service you registered for. Legal basis to be confirmed by legal review.",'
        '"withdraw_effect":"Cannot be withdrawn without deleting your account."},'
        '{"key":"location_tracking","title":"Location tracking","kind":"consent",'
        '"description":"Your phone location is sent while the app is open to compute your safety score, detect unusual movement and warn you about risky zones.",'
        '"data_categories":["location_history","live_location"],'
        '"shared_with":"Not shared outside MUSAFIR except during an emergency (see SOS).",'
        '"retention_category":"location_history","basis_note":"Optional; based on your consent.",'
        '"withdraw_effect":"Background location updates stop. Safety scoring, zone warnings and anomaly detection will not work. SOS still works and sends your location only when you raise it."},'
        '{"key":"emergency_sos","title":"Emergency & SOS","kind":"notice",'
        '"description":"When you or a safety rule raises an SOS, your identity, last location and emergency contacts are used to dispatch help.",'
        '"data_categories":["sos_incident","live_location","emergency_contacts","medical_profile"],'
        '"shared_with":"Police stations and responders handling the incident; your emergency contacts.",'
        '"retention_category":"sos_incident_records","basis_note":"Emergency response; legal basis to be confirmed by legal review. Not switched off by withdrawing other consents.",'
        '"withdraw_effect":"Cannot be withdrawn while an emergency is open."},'
        '{"key":"guardian_sharing","title":"Share live status with a guardian","kind":"consent",'
        '"description":"If you create a Trip Guardian link, that person can see your live location and status.",'
        '"data_categories":["live_location","guardian_contact"],'
        '"shared_with":"Only the people you give a guardian link to.",'
        '"retention_category":"guardian_links","basis_note":"Optional; based on your consent.",'
        '"withdraw_effect":"All guardian links are switched off and cannot see you any more."},'
        '{"key":"notifications","title":"Push notifications","kind":"consent",'
        '"description":"Alerts and SOS confirmations are sent to this phone through your browser push service.",'
        '"data_categories":["device_info","push_subscription"],'
        '"shared_with":"Your browser vendor\'s push service delivers the message.",'
        '"retention_category":"device_info","basis_note":"Optional; based on your consent.",'
        '"withdraw_effect":"Push alerts to this phone stop. In-app alerts keep working."}]'
    )
    # JSON list of retention categories seeded into `retention_policies` on first
    # run. retention_days null + requires_review true = "no period established --
    # an administrator/legal must decide"; the engine will NOT delete those.
    # account_deletion: delete | anonymize | retain (what happens on erasure).
    PRIVACY_RETENTION_DEFAULTS: str = (
        '['
        '{"category":"tourist_profile","title":"Tourist profile","purpose":"Digital ID and trip safety","legal_basis":"Service provision (to confirm)",'
        '"retention_days":null,"start":"trip_end","action":"delete","account_deletion":"delete","requires_review":true,'
        '"note":"No period established. Kept while the account exists; erased on account deletion."},'
        '{"category":"location_history","title":"Location history","purpose":"Safety scoring and anomaly detection","legal_basis":"Consent (location_tracking)",'
        '"retention_days":90,"start":"trip_end","action":"delete","account_deletion":"delete","requires_review":true,'
        '"note":"Default; each tourist can choose 1-365 days after trip end (existing per-tourist setting). Value to be confirmed by legal review.",'
        '"per_tourist":true},'
        '{"category":"sos_incident_records","title":"SOS & incident records","purpose":"Emergency response record","legal_basis":"Emergency response / operational (to confirm)",'
        '"retention_days":null,"start":"incident_resolved","action":"anonymize","account_deletion":"anonymize","requires_review":true,'
        '"note":"No period established. Open incidents are always retained; resolved ones are de-identified on account deletion."},'
        '{"category":"emergency_contacts","title":"Emergency contacts","purpose":"Notify a person you chose","legal_basis":"Service provision",'
        '"retention_days":null,"start":"account","action":"delete","account_deletion":"delete","requires_review":true,'
        '"note":"Stored inside the profile; erased with it."},'
        '{"category":"digital_identity","title":"Digital ID, QR tokens and scan log","purpose":"Verify identity in the field","legal_basis":"Service provision",'
        '"retention_days":null,"start":"trip_end","action":"delete","account_deletion":"delete","requires_review":true,'
        '"note":"QR credentials and the ID hash chain are deleted on erasure."},'
        '{"category":"medical_profile","title":"Emergency medical profile","purpose":"Help responders in a medical emergency","legal_basis":"Your own entry; tourist-reported",'
        '"retention_days":null,"start":"account","action":"delete","account_deletion":"delete","requires_review":true,'
        '"note":"Encrypted at rest; erased on account deletion."},'
        '{"category":"guardian_links","title":"Guardian links","purpose":"Family live-status sharing","legal_basis":"Consent (guardian_sharing)",'
        '"retention_days":null,"start":"trip_end","action":"delete","account_deletion":"delete","requires_review":true,"note":""},'
        '{"category":"device_info","title":"Devices & push subscriptions","purpose":"Wearable and push delivery","legal_basis":"Consent (notifications)",'
        '"retention_days":null,"start":"account","action":"delete","account_deletion":"delete","requires_review":true,"note":""},'
        '{"category":"session_tokens","title":"Sessions & reset tokens","purpose":"Authentication","legal_basis":"Security",'
        '"retention_days":0,"start":"token_expiry","action":"delete","account_deletion":"delete","requires_review":false,'
        '"note":"Technical expiry: rows are removed once the token itself has expired."},'
        '{"category":"notification_logs","title":"Notification delivery log","purpose":"Delivery proof and retries","legal_basis":"Operational (to confirm)",'
        '"retention_days":null,"start":"created","action":"delete","account_deletion":"anonymize","requires_review":true,'
        '"note":"No period established; set one to enable scheduled deletion."},'
        '{"category":"activity_records","title":"Safety reports, fare checks, check-ins, permits, itineraries","purpose":"Trip features you used","legal_basis":"Service provision",'
        '"retention_days":null,"start":"created","action":"delete","account_deletion":"delete","requires_review":true,'
        '"note":"No period established; erased on account deletion."},'
        '{"category":"efir_records","title":"E-FIR drafts","purpose":"Police report generated at your request","legal_basis":"Possible statutory record (to confirm)",'
        '"retention_days":null,"start":"created","action":"retain","account_deletion":"retain","requires_review":true,'
        '"note":"Held as a documented exception until legal review decides; access restricted to admins."},'
        '{"category":"security_audit_logs","title":"Security & audit logs","purpose":"Security and accountability","legal_basis":"Security / legal obligation (to confirm)",'
        '"retention_days":null,"start":"created","action":"delete","account_deletion":"anonymize","requires_review":true,'
        '"note":"No period established; your e-mail/ID are replaced by an opaque reference on erasure."},'
        '{"category":"id_scan_log","title":"Digital ID scan log","purpose":"Accountability for who verified an ID","legal_basis":"Security / accountability (to confirm)",'
        '"retention_days":null,"start":"created","action":"retain","account_deletion":"anonymize","requires_review":true,'
        '"note":"The responder-side record is kept; the link to you and the scan location are removed on account deletion."},'
        '{"category":"consent_records","title":"Consent records","purpose":"Prove what you agreed to and when","legal_basis":"Accountability (to confirm)",'
        '"retention_days":null,"start":"created","action":"retain","account_deletion":"anonymize","requires_review":true,'
        '"note":"Kept as an audit trail with the link to you removed on account deletion. Period to be set by legal review."},'
        '{"category":"grievances","title":"Privacy grievances","purpose":"Handle and evidence your complaints","legal_basis":"Grievance redressal (to confirm)",'
        '"retention_days":null,"start":"created","action":"retain","account_deletion":"anonymize","requires_review":true,'
        '"note":"An open grievance is kept until resolved; after that it is de-identified on account deletion."},'
        '{"category":"data_exports","title":"Prepared data exports","purpose":"Give you a copy of your data","legal_basis":"Your request",'
        '"retention_days":0,"start":"created","action":"delete","account_deletion":"delete","requires_review":false,'
        '"note":"Download links expire after PRIVACY_EXPORT_TTL_HOURS."}'
        ']'
    )
    PRIVACY_EXPORT_DIR: str = "privacy_exports"
    PRIVACY_EXPORT_TTL_HOURS: int = 24
    PRIVACY_EXPORT_MAX_PER_DAY: int = 5
    PRIVACY_LOCATION_PAGE_MAX: int = 500
    # Grace period between confirming an account deletion and processing it
    # (0 = immediately). Sessions are revoked and login blocked either way.
    PRIVACY_DELETION_GRACE_HOURS: int = 0
    PRIVACY_DELETION_MAX_ATTEMPTS: int = 5
    # JSON list of external processors that hold personal data and must be told
    # about an erasure, e.g. [{"name":"SMS gateway","contact":"dpo@vendor"}].
    # Empty = none configured; the deletion record says so instead of implying it.
    PRIVACY_EXTERNAL_PROCESSORS: str = "[]"
    # Free-text lifecycle shown with every deletion status. Be honest.
    PRIVACY_BACKUP_NOTE: str = (
        "Copies held in database backups are not edited. They are overwritten when "
        "the backup rotation expires; they are not restored for normal use."
    )
    PRIVACY_BACKUP_ROTATION_DAYS: int = 0   # 0 = not configured/unknown
    # Registration: refuse sign-up unless the consents marked "required" are sent.
    # Off by default so existing API clients keep working; the web form enforces it.
    PRIVACY_REGISTRATION_CONSENT_REQUIRED: bool = False
    # How far ahead (minutes) each ping's trajectory is projected to check for
    # an imminent high-risk/restricted zone crossing.
    TRAJECTORY_HORIZON_MIN: float = 15.0
    # Lateral offset (metres) applied to a candidate route's midpoint waypoint
    # when perturbing the direct origin->destination line (see routing.py).
    ROUTE_CANDIDATE_OFFSET_M: float = 250.0
    # Distance (metres) between risk-sampling points along a candidate route.
    ROUTE_SAMPLE_INTERVAL_M: float = 100.0
    # Safe-route planning is for local trips; farther destinations are rejected
    # (also bounds the work one request can ask the server to do).
    ROUTE_MAX_DISTANCE_KM: float = 500.0

    # ---- map defaults (surfaced to the frontend via /api/config) ----
    MAP_CENTER_LAT: float = 26.1445
    MAP_CENTER_LNG: float = 91.7362
    MAP_DEFAULT_ZOOM: int = 13

    # ---- CORS / hosts (comma-separated in env) ----
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    ALLOWED_HOSTS: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def is_test(self) -> bool:
        return self.ENVIRONMENT.lower() == "test"

    @field_validator("DATABASE_URL")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Managed hosts (Render, Heroku, Railway) hand out postgres:// or
        # postgresql:// URLs; SQLAlchemy needs the psycopg (v3) driver spelled out.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @field_validator("ENVIRONMENT")
    @classmethod
    def _valid_env(cls, v: str) -> str:
        if v.lower() not in ("development", "production", "test"):
            raise ValueError("ENVIRONMENT must be development, production or test")
        return v.lower()

    @model_validator(mode="after")
    def _finalize_secret(self) -> Settings:
        if not self.SECRET_KEY:
            if self.is_production:
                raise RuntimeError(
                    "SECRET_KEY must be set in production. Generate one with: "
                    "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
                )
            # Dev convenience: generate a key once and persist it to a
            # gitignored file. It must be STABLE across restarts because the
            # digital-ID hash chain is keyed with it (see services/hashchain.py)
            # -- a fresh key each boot would invalidate every existing chain.
            key_file = Path(self.DEV_SECRET_FILE)
            if key_file.exists():
                self.SECRET_KEY = key_file.read_text(encoding="utf-8").strip()
            else:
                self.SECRET_KEY = secrets.token_urlsafe(48)
                try:
                    key_file.write_text(self.SECRET_KEY, encoding="utf-8")
                except OSError:
                    warnings.warn(
                        "Could not persist the dev SECRET_KEY; hash chains and "
                        "tokens will reset on restart.",
                        stacklevel=2,
                    )
        elif len(self.SECRET_KEY) < 32 and self.is_production:
            raise RuntimeError("SECRET_KEY is too short for production (need >= 32 chars).")

        if self.is_production:
            weak = [
                name for name in _SEED_PASSWORD_VARS
                if os.environ.get(name, "").strip().lower() in KNOWN_WEAK_PASSWORDS
            ]
            if weak:
                raise RuntimeError(
                    f"Refusing to start in production with known weak/demo passwords in: "
                    f"{', '.join(weak)}. Unset them (random passwords are generated) or "
                    f"choose strong ones."
                )

        if self.is_production and "*" in self.allowed_hosts_list:
            warnings.warn("ALLOWED_HOSTS='*' in production is insecure — set explicit hosts.",
                          stacklevel=2)
        return self


settings = Settings()
