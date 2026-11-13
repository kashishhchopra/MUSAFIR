/* MUSAFIR push handler -- loaded into the PWA service worker via workbox
 * `importScripts` (see vite.config.js). Runs even when no MUSAFIR tab is open
 * (subject to browser/OS support): shows the emergency notification and, on
 * click, opens the page that lets the recipient act on it.
 *
 * Payload (JSON, from backend services/notifications.py):
 *   { title, body, url, severity, incident_id, tag, event, ts }
 */
self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch (_e) {
    data = { title: 'MUSAFIR alert', body: event.data ? event.data.text() : '' }
  }
  const critical = data.severity === 'critical'
  const title = data.title || 'MUSAFIR alert'
  const options = {
    body: data.body || '',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    tag: data.tag || (data.incident_id ? `incident-${data.incident_id}` : undefined),
    renotify: Boolean(data.tag),
    // An SOS must not quietly slide away: keep it on screen until acted on.
    requireInteraction: critical,
    vibrate: critical ? [300, 120, 300, 120, 300] : [120],
    timestamp: data.ts || Date.now(),
    data: { url: data.url || '/', incident_id: data.incident_id || null, event: data.event || '' },
    actions: data.url ? [{ action: 'open', title: data.incident_id ? 'View incident' : 'Open' }] : [],
  }
  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  // Same-origin only: a payload can never send the user to another site.
  let target = new URL('/', self.location.origin)
  try {
    const u = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin)
    if (u.origin === self.location.origin) target = u
  } catch (_e) { /* keep the default */ }

  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    for (const w of wins) {
      if (new URL(w.url).origin === self.location.origin && 'focus' in w) {
        // navigate() is refused for a page this worker does not control yet; in that
        // case open the target in a new window rather than silently doing nothing.
        if ('navigate' in w) {
          try { const nav = await w.navigate(target.href); return (nav || w).focus() } catch (_e) { /* fall through */ }
        }
        break
      }
    }
    return self.clients.openWindow(target.href)
  })())
})
