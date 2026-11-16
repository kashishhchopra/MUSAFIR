# MUSAFIR on iOS (Capacitor)

The iOS app is the existing React build wrapped by Capacitor (`frontend/ios`).

## Build & run
1. Deploy the backend over HTTPS (Render, `render.yaml`); `CORS_ORIGINS` must include `capacitor://localhost`
   (iOS) and `https://localhost` (Android).
2. Put the real host in `frontend/.env.production`.
3. `cd frontend && npm run ios:sync && npm run ios:open`, pick a device/simulator in Xcode, press Run.

## Native features
- **Background location** (`src/lib/geo.js`): Walk-Me-Home and live tracking keep reporting with the phone locked
  (`UIBackgroundModes: location`; the user must choose "Always" for location).
- **Push (APNs)** (`src/lib/pushService.js`, `backend/app/services/apns.py`): the same native push code as the
  Android app; on iOS the plugin returns an APNs device token, which the app registers with
  `POST /notifications/push/subscribe-apns` (stored as `kind="apns"`, next to Android's `kind="fcm"`). The existing
  push pipeline (SOS, incident updates, escalation) sends those rows through APNs. Tapping a notification opens
  its link (`src/components/NativeBridge.jsx`).
- **Login session**: like Android, the app keeps the refresh token in app storage (`src/lib/nativeSession.js`)
  because the WebView origin (`capacitor://localhost`) never receives the SameSite cookie.

Android and iOS share one Capacitor config. Android's app id is `com.musafir.app` (tied to its Firebase
project); the iOS bundle id is `com.kashish.musafir`, because `com.musafir.app` is already registered to
another Apple developer account and Apple bundle ids are unique across all accounts. The Capacitor `appId`
is only used when a platform is first added, so the two can differ.

## One-time Apple setup for push (needs the paid Apple Developer Program)
1. Xcode -> App target -> Signing & Capabilities -> **+ Capability -> Push Notifications**
   (and keep **Background Modes**: Location updates, Remote notifications).
2. developer.apple.com -> Keys -> create a key with **APNs**; download the `.p8`.
3. On the backend set `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_BUNDLE_ID` (`com.kashish.musafir`), `APNS_AUTH_KEY` (the .p8 contents, `\n` for
   line breaks) and `APNS_USE_SANDBOX=true` for Xcode builds / `false` for TestFlight and App Store.
