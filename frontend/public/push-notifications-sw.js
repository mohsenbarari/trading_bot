// Service Worker fragment imported into the generated workbox SW.
// Receives Web Push payloads and routes notification clicks back into the SPA.

;(() => {
  const CANONICAL_NOTIFICATION_ROUTE = '/account/notifications'
  const LOCAL_NOTIFICATION_ICON = '/pwa-192x192.png'
  const WEB_PUSH_SESSION_CLEANUP_MESSAGE = 'web-push:cleanup-session'
  const WEB_PUSH_DELIVERY_GATE_MESSAGE = 'web-push:delivery-gate'
  const WEB_PUSH_SUBSCRIPTION_PATH = '/api/notifications/push/subscription'
  const WEB_PUSH_DELIVERY_GATE_CACHE = 'web-push-delivery-gate-v1'
  const WEB_PUSH_DELIVERY_GATE_KEY = '/__web-push-delivery-gate-v1'
  const FORBIDDEN_METADATA_LABELS = new Set([
    'مسیر',
    'route',
    'api',
    'backend',
    'server',
    'homeserver',
  ])
  let isWebPushDeliveryEnabled = null
  let webPushDeliveryGateWrite = Promise.resolve()

  function parsePushPayload(event) {
    if (!event.data) return {}
    try {
      const payload = event.data.json()
      return payload && typeof payload === 'object' && !Array.isArray(payload) ? payload : {}
    } catch {
      return { body: event.data.text() }
    }
  }

  function isForbiddenMetadataLabel(value) {
    const label = typeof value === 'string' ? value.trim() : ''
    if (!label) return false
    const parts = label.split(/\s+/u)
    const lastToken = parts[parts.length - 1] || label
    const normalize = (candidate) => candidate.toLowerCase().replace(/[\s_.-]/gu, '')
    return (
      FORBIDDEN_METADATA_LABELS.has(normalize(label)) ||
      FORBIDDEN_METADATA_LABELS.has(normalize(lastToken))
    )
  }

  function sanitizeText(value) {
    if (typeof value !== 'string') return ''
    return value
      .split(/[\r\n\u2028\u2029]+/u)
      .filter((line) => {
        const colonIndex = line.search(/[:=：＝﹕]/u)
        return colonIndex === -1 || !isForbiddenMetadataLabel(line.slice(0, colonIndex))
      })
      .join('\n')
      .trim()
  }

  function normalizeRoute(value) {
    if (typeof value !== 'string') return CANONICAL_NOTIFICATION_ROUTE
    const trimmed = value.trim()
    if (
      !trimmed ||
      !trimmed.startsWith('/') ||
      trimmed.startsWith('//') ||
      trimmed.includes('\\')
    ) {
      return CANONICAL_NOTIFICATION_ROUTE
    }
    try {
      const parsed = new URL(trimmed, self.location.origin)
      if (parsed.origin !== self.location.origin) return CANONICAL_NOTIFICATION_ROUTE
      if (
        parsed.pathname === '/notifications' ||
        parsed.pathname === CANONICAL_NOTIFICATION_ROUTE
      ) {
        return CANONICAL_NOTIFICATION_ROUTE
      }
      if (parsed.pathname === '/market') return '/market'
      if (/^\/users\/\d+$/u.test(parsed.pathname)) {
        const accountName = parsed.searchParams.get('account_name')
        const query = new URLSearchParams()
        if (accountName && accountName.length <= 120) query.set('account_name', accountName)
        const serializedQuery = query.toString()
        return `${parsed.pathname}${serializedQuery ? `?${serializedQuery}` : ''}`
      }
      if (parsed.pathname === '/chat') {
        const userId = parsed.searchParams.get('user_id')
        if (!userId || !/^-?\d+$/u.test(userId)) return CANONICAL_NOTIFICATION_ROUTE
        const query = new URLSearchParams({ user_id: userId })
        const userName = parsed.searchParams.get('user_name')
        if (userName && userName.length <= 120) query.set('user_name', userName)
        return `/chat?${query.toString()}`
      }
      return CANONICAL_NOTIFICATION_ROUTE
    } catch {
      return CANONICAL_NOTIFICATION_ROUTE
    }
  }

  function normalizeAuthToken(value) {
    if (typeof value !== 'string' || value.length === 0 || value.length > 8192) return null
    if (value.trim() !== value || /[\r\n]/u.test(value)) return null
    return value
  }

  function persistWebPushDeliveryGate(enabled) {
    isWebPushDeliveryEnabled = enabled
    webPushDeliveryGateWrite = webPushDeliveryGateWrite
      .catch(() => undefined)
      .then(async () => {
        if (typeof caches === 'undefined') return
        try {
          const cache = await caches.open(WEB_PUSH_DELIVERY_GATE_CACHE)
          await cache.put(
            WEB_PUSH_DELIVERY_GATE_KEY,
            new Response(enabled ? 'enabled' : 'disabled', {
              headers: { 'Content-Type': 'text/plain' },
            }),
          )
        } catch {
          // Memory state remains fail-closed for the current worker lifetime.
        }
      })
    return webPushDeliveryGateWrite
  }

  async function readWebPushDeliveryGate() {
    if (typeof isWebPushDeliveryEnabled === 'boolean') return isWebPushDeliveryEnabled
    if (typeof caches === 'undefined') {
      isWebPushDeliveryEnabled = false
      return false
    }
    try {
      const cache = await caches.open(WEB_PUSH_DELIVERY_GATE_CACHE)
      const response = await cache.match(WEB_PUSH_DELIVERY_GATE_KEY)
      isWebPushDeliveryEnabled = Boolean(response) && (await response.text()) === 'enabled'
    } catch {
      isWebPushDeliveryEnabled = false
    }
    return isWebPushDeliveryEnabled
  }

  async function cleanupWebPushSession(authToken) {
    const pushManager = self.registration && self.registration.pushManager
    if (!pushManager || typeof pushManager.getSubscription !== 'function') return

    let subscription = null
    try {
      subscription = await pushManager.getSubscription()
    } catch {
      return
    }
    if (!subscription) return

    const endpoint = typeof subscription.endpoint === 'string' ? subscription.endpoint : ''
    const deleteRequest = endpoint
      ? fetch(WEB_PUSH_SUBSCRIPTION_PATH, {
          method: 'DELETE',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${authToken}`,
          },
          body: JSON.stringify({ endpoint }),
          credentials: 'same-origin',
          redirect: 'error',
          cache: 'no-store',
        }).catch(() => undefined)
      : Promise.resolve()
    const localUnsubscribe = Promise.resolve()
      .then(() => subscription.unsubscribe())
      .catch(() => false)

    await Promise.all([deleteRequest, localUnsubscribe])
  }

  self.addEventListener('message', (event) => {
    const message = event && event.data
    if (!message || typeof message !== 'object' || Array.isArray(message)) return

    if (message.type === WEB_PUSH_DELIVERY_GATE_MESSAGE) {
      if (typeof message.enabled === 'boolean') {
        event.waitUntil(persistWebPushDeliveryGate(message.enabled))
      }
      return
    }
    if (message.type !== WEB_PUSH_SESSION_CLEANUP_MESSAGE) return

    isWebPushDeliveryEnabled = false
    const authToken = normalizeAuthToken(message.authToken)
    const closeGate = persistWebPushDeliveryGate(false)
    if (!authToken) {
      event.waitUntil(closeGate)
      return
    }
    event.waitUntil(Promise.all([closeGate, cleanupWebPushSession(authToken)]))
  })

  self.addEventListener('push', (event) => {
    event.waitUntil(
      (async () => {
        if (!(await readWebPushDeliveryGate())) return

        const payload = parsePushPayload(event)
        const title =
          sanitizeText(payload.title).split(/[\r\n\u2028\u2029]+/u).find(Boolean) ||
          'اعلان جدید'
        const route = normalizeRoute(payload.route || (payload.data && payload.data.route))
        const data = { route }

        await self.registration.showNotification(title, {
          body: sanitizeText(payload.body),
          icon: LOCAL_NOTIFICATION_ICON,
          badge: LOCAL_NOTIFICATION_ICON,
          data,
          dir: 'rtl',
          lang: 'fa-IR',
          vibrate: [200, 100, 200],
        })
      })(),
    )
  })

  self.addEventListener('notificationclick', (event) => {
    event.notification.close()
    const route = normalizeRoute(
      event.notification && event.notification.data && event.notification.data.route,
    )
    const targetUrl = new URL(route, self.location.origin).href

    event.waitUntil(
      (async () => {
        const windowClients = await self.clients.matchAll({
          type: 'window',
          includeUncontrolled: true,
        })
        for (const client of windowClients) {
          try {
            const clientUrl = new URL(client.url)
            if (clientUrl.origin !== self.location.origin) continue
            if ('navigate' in client) {
              await client.navigate(targetUrl)
            }
            return client.focus()
          } catch {
            // Continue to the next client.
          }
        }

        return self.clients.openWindow(targetUrl)
      })(),
    )
  })
})()
