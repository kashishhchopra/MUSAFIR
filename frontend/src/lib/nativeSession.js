// The Android app's WebView runs on a different origin than the API, so the
// SameSite=strict refresh-token cookie never reaches it. The app keeps the
// refresh token itself in app-private storage instead.
import { Preferences } from '@capacitor/preferences'

const KEY = 'refresh_token'

export async function loadRefreshToken() {
  const { value } = await Preferences.get({ key: KEY })
  return value
}

export function saveRefreshToken(token) {
  return token ? Preferences.set({ key: KEY, value: token }) : Preferences.remove({ key: KEY })
}

export function clearRefreshToken() {
  return Preferences.remove({ key: KEY })
}
