import i18n from '../i18n'

// English copy for Fall & Crash Detection, registered under `fall_detection.*`.
//
// It lives here rather than in locales/*.json because src/i18n.test.js
// requires every one of the 21 locale files to carry the identical key set.
// Other languages fall back to this English text (fallbackLng: 'en') until
// someone adds reviewed translations -- when they do, move this object into
// each locales/<lang>.json under the same `fall_detection` key and delete this
// file; no component changes are needed. Safety-critical wording is better
// left in English than machine-translated unreviewed.
export const FALL_STRINGS_EN = {
  card_title: 'Fall & crash detection',
  how_it_works:
    'Uses your phone’s motion sensors, on this device only, to notice a possible fall or crash. '
    + 'Raw sensor readings never leave your phone — only a short summary is sent if something is detected.',
  disclaimer: 'This is an aid, not a guarantee: it cannot detect every fall or crash and can sometimes '
    + 'trigger by mistake. Always use the SOS button if you can.',
  enable: 'Turn on motion monitoring',
  disable: 'Turn off',
  status_off: 'Monitoring off',
  status_starting: 'Starting…',
  status_active: 'Monitoring active',
  status_denied: 'Motion permission denied',
  status_unsupported: 'No motion sensor on this device',
  status_unavailable: 'Sensor not sending data',
  pill_active: 'Fall detection on',
  denied_help: 'Allow “Motion & Orientation” access for this site in your browser or system settings, then try again.',
  unsupported_help: 'This browser or device has no accelerometer. You can still try the feature with simulation mode below.',
  unavailable_help: 'The sensor gave no readings (common on laptops, or when a browser blocks motion data on plain HTTP). Open the app over HTTPS on a phone, or use simulation mode.',
  insecure_help: 'Motion sensors need a secure (HTTPS) connection on most phones.',
  config_error: 'Could not load detection settings.',
  disabled_by_server: 'Fall detection is turned off on this server.',

  simulate_title: 'Simulation mode',
  simulate_note: 'For demonstrations. Simulated events are clearly labelled, are NOT sensor detections, and notify only the control room — never your family.',
  simulate_fall: 'Simulate a fall',
  simulate_crash: 'Simulate a crash',

  history_title: 'Recent detections',
  history_empty: 'No detections yet.',
  type_fall: 'Fall',
  type_crash: 'Crash',
  src_phone_sensor: 'Phone sensor',
  src_wearable: 'Wearable',
  src_simulation: 'Simulation',
  st_pending: 'Awaiting response',
  st_cancelled: 'Cancelled — you were safe',
  st_confirmed: 'Incident raised',
  st_sos_triggered: 'SOS sent',

  modal_title_fall: 'Possible fall detected',
  modal_title_crash: 'Possible crash detected',
  modal_question: 'Are you OK?',
  modal_explain: 'If you don’t respond, emergency services will be alerted automatically.',
  seconds_left: 'seconds left',
  cancel_button: 'I AM SAFE – CANCEL ALERT',
  sos_button: 'SEND SOS NOW',
  cancelling: 'Cancelling…',
  sending: 'Alerting responders…',
  from_simulation: 'SIMULATION — not a real sensor detection',
  from_wearable: 'Detected by your wearable device',
  from_phone: 'Detected by your phone’s motion sensors',
  no_location: 'No GPS fix — your last known location will be used if available.',
  network_retrying: 'No connection — retrying. Your countdown keeps running.',
  result_cancelled_title: 'Alert cancelled',
  result_cancelled_body: 'Glad you’re safe. No emergency was raised.',
  result_confirmed_title: 'Emergency raised',
  result_confirmed_body: 'No response was received, so a critical incident was opened and responders and your contacts have been notified.',
  result_confirmed_incident: 'Incident #{{id}}',
  result_sos_title: 'SOS sent',
  result_sos_body: 'Your SOS was sent through the emergency workflow. Stay where you are if it is safe to.',
  result_error_title: 'Something went wrong',
  result_error_body: 'We could not reach the server. If you need help, use the SOS button or call local emergency services.',
  close: 'Close',
}

// Merge (deep) without overwriting anything a locale file may later define.
i18n.addResourceBundle('en', 'translation', { fall_detection: FALL_STRINGS_EN }, true, false)
