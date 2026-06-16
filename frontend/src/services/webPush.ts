import { apiFetch } from '../utils/auth'

export type WebPushRuntimeState =
  | 'checking'
  | 'unsupported'
  | 'insecure'
  | 'server-disabled'
  | 'permission-blocked'
  | 'permission-default'
  | 'subscribed'
  | 'unsubscribed'
  | 'error'

export interface WebPushPublicConfig {
  enabled: boolean
  public_key?: string | null
  missing?: string[]
}

export interface WebPushStatus {
  state: WebPushRuntimeState
  config?: WebPushPublicConfig
}

export interface WebPushTestResult {
  total: number
  sent: number
  failed: number
  disabled: number
}

export interface NotificationPreferences {
  market_offer_push_enabled: boolean
}

function hasWindowRuntime(): boolean {
  return typeof window !== 'undefined' && typeof navigator !== 'undefined'
}

export function isWebPushRuntimeSupported(): boolean {
  return Boolean(
    hasWindowRuntime()
    && 'Notification' in window
    && 'serviceWorker' in navigator
    && 'PushManager' in window
  )
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = window.atob(base64)
  const outputArray = new Uint8Array(rawData.length)

  for (let i = 0; i < rawData.length; i += 1) {
    outputArray[i] = rawData.charCodeAt(i)
  }

  return outputArray
}

function resolvePlatform(): string {
  const userAgentData = (navigator as any).userAgentData
  if (userAgentData?.platform) return String(userAgentData.platform).slice(0, 80)
  return navigator.platform || 'web'
}

async function getReadyServiceWorker(): Promise<ServiceWorkerRegistration> {
  if (!('serviceWorker' in navigator)) {
    throw new Error('service_worker_unsupported')
  }
  return navigator.serviceWorker.ready
}

async function postSubscription(subscription: PushSubscription): Promise<void> {
  const json = subscription.toJSON()
  const endpoint = json.endpoint
  const keys = json.keys
  if (!endpoint || !keys?.p256dh || !keys?.auth) {
    throw new Error('invalid_push_subscription')
  }

  const response = await apiFetch('/api/notifications/push/subscription', {
    method: 'POST',
    body: JSON.stringify({
      endpoint,
      keys: {
        p256dh: keys.p256dh,
        auth: keys.auth,
      },
      platform: resolvePlatform(),
    }),
  })
  if (!response.ok) {
    throw new Error('push_subscription_rejected')
  }
}

export async function fetchWebPushPublicConfig(): Promise<WebPushPublicConfig> {
  const response = await apiFetch('/api/notifications/push/public-key')
  if (!response.ok) {
    throw new Error('push_config_unavailable')
  }
  return response.json()
}

export async function getWebPushStatus(): Promise<WebPushStatus> {
  if (!isWebPushRuntimeSupported()) {
    return { state: 'unsupported' }
  }
  if (!window.isSecureContext) {
    return { state: 'insecure' }
  }

  const config = await fetchWebPushPublicConfig()
  if (!config.enabled || !config.public_key) {
    return { state: 'server-disabled', config }
  }
  if (Notification.permission === 'denied') {
    return { state: 'permission-blocked', config }
  }
  if (Notification.permission === 'default') {
    return { state: 'permission-default', config }
  }

  const registration = await getReadyServiceWorker()
  const subscription = await registration.pushManager.getSubscription()
  return { state: subscription ? 'subscribed' : 'unsubscribed', config }
}

async function subscribeWithKey(
  registration: ServiceWorkerRegistration,
  publicKey: string,
): Promise<PushSubscription> {
  return registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(publicKey),
  })
}

async function subscribeAndRegisterWebPush(config: WebPushPublicConfig): Promise<WebPushStatus> {
  if (!config.enabled || !config.public_key) {
    return { state: 'server-disabled', config }
  }

  const registration = await getReadyServiceWorker()
  let subscription = await registration.pushManager.getSubscription()
  if (!subscription) {
    subscription = await subscribeWithKey(registration, config.public_key)
  }

  try {
    await postSubscription(subscription)
  } catch (error) {
    await subscription.unsubscribe().catch(() => false)
    subscription = await subscribeWithKey(registration, config.public_key)
    await postSubscription(subscription)
  }

  return { state: 'subscribed', config }
}

export async function promptAndEnableWebPushNotifications(): Promise<WebPushStatus> {
  if (!isWebPushRuntimeSupported()) {
    return { state: 'unsupported' }
  }
  if (!window.isSecureContext) {
    return { state: 'insecure' }
  }
  if (Notification.permission === 'denied') {
    return { state: 'permission-blocked' }
  }

  const permission = Notification.permission === 'granted'
    ? 'granted'
    : await Notification.requestPermission()
  if (permission === 'denied') {
    return { state: 'permission-blocked' }
  }
  if (permission !== 'granted') {
    return { state: 'permission-default' }
  }

  const config = await fetchWebPushPublicConfig()
  return subscribeAndRegisterWebPush(config)
}

export async function enableWebPushNotifications(): Promise<WebPushStatus> {
  if (!isWebPushRuntimeSupported()) {
    return { state: 'unsupported' }
  }
  if (!window.isSecureContext) {
    return { state: 'insecure' }
  }

  const config = await fetchWebPushPublicConfig()
  if (!config.enabled || !config.public_key) {
    return { state: 'server-disabled', config }
  }
  const permission = Notification.permission === 'granted'
    ? 'granted'
    : await Notification.requestPermission()
  if (permission === 'denied') {
    return { state: 'permission-blocked', config }
  }
  if (permission !== 'granted') {
    return { state: 'permission-default', config }
  }

  return subscribeAndRegisterWebPush(config)
}

export async function disableWebPushNotifications(): Promise<WebPushStatus> {
  if (!isWebPushRuntimeSupported()) {
    return { state: 'unsupported' }
  }

  const registration = await getReadyServiceWorker()
  const subscription = await registration.pushManager.getSubscription()
  if (subscription) {
    const endpoint = subscription.endpoint
    await subscription.unsubscribe().catch(() => false)
    if (endpoint) {
      await apiFetch('/api/notifications/push/subscription', {
        method: 'DELETE',
        body: JSON.stringify({ endpoint }),
      }).catch(() => undefined)
    }
  }

  return getWebPushStatus()
}

export async function sendWebPushTestNotification(): Promise<WebPushTestResult> {
  const response = await apiFetch('/api/notifications/push/test', { method: 'POST' })
  if (!response.ok) {
    throw new Error('push_test_failed')
  }
  return response.json()
}

export async function fetchNotificationPreferences(): Promise<NotificationPreferences> {
  const response = await apiFetch('/api/notifications/preferences')
  if (!response.ok) {
    throw new Error('notification_preferences_unavailable')
  }
  return response.json()
}

export async function updateNotificationPreferences(
  preferences: NotificationPreferences,
): Promise<NotificationPreferences> {
  const response = await apiFetch('/api/notifications/preferences', {
    method: 'PATCH',
    body: JSON.stringify(preferences),
  })
  if (!response.ok) {
    throw new Error('notification_preferences_update_failed')
  }
  return response.json()
}
