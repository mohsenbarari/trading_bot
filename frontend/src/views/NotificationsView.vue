<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, BellRing, ChevronRight, RefreshCw } from 'lucide-vue-next'
import {
  AppButton,
  AppEmptyState,
  AppFilterChips,
  AppLoadingState,
  AppPage,
  AppPageHeader,
  AppSectionCard,
  AppStatusBadge,
} from '../components/ui'
import { useNotificationStore } from '../stores/notifications'
import {
  isForbiddenNotificationMetadataLabel,
  notificationMetadataSeparatorIndex,
  type NormalizedAppNotification,
} from '../types/notifications'
import { formatIranTime } from '../utils/iranTime'
import { getNotificationIconComponent } from '../utils/notificationUi'
import {
  enableWebPushNotifications,
  getWebPushStatus,
  type WebPushRuntimeState,
} from '../services/webPush'
import { validateIntendedRoute } from '../utils/authNavigation'
import { assertSuccessfulNavigation } from '../utils/navigationResult'

const router = useRouter()
const notificationStore = useNotificationStore()
const activeCategory = ref<'trade' | 'management'>('trade')
const pushState = ref<WebPushRuntimeState>('checking')
const isPushBusy = ref(false)
const pushActionMessage = ref('')

const tradeNotifications = computed(() => notificationStore.appNotifications.filter((notification) => notification.category === 'trade'))
const managementNotifications = computed(() => notificationStore.appNotifications.filter((notification) => notification.category !== 'trade'))
const activeCategoryNotifications = computed(() => (
  activeCategory.value === 'trade' ? tradeNotifications.value : managementNotifications.value
))
const hasNotifications = computed(() => notificationStore.appNotifications.length > 0)
const categoryOptions = computed(() => [
  {
    key: 'trade' as const,
    label: 'معاملات',
  },
  {
    key: 'management' as const,
    label: 'سایر',
  },
])
const filteredNotifications = computed(() => activeCategoryNotifications.value)
const hasInitialHistoryError = computed(() => (
  notificationStore.historyStatus === 'error' && !hasNotifications.value
))
const hasRetainedHistoryError = computed(() => (
  notificationStore.historyStatus === 'error' && hasNotifications.value
))
const hasConfirmedEmptyHistory = computed(() => (
  notificationStore.hasLoadedHistory
  && notificationStore.historyStatus === 'success'
  && !hasNotifications.value
))
const isHistoryBusy = computed(() => (
  notificationStore.isLoadingHistory || notificationStore.isRefreshingHistory
))
const pushStatusLabel = computed(() => {
  if (pushState.value === 'checking') return 'در حال بررسی'
  if (pushState.value === 'unsupported') return 'پشتیبانی نمی‌شود'
  if (pushState.value === 'insecure') return 'نیازمند HTTPS'
  if (pushState.value === 'server-disabled') return 'غیرفعال در سرور'
  if (pushState.value === 'permission-blocked') return 'مسدود در مرورگر'
  if (pushState.value === 'permission-default') return 'آماده فعال‌سازی'
  if (pushState.value === 'subscribed') return 'فعال'
  if (pushState.value === 'unsubscribed') return 'غیرفعال'
  return 'خطا'
})
const pushStatusTone = computed<'neutral' | 'success' | 'warning' | 'danger'>(() => {
  if (pushState.value === 'subscribed') return 'success'
  if (pushState.value === 'permission-default' || pushState.value === 'unsubscribed') return 'warning'
  if (pushState.value === 'checking') return 'neutral'
  return 'danger'
})
const canEnablePush = computed(() => (
  pushState.value === 'permission-default'
  || pushState.value === 'unsubscribed'
))
const pushStateDescription = computed(() => {
  if (pushState.value === 'checking') return 'وضعیت اعلان در همین مرورگر بررسی می‌شود.'
  if (pushState.value === 'unsupported') return 'این مرورگر یا دستگاه از اعلان مرورگر پشتیبانی نمی‌کند.'
  if (pushState.value === 'insecure') return 'فعال‌سازی اعلان مرورگر فقط در اتصال امن در دسترس است.'
  if (pushState.value === 'server-disabled') return 'اعلان مرورگر در حال حاضر از سمت سرویس فعال نیست.'
  if (pushState.value === 'permission-blocked') return 'اجازه اعلان در تنظیمات همین مرورگر مسدود است.'
  if (pushState.value === 'permission-default') return 'برای دریافت اعلان‌های مهم در همین مرورگر، اجازه نمایش اعلان را فعال کنید.'
  if (pushState.value === 'subscribed') return 'اعلان‌های این مرورگر فعال است.'
  if (pushState.value === 'unsubscribed') return 'این مرورگر اجازه اعلان دارد، اما اشتراک فعالی ندارد.'
  return 'وضعیت اعلان مرورگر مشخص نشد. دوباره بررسی کنید.'
})

const goBack = async () => {
  try {
    assertSuccessfulNavigation(await router.push({ name: 'account' }))
  } catch {
    // The user remains in the fully usable notification center when the
    // transition is cancelled or its lazy chunk cannot load.
  }
}

const formatTime = (ts: unknown) => {
  return formatIranTime(ts)
}

type ParsedNotificationLine = {
  icon: string
  text: string
  label: string
  value: string
  isField: boolean
  isWide: boolean
}

const parseNotificationLine = (rawLine: string): ParsedNotificationLine | null => {
  const trimmed = rawLine.trim()
  if (!trimmed) return null

  // Filter implementation metadata before attempting to interpret a leading
  // token as an icon. Otherwise a line such as `route: /market` would treat
  // `route:` as the icon and expose the raw value as plain text.
  const rawColonIndex = notificationMetadataSeparatorIndex(trimmed)
  if (
    rawColonIndex !== -1 &&
    isForbiddenNotificationMetadataLabel(trimmed.slice(0, rawColonIndex))
  ) {
    return null
  }

  const iconMatch = trimmed.match(
    /^([\p{Extended_Pictographic}\p{Emoji_Presentation}\u200d\ufe0f]+)\s+(.*)$/u,
  )
  const icon = iconMatch?.[1] || ''
  const remainder = (iconMatch?.[2] || trimmed).trim()
  const colonIndex = notificationMetadataSeparatorIndex(remainder)

  if (colonIndex === -1) {
    return {
      icon,
      text: remainder,
      label: '',
      value: '',
      isField: false,
      isWide: true,
    }
  }

  const label = remainder.slice(0, colonIndex).trim()
  const value = remainder.slice(colonIndex + 1).trim()
  if (isForbiddenNotificationMetadataLabel(label)) return null
  const isWide = label === 'زمان معامله' || label === 'مسیر' || label === 'توضیحات'

  return {
    icon,
    text: '',
    label,
    value,
    isField: true,
    isWide,
  }
}

const getNotificationLines = (notification: NormalizedAppNotification): ParsedNotificationLine[] => {
  const body = notification.content || notification.body || ''
  return body
    .split(/[\r\n\u2028\u2029]+/u)
    .map(parseNotificationLine)
    .filter((line): line is ParsedNotificationLine => line !== null)
}

const shouldUseStructuredLines = (notification: NormalizedAppNotification): boolean => {
  const body = notification.content || notification.body || ''
  return /[\r\n\u2028\u2029]/u.test(body) || notification.category === 'trade'
}

async function refreshPushState() {
  pushActionMessage.value = ''
  pushState.value = 'checking'
  try {
    const status = await getWebPushStatus()
    pushState.value = status.state
  } catch {
    pushState.value = 'error'
  }
}

async function enablePush() {
  if (isPushBusy.value) return
  isPushBusy.value = true
  pushActionMessage.value = ''
  try {
    const status = await enableWebPushNotifications()
    pushState.value = status.state
    pushActionMessage.value = status.state === 'subscribed' ? 'فعال شد' : pushStatusLabel.value
  } catch {
    pushState.value = 'error'
    pushActionMessage.value = 'فعال‌سازی ناموفق بود'
  } finally {
    isPushBusy.value = false
  }
}

async function retryHistory() {
  if (isHistoryBusy.value) return
  await notificationStore.openNotificationCenter()
}

function resolveNotificationRoute(notification: NormalizedAppNotification): string | null {
  const routePath = typeof notification.route === 'string' ? notification.route.trim() : ''
  const safePath = validateIntendedRoute({ fullPath: routePath })
  if (!safePath) return null

  try {
    const resolved = router.resolve(safePath)
    if (!resolved.matched.length || resolved.name === 'system-recovery') return null
    return validateIntendedRoute({ fullPath: resolved.fullPath })
  } catch {
    return null
  }
}

function canOpenNotificationRoute(notification: NormalizedAppNotification): boolean {
  return resolveNotificationRoute(notification) !== null
}

const openNotificationRoute = async (notification: NormalizedAppNotification) => {
  const routePath = resolveNotificationRoute(notification)
  if (!routePath) return
  try {
    assertSuccessfulNavigation(await router.push(routePath))
    if (router.currentRoute.value.name === 'system-recovery') {
      assertSuccessfulNavigation(await router.replace({ name: 'account-notifications' }))
    }
  } catch {
    // Keep the notification and its context visible when navigation fails.
  }
}

onMounted(async () => {
  void refreshPushState()
  await notificationStore.openNotificationCenter()
})
</script>

<template>
  <AppPage narrow class="ui-v2-daily-page ui-v2-notifications-page">
    <div class="notifications-view ui-v2-daily-page__content">
      <AppPageHeader eyebrow="حساب" title="اعلان‌ها" description="آخرین اعلان‌های دریافت‌شده در این حساب">
        <template #actions>
          <AppButton
            type="button"
            class="notifications-return"
            variant="ghost"
            size="sm"
            @click="goBack"
          >
            <template #icon>
              <ChevronRight :size="18" />
            </template>
            بازگشت به حساب
          </AppButton>
        </template>
      </AppPageHeader>

      <div class="notifications-topbar" aria-label="دسته‌بندی مرکز اعلان‌ها">
        <AppFilterChips
          v-model="activeCategory"
          class="notification-category-tabs"
          label="دسته‌بندی اعلان‌ها"
          id-prefix="notifications-category"
          focus-selection-on-keyboard
          :options="categoryOptions"
        />
      </div>

      <div class="content">
        <AppSectionCard
          title="اعلان مرورگر"
          :description="pushStateDescription"
          class="push-section ui-v2-browser-push ui-v2-notifications-push"
        >
          <template #actions>
            <AppStatusBadge :tone="pushStatusTone">{{ pushStatusLabel }}</AppStatusBadge>
          </template>

          <div class="push-controls">
            <AppButton
              v-if="canEnablePush"
              class="push-enable-btn"
              :loading="isPushBusy"
              @click="enablePush"
            >
              <template #icon>
                <BellRing :size="16" />
              </template>
              فعال‌سازی اعلان مرورگر
            </AppButton>
            <AppButton
              v-else-if="pushState === 'error'"
              class="push-status-retry"
              variant="secondary"
              :loading="pushState === 'checking'"
              @click="refreshPushState"
            >
              <template #icon>
                <RefreshCw :size="16" />
              </template>
              بررسی دوباره
            </AppButton>
          </div>
          <p class="push-device-scope">این تنظیم فقط برای همین مرورگر و دستگاه است.</p>
          <p v-if="pushActionMessage" class="push-action-message" role="status" aria-live="polite">
            {{ pushActionMessage }}
          </p>
        </AppSectionCard>

        <div
          :id="`notifications-category-${activeCategory}-panel`"
          class="notification-category-panel"
          role="tabpanel"
          :aria-labelledby="`notifications-category-${activeCategory}-tab`"
        >
          <AppLoadingState
            v-if="notificationStore.isLoadingHistory && !hasNotifications"
            class="ds-loading-state"
            label="در حال دریافت اعلان‌ها"
          />

          <AppEmptyState
            v-else-if="hasInitialHistoryError"
            class="notification-history-error"
            title="اعلان‌ها دریافت نشدند"
            message="دریافت اعلان‌ها انجام نشد. دوباره تلاش کنید."
            tone="danger"
            role="alert"
          >
            <template #icon>
              <Bell :size="48" />
            </template>
            <template #actions>
              <AppButton
                class="notification-history-retry"
                size="sm"
                :loading="isHistoryBusy"
                @click="retryHistory"
              >
                <template #icon>
                  <RefreshCw :size="16" />
                </template>
                تلاش دوباره
              </AppButton>
            </template>
          </AppEmptyState>

          <AppEmptyState
            v-else-if="hasConfirmedEmptyHistory"
            title="هیچ اعلانی یافت نشد"
            message="در آخرین اعلان‌های دریافت‌شده موردی برای نمایش وجود ندارد."
            tone="info"
            role="status"
          >
            <template #icon>
              <Bell :size="48" />
            </template>
          </AppEmptyState>

          <section
            v-else-if="hasNotifications"
            class="notifications-section"
          >
            <div
              v-if="notificationStore.isRefreshingHistory"
              class="notification-history-feedback is-refreshing"
              role="status"
              aria-live="polite"
            >
              <span>در حال به‌روزرسانی اعلان‌ها</span>
            </div>

            <div
              v-else-if="hasRetainedHistoryError"
              class="notification-history-feedback is-error"
              role="alert"
            >
              <span>به‌روزرسانی اعلان‌ها انجام نشد؛ موارد قبلی همچنان نمایش داده می‌شوند.</span>
              <AppButton
                class="notification-history-retry"
                variant="ghost"
                size="sm"
                :loading="isHistoryBusy"
                @click="retryHistory"
              >
                <template #icon>
                  <RefreshCw :size="16" />
                </template>
                تلاش دوباره
              </AppButton>
            </div>

            <AppEmptyState
              v-if="filteredNotifications.length === 0"
              class="notification-filter-empty"
              title="اعلانی در این فیلتر وجود ندارد"
              :message="activeCategory === 'trade' ? 'اعلان معاملاتی برای نمایش وجود ندارد.' : 'اعلانی در دسته سایر برای نمایش وجود ندارد.'"
              tone="neutral"
              role="status"
            >
              <template #icon>
                <Bell :size="40" />
              </template>
            </AppEmptyState>

            <div v-else class="notifications-list">
            <component
              v-for="notif in filteredNotifications"
              :key="notif.id"
              :is="canOpenNotificationRoute(notif) ? 'button' : 'article'"
              :type="canOpenNotificationRoute(notif) ? 'button' : undefined"
              class="notif-item ui-v2-notifications-item"
              :class="[
                `type-${notif.level || 'info'}`,
                `category-${notif.category || 'system'}`,
                {
                  'is-unread': !notif.is_read,
                  'ui-v2-notifications-item--unread': !notif.is_read,
                },
              ]"
              :aria-label="canOpenNotificationRoute(notif) ? `باز کردن اعلان ${notif.title || 'اعلان جدید'}` : undefined"
              @click="openNotificationRoute(notif)"
            >
              <div class="notif-main">
                <div class="notif-icon">
                  <component :is="getNotificationIconComponent(notif)" :size="20" />
                  <div v-if="!notif.is_read" class="unread-dot"></div>
                </div>

                <div class="notif-body">
                  <div v-if="notif.category !== 'trade'" class="notif-meta-row">
                    <h3 class="notif-title">{{ notif.title || 'اعلان جدید' }}</h3>
                  </div>

                  <div
                    v-if="shouldUseStructuredLines(notif)"
                    class="notif-lines"
                    :class="{ 'is-trade-lines': notif.category === 'trade' }"
                  >
                    <div
                      v-for="(line, lineIndex) in getNotificationLines(notif)"
                      :key="`${notif.id}-line-${lineIndex}`"
                      class="notif-line ui-v2-notifications-line"
                      :class="[
                        line.isField ? 'notif-line-field ui-v2-notifications-field' : 'notif-line-plain',
                        { 'notif-line-wide': line.isWide },
                      ]"
                    >
                      <span v-if="line.icon" class="notif-line-icon" aria-hidden="true">{{ line.icon }}</span>
                      <template v-if="line.isField">
                        <span class="notif-line-label">{{ line.label }}</span>
                        <span class="notif-line-separator">:</span>
                        <bdi class="notif-line-value">{{ line.value }}</bdi>
                      </template>
                      <bdi v-else class="notif-line-text">{{ line.text }}</bdi>
                    </div>
                  </div>

                  <p v-else class="notif-text">{{ notif.content || notif.body }}</p>
                  <span class="notif-time">{{ formatTime(notif.created_at || notif.client_received_at) }}</span>
                </div>
              </div>

            </component>
            </div>
          </section>
        </div>
      </div>
    </div>
  </AppPage>
</template>

<style scoped>
.notifications-view {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  min-height: 100%;
}

.notifications-topbar {
  display: block;
  min-width: 0;
}

.notifications-return {
  justify-self: start;
}

.content {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}

.notification-category-tabs {
  min-width: 0;
  margin-bottom: 0;
}

.notification-category-tabs :deep(.ui-filter-chips) {
  width: 100%;
}

.notification-category-tabs :deep(.ui-filter-chip) {
  min-width: 0;
  white-space: nowrap;
}

.notifications-section {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.notification-history-feedback {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  min-height: var(--ds-touch-target);
  padding: 0.55rem 0.75rem;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
}

.notification-history-feedback.is-refreshing {
  background: var(--ds-bg-page);
}

.notification-history-feedback.is-error {
  border-color: var(--ds-danger-500);
  background: var(--ds-danger-50);
  color: var(--ds-danger-500);
}

.notification-history-retry {
  flex: 0 0 auto;
}

.push-section :deep(.ui-section-card__body) {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.push-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  align-items: center;
  justify-content: flex-start;
}

.push-enable-btn {
  min-width: 7.5rem;
}

.push-action-message {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
}

.push-device-scope {
  margin: 0;
  color: var(--ds-info-700);
  font-size: var(--ds-font-xs);
}

.notif-item:focus-visible {
  outline: 3px solid rgba(245, 158, 11, 0.34);
  outline-offset: 3px;
}

.notifications-list {
  display: flex;
  flex-direction: column;
  gap: 0.7rem;
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 4rem);
}

.notif-item {
  position: relative;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  padding: 0.9rem;
  background: var(--ds-bg-card);
  border-radius: var(--ds-radius-lg);
  border: 1px solid var(--ds-border-light);
  border-right: 4px solid var(--ds-border-strong);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: var(--ds-shadow-xs);
  color: inherit;
  font: inherit;
  text-align: start;
}

.notif-item:is(button) {
  cursor: pointer;
}

.notif-item.is-unread {
  background: color-mix(in srgb, var(--ds-primary-50) 40%, var(--ds-bg-card) 60%);
  border-color: var(--ds-primary-100);
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.05);
}

.notif-item.type-info { border-right-color: var(--ds-info-500); }
.notif-item.type-success { border-right-color: var(--ds-success-500); }
.notif-item.type-warning { border-right-color: var(--ds-primary-500); }
.notif-item.type-error { border-right-color: var(--ds-danger-500); }

.notif-item.category-trade {
  gap: 0.55rem;
  padding: 0.8rem 0.9rem;
}

.notif-main {
  display: flex;
  gap: 0.75rem;
  min-width: 0;
}

.notif-item.category-trade .notif-main {
  gap: 0.7rem;
}

.notif-icon {
  position: relative;
  width: 40px;
  height: 40px;
  background: var(--ds-bg-page);
  border-radius: var(--ds-radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: var(--ds-primary-600);
}

.type-info .notif-icon { color: var(--ds-info-500); background: var(--ds-info-50); }
.type-success .notif-icon { color: var(--ds-success-500); background: var(--ds-success-50); }
.type-warning .notif-icon { color: var(--ds-primary-500); background: var(--ds-primary-50); }
.type-error .notif-icon { color: var(--ds-danger-500); background: var(--ds-danger-50); }

.notif-item.category-trade .notif-icon {
  width: 38px;
  height: 38px;
  border-radius: 12px;
}

.unread-dot {
  position: absolute;
  top: -4px;
  right: -4px;
  width: 10px;
  height: 10px;
  background: var(--ds-danger-500);
  border-radius: 50%;
  border: 2px solid var(--ds-bg-card);
}

.notif-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  min-width: 0;
}

.notif-item.category-trade .notif-body {
  gap: 0.25rem;
}

.notif-meta-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.notif-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.notif-title {
  margin: 0;
  font-size: var(--ds-font-md);
  font-weight: 700;
  color: var(--ds-text-primary);
  overflow-wrap: anywhere;
}

.notif-text {
  margin: 0;
  font-size: var(--ds-font-sm);
  color: var(--ds-text-secondary);
  line-height: 1.5;
  overflow-wrap: anywhere;
  white-space: pre-line;
  unicode-bidi: plaintext;
}

.notif-lines {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}

.notif-lines.is-trade-lines {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.35rem 0.45rem;
  align-items: start;
}

.notif-item.category-trade .notif-lines.is-trade-lines {
  gap: 0.28rem 0.35rem;
}

.notif-line {
  display: flex;
  align-items: flex-start;
  gap: 0.35rem;
  min-width: 0;
  unicode-bidi: plaintext;
}

.notif-line-field {
  display: grid;
  grid-template-columns: auto auto auto minmax(0, 1fr);
  align-items: baseline;
  padding: 0.32rem 0.5rem;
  border-radius: 12px;
  background: var(--ds-bg-page);
  border: 1px solid var(--ds-border-light);
}

.notif-item.category-trade .notif-line-field {
  padding: 0.24rem 0.42rem;
  border-radius: 10px;
}

.notif-line-plain {
  font-size: var(--ds-font-sm);
  font-weight: 700;
  color: var(--ds-text-primary);
}

.notif-lines.is-trade-lines .notif-line-field {
  background: color-mix(in srgb, var(--ds-bg-page) 90%, var(--ds-primary-50) 10%);
}

.notif-lines.is-trade-lines .notif-line-plain,
.notif-lines.is-trade-lines .notif-line-wide {
  grid-column: 1 / -1;
}

.notif-lines.is-trade-lines .notif-line-plain {
  padding-bottom: 0.18rem;
  margin-bottom: 0.02rem;
  border-bottom: 1px dashed var(--ds-border-light);
}

.notif-line-icon {
  flex: 0 0 auto;
  line-height: 1.4;
}

.notif-line-label {
  font-size: var(--ds-font-xs);
  font-weight: 700;
  color: var(--ds-text-primary);
  overflow-wrap: anywhere;
}

.notif-line-separator {
  color: var(--ds-text-muted);
  font-weight: 700;
}

.notif-line-value,
.notif-line-text {
  min-width: 0;
  line-height: 1.45;
  color: var(--ds-text-secondary);
  overflow-wrap: anywhere;
}

.notif-line-value {
  font-size: var(--ds-font-xs);
}

.notif-line-text {
  font-size: var(--ds-font-sm);
  color: var(--ds-text-primary);
}

.notif-item.category-trade .notif-line-label,
.notif-item.category-trade .notif-line-value {
  font-size: var(--ds-font-xs);
}

.notif-item.category-trade .notif-line-text {
  font-size: var(--ds-font-sm);
}

.notif-time {
  align-self: flex-start;
  margin-top: 0.15rem;
  font-size: var(--ds-font-xs);
  font-weight: 500;
  color: var(--ds-text-placeholder);
}

.notif-item.category-trade .notif-time {
  margin-top: 0;
  font-size: var(--ds-font-xs);
}

@media (max-width: 640px) {
  .notif-item {
    padding: 0.8rem;
  }

  .notif-item.category-trade {
    padding: 0.72rem 0.78rem;
  }

  .notif-main {
    gap: 0.75rem;
  }

  .notif-meta-row {
    flex-direction: column;
  }

  .notif-lines.is-trade-lines {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
