/* eslint-disable */
// Service Worker fragment imported into the generated workbox SW.
// Receives Web Push payloads and routes notification clicks back into the SPA.

(() => {
  function parsePushPayload(event) {
    if (!event.data) return {}
    try {
      return event.data.json()
    } catch (error) {
      return { body: event.data.text() }
    }
  }

  function normalizeRoute(value) {
    if (typeof value !== 'string') return '/notifications'
    const trimmed = value.trim()
    if (!trimmed || !trimmed.startsWith('/')) return '/notifications'
    return trimmed
  }

  self.addEventListener('push', (event) => {
    const payload = parsePushPayload(event)
    const title = typeof payload.title === 'string' && payload.title.trim()
      ? payload.title
      : 'اعلان جدید'
    const route = normalizeRoute(payload.route || (payload.data && payload.data.route))
    const data = Object.assign({}, payload.data || {}, { route })

    event.waitUntil(self.registration.showNotification(title, {
      body: typeof payload.body === 'string' ? payload.body : '',
      icon: payload.icon || '/pwa-192x192.png',
      badge: payload.badge || '/pwa-192x192.png',
      tag: payload.tag || undefined,
      data,
      dir: 'rtl',
      lang: 'fa-IR',
      vibrate: [200, 100, 200],
    }))
  })

  self.addEventListener('notificationclick', (event) => {
    event.notification.close()
    const route = normalizeRoute(event.notification && event.notification.data && event.notification.data.route)
    const targetUrl = new URL(route, self.location.origin).href

    event.waitUntil((async () => {
      const windowClients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const client of windowClients) {
        try {
          const clientUrl = new URL(client.url)
          if (clientUrl.origin !== self.location.origin) continue
          if ('navigate' in client) {
            await client.navigate(targetUrl)
          }
          return client.focus()
        } catch (error) {
          // Continue to the next client.
        }
      }

      return self.clients.openWindow(targetUrl)
    })())
  })
})()
