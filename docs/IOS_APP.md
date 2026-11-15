# MUSAFIR on iOS (Capacitor)

The iOS app is the existing React build wrapped by Capacitor (`frontend/ios`).

## Build & run
1. Deploy the backend over HTTPS (Render, `render.yaml`); add `capacitor://localhost` to `CORS_ORIGINS`.
2. Put the real host in `frontend/.env.production`.
3. `cd frontend && npm run ios:sync && npm run ios:open`, pick a device/simulator in Xcode, press Run.

## Native features
- **Background location** (`src/lib/geo.js`): Walk-Me-Home and live tracking keep reporting with the phone locked
  (`UIBackgroundModes: location`; the user must choose "Always" for location).
- **Push (APNs)** (`src/lib/pushService.js`, `backend/app/services/apns.py`): the app registers an APNs token as
  endpoint `apns:<token>` through the normal `/notifications/push/*` endpoints; the existing push pipeline
  (SOS, incident updates, escalation) routes those rows to APNs. Tapping a notification opens its link.

## One-time Apple setup for push (needs the paid Apple Developer Program)
1. Xcode -> App target -> Signing & Capabilities -> **+ Capability -> Push Notifications**
   (and keep **Background Modes**: Location updates, Remote notifications).
2. developer.apple.com -> Keys -> create a key with **APNs**; download the `.p8`.
3. On the backend set `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_BUNDLE_ID`, `APNS_AUTH_KEY` (the .p8 contents, `\n` for
   line breaks) and `APNS_USE_SANDBOX=true` for Xcode builds / `false` for TestFlight and App Store.
