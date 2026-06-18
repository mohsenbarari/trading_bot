<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, BellOff, BellRing, ChevronLeft, Mail, MailOpen, Send, Trash2 } from 'lucide-vue-next'
import {
  AppButton,
  AppConfirmDialog,
  AppEmptyState,
  AppFilterChips,
  AppIconButton,
  AppLoadingState,
  AppPage,
  AppPageHeader,
  AppSectionCard,
  AppStatusBadge,
} from '../components/ui'
import { useNotificationStore } from '../stores/notifications'
import type { NormalizedAppNotification } from '../types/notifications'
import { formatIranTime } from '../utils/iranTime'
import { getNotificationIconComponent } from '../utils/notificationUi'
import {
  disableWebPushNotifications,
  enableWebPushNotifications,
  getWebPushStatus,
  sendWebPushTestNotification,
  type WebPushRuntimeState,
} from '../services/webPush'

const router = useRouter()
const notificationStore = useNotificationStore()
const isClearingAll = ref(false)
const activeFilter = ref<'all' | 'unread' | 'read'>('all')
const confirmClearAllOpen = ref(false)
const pendingDeleteNotification = ref<NormalizedAppNotification | null>(null)
const pushState = ref<WebPushRuntimeState>('checking')
const isPushBusy = ref(false)
const pushActionMessage = ref('')

const unreadCount = computed(() => notificationStore.appNotifications.filter((notification) => !notification.is_read).length)
const readCount = computed(() => notificationStore.appNotifications.length - unreadCount.value)
const totalCount = computed(() => notificationStore.appNotifications.length)
const filterOptions = computed(() => [
  { key: 'all' as const, label: `همه ${totalCount.value.toLocaleString('fa-IR')}` },
  { key: 'unread' as const, label: `خوانده‌نشده ${unreadCount.value.toLocaleString('fa-IR')}` },
  { key: 'read' as const, label: `خوانده‌شده ${readCount.value.toLocaleString('fa-IR')}` },
])
const filteredNotifications = computed(() => {
  if (activeFilter.value === 'unread') {
    return notificationStore.appNotifications.filter((notification) => !notification.is_read)
  }
  if (activeFilter.value === 'read') {
    return notificationStore.appNotifications.filter((notification) => notification.is_read)
  }
  return notificationStore.appNotifications
})
const activeFilterLabel = computed(() => {
  if (activeFilter.value === 'unread') return 'خوانده‌نشده'
  if (activeFilter.value === 'read') return 'خوانده‌شده'
  return 'همه اعلان‌ها'
})
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
  || pushState.value === 'error'
))
const canDisablePush = computed(() => pushState.value === 'subscribed')
const canTestPush = computed(() => pushState.value === 'subscribed')

const goBack = () => {
  router.push('/')
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

  const iconMatch = trimmed.match(/^(\S+)\s+(.*)$/)
  const icon = iconMatch?.[1] || ''
  const remainder = (iconMatch?.[2] || trimmed).trim()
  const colonIndex = remainder.indexOf(':')

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
    .split(/\r?\n+/)
    .map(parseNotificationLine)
    .filter((line): line is ParsedNotificationLine => line !== null)
}

const shouldUseStructuredLines = (notification: NormalizedAppNotification): boolean => {
  const body = notification.content || notification.body || ''
  return body.includes('\n') || notification.category === 'trade'
}

const clearAll = async () => {
  if (isClearingAll.value || notificationStore.appNotifications.length === 0) return
  isClearingAll.value = true
  try {
    await notificationStore.clearAllNotifications()
    confirmClearAllOpen.value = false
  } finally {
    isClearingAll.value = false
  }
}

async function refreshPushState() {
  pushActionMessage.value = ''
  pushState.value = 'checking'
  try {
    const status = await getWebPushStatus()
    pushState.value = status.state
  } catch (error) {
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
  } catch (error) {
    pushState.value = 'error'
    pushActionMessage.value = 'فعال‌سازی ناموفق بود'
  } finally {
    isPushBusy.value = false
  }
}

async function disablePush() {
  if (isPushBusy.value) return
  isPushBusy.value = true
  pushActionMessage.value = ''
  try {
    const status = await disableWebPushNotifications()
    pushState.value = status.state
    pushActionMessage.value = 'غیرفعال شد'
  } catch (error) {
    pushState.value = 'error'
    pushActionMessage.value = 'غیرفعال‌سازی ناموفق بود'
  } finally {
    isPushBusy.value = false
  }
}

async function sendPushTest() {
  if (isPushBusy.value) return
  isPushBusy.value = true
  pushActionMessage.value = ''
  try {
    const result = await sendWebPushTestNotification()
    pushActionMessage.value = result.sent > 0 ? 'تست ارسال شد' : 'دستگاهی ثبت نشده است'
  } catch (error) {
    pushActionMessage.value = 'ارسال تست ناموفق بود'
  } finally {
    isPushBusy.value = false
  }
}

const openNotificationRoute = (notification: NormalizedAppNotification) => {
  const routePath = typeof notification.route === 'string' ? notification.route.trim() : ''
  if (!routePath) return
  router.push(routePath)
}

function canOpenNotificationRoute(notification: NormalizedAppNotification): boolean {
  return typeof notification.route === 'string' && notification.route.trim().length > 0
}

function requestDelete(notification: NormalizedAppNotification) {
  pendingDeleteNotification.value = notification
}

function closeDeleteConfirm() {
  pendingDeleteNotification.value = null
}

async function confirmDeleteNotification() {
  const notification = pendingDeleteNotification.value
  if (!notification) return
  await notificationStore.deleteNotification(notification.id)
  pendingDeleteNotification.value = null
}

onMounted(async () => {
  void refreshPushState()
  await notificationStore.openNotificationCenter()
})
</script>

<template>
  <AppPage narrow>
    <div class="notifications-view">
      <AppPageHeader
        eyebrow="حساب کاربری"
        title="مرکز اعلان‌ها"
      >
        <template #actions>
          <AppIconButton type="button" class="notifications-return" label="بازگشت" size="sm" @click="goBack">
            <ChevronLeft :size="24" />
          </AppIconButton>
        </template>
      </AppPageHeader>

      <main class="content">
        <AppSectionCard
          title="اعلان دستگاه"
          :description="pushStatusLabel"
          class="push-section"
        >
          <template #actions>
            <AppStatusBadge :tone="pushStatusTone">{{ pushStatusLabel }}</AppStatusBadge>
          </template>

          <div class="push-controls">
            <AppButton
              v-if="canEnablePush"
              class="push-enable-btn"
              size="sm"
              :loading="isPushBusy"
              @click="enablePush"
            >
              <template #icon>
                <BellRing :size="16" />
              </template>
              فعال‌سازی
            </AppButton>
            <AppButton
              v-if="canTestPush"
              class="push-test-btn"
              variant="secondary"
              size="sm"
              :loading="isPushBusy"
              @click="sendPushTest"
            >
              <template #icon>
                <Send :size="16" />
              </template>
              تست
            </AppButton>
            <AppButton
              v-if="canDisablePush"
              class="push-disable-btn"
              variant="ghost"
              size="sm"
              :loading="isPushBusy"
              @click="disablePush"
            >
              <template #icon>
                <BellOff :size="16" />
              </template>
              غیرفعال
            </AppButton>
          </div>
          <p v-if="pushActionMessage" class="push-action-message">{{ pushActionMessage }}</p>
        </AppSectionCard>

        <AppLoadingState v-if="notificationStore.isLoadingHistory" class="ds-loading-state" label="در حال دریافت اعلان‌ها" />

        <AppEmptyState
          v-else-if="notificationStore.appNotifications.length === 0"
          title="هیچ اعلانی یافت نشد"
          message="اعلان‌های سیستم، بازار و معاملات بعد از دریافت در این بخش نمایش داده می‌شوند."
          tone="info"
        >
          <template #icon>
            <Bell :size="48" />
          </template>
        </AppEmptyState>

        <AppSectionCard
          v-else
          title="صندوق ورودی"
          :description="`فیلتر فعال: ${activeFilterLabel}`"
          class="notifications-section"
        >
          <template #actions>
            <div class="notifications-summary">
              <AppStatusBadge tone="warning">خوانده‌نشده {{ unreadCount.toLocaleString('fa-IR') }}</AppStatusBadge>
              <AppStatusBadge tone="neutral">کل {{ totalCount.toLocaleString('fa-IR') }}</AppStatusBadge>
              <AppButton
                class="clear-btn"
                variant="danger"
                size="sm"
                :loading="isClearingAll"
                @click="confirmClearAllOpen = true"
              >
                <template #icon>
                  <Trash2 :size="16" />
                </template>
                پاک‌سازی همه
              </AppButton>
            </div>
          </template>

          <AppFilterChips
            v-model="activeFilter"
            class="notification-toolbar"
            label="فیلتر اعلان‌ها"
            :options="filterOptions"
          />

          <AppEmptyState
            v-if="filteredNotifications.length === 0"
            class="notification-filter-empty"
            title="اعلانی در این فیلتر وجود ندارد"
            message="فیلتر دیگری را انتخاب کنید یا بعداً دوباره بررسی کنید."
            tone="neutral"
          >
            <template #icon>
              <Bell :size="40" />
            </template>
          </AppEmptyState>

          <div
            v-else
            :id="`notifications-${activeFilter}-panel`"
            class="notifications-list"
            role="tabpanel"
            :aria-label="`اعلان‌های ${filterOptions.find((option) => option.key === activeFilter)?.label || ''}`"
          >
            <div
              v-for="notif in filteredNotifications"
              :key="notif.id"
              class="notif-item"
              :class="[
                `type-${notif.level || 'info'}`,
                `category-${notif.category || 'system'}`,
                { 'is-unread': !notif.is_read },
              ]"
              :role="canOpenNotificationRoute(notif) ? 'button' : undefined"
              :tabindex="canOpenNotificationRoute(notif) ? 0 : undefined"
              :aria-label="canOpenNotificationRoute(notif) ? `باز کردن اعلان ${notif.title || 'اعلان جدید'}` : undefined"
              @click="openNotificationRoute(notif)"
              @keydown.enter.prevent="openNotificationRoute(notif)"
              @keydown.space.prevent="openNotificationRoute(notif)"
            >
              <div class="notif-main">
                <div class="notif-icon">
                  <component :is="getNotificationIconComponent(notif)" :size="20" />
                  <div v-if="!notif.is_read" class="unread-dot"></div>
                </div>

                <div class="notif-body">
                  <div v-if="notif.category !== 'trade'" class="notif-meta-row">
                    <h3 class="notif-title">{{ notif.title || 'اعلان جدید' }}</h3>
                    <div class="notif-badges">
                      <AppStatusBadge :tone="notif.is_read ? 'neutral' : 'warning'">
                        {{ notif.is_read ? 'خوانده‌شده' : 'جدید' }}
                      </AppStatusBadge>
                    </div>
                  </div>

                  <div
                    v-if="shouldUseStructuredLines(notif)"
                    class="notif-lines"
                    :class="{ 'is-trade-lines': notif.category === 'trade' }"
                  >
                    <div
                      v-for="(line, lineIndex) in getNotificationLines(notif)"
                      :key="`${notif.id}-line-${lineIndex}`"
                      class="notif-line"
                      :class="[
                        line.isField ? 'notif-line-field' : 'notif-line-plain',
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

              <div class="notif-actions">
                <button
                  type="button"
                  class="notification-control toggle-read-btn"
                  :aria-label="notif.is_read ? `علامت‌گذاری ${notif.title || 'اعلان جدید'} به عنوان خوانده‌نشده` : `علامت‌گذاری ${notif.title || 'اعلان جدید'} به عنوان خوانده‌شده`"
                  @click.stop="notificationStore.toggleReadStatus(notif.id, !notif.is_read)"
                >
                  <component :is="notif.is_read ? Mail : MailOpen" :size="16" />
                </button>
                <button
                  type="button"
                  class="notification-control delete-btn"
                  :aria-label="`حذف اعلان ${notif.title || 'اعلان جدید'}`"
                  @click.stop="requestDelete(notif)"
                >
                  <Trash2 :size="16" />
                </button>
              </div>
            </div>
          </div>
        </AppSectionCard>
      </main>
    </div>
  </AppPage>

  <AppConfirmDialog
    :open="confirmClearAllOpen"
    title="پاک‌سازی همه اعلان‌ها"
    message="همه اعلان‌های فعلی از این مرکز حذف می‌شوند. این عمل فقط روی inbox فعلی شما اثر می‌گذارد."
    confirm-label="پاک‌سازی"
    cancel-label="انصراف"
    tone="danger"
    @cancel="confirmClearAllOpen = false"
    @confirm="clearAll"
  />

  <AppConfirmDialog
    :open="Boolean(pendingDeleteNotification)"
    title="حذف اعلان"
    :message="`اعلان «${pendingDeleteNotification?.title || 'اعلان جدید'}» از inbox شما حذف می‌شود.`"
    confirm-label="حذف اعلان"
    cancel-label="انصراف"
    tone="danger"
    @cancel="closeDeleteConfirm"
    @confirm="confirmDeleteNotification"
  />
</template>

<style scoped>
.notifications-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  min-height: 100%;
}

.content {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.notifications-summary {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  align-items: center;
  gap: 0.5rem;
}

.notification-toolbar {
  margin-bottom: 1rem;
}

.notifications-section :deep(.ui-section-card__body) {
  display: flex;
  flex-direction: column;
  gap: 1rem;
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
}

.push-action-message {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
}

.notif-item:focus-visible,
.notification-control:focus-visible {
  outline: 3px solid rgba(245, 158, 11, 0.34);
  outline-offset: 3px;
}

.notifications-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 4rem);
}

.notif-item {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  padding: 1.25rem;
  background: var(--ds-bg-card);
  border-radius: var(--ds-radius-xl);
  border: 1px solid var(--ds-border-light);
  border-right: 5px solid var(--ds-border-strong);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: var(--ds-shadow-xs);
}

.notif-item[role='button'] {
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
  gap: 1rem;
  min-width: 0;
}

.notif-item.category-trade .notif-main {
  gap: 0.7rem;
}

.notif-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}

.notif-item.category-trade .notif-actions {
  gap: 0.35rem;
}

.notification-control {
  min-width: var(--ds-touch-target);
  min-height: var(--ds-touch-target);
  border-radius: 10px;
  border: none;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  box-shadow: var(--ds-shadow-sm);
}

.delete-btn {
  background: var(--ds-danger-50);
  color: var(--ds-danger-500);
}

.delete-btn:hover {
  background: var(--ds-danger-100);
  transform: scale(1.04);
}

.toggle-read-btn {
  background: var(--ds-bg-page);
  color: var(--ds-text-muted);
}

.toggle-read-btn:hover {
  background: var(--ds-bg-hover);
  color: var(--ds-text-primary);
}

.notif-icon {
  position: relative;
  width: 48px;
  height: 48px;
  background: var(--ds-bg-page);
  border-radius: 14px;
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
  font-size: var(--ds-font-lg);
  font-weight: 700;
  color: var(--ds-text-primary);
}

.notif-text {
  margin: 0;
  font-size: var(--ds-font-base);
  color: var(--ds-text-secondary);
  line-height: 1.5;
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
  font-size: var(--ds-font-base);
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
  font-size: var(--ds-font-sm);
  font-weight: 700;
  color: var(--ds-text-primary);
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
}

.notif-line-value {
  font-size: 0.84rem;
}

.notif-line-text {
  font-size: var(--ds-font-base);
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
  font-size: var(--ds-font-sm);
  font-weight: 500;
  color: var(--ds-text-placeholder);
}

.notif-item.category-trade .notif-time {
  margin-top: 0;
  font-size: var(--ds-font-xs);
}

@media (max-width: 640px) {
  .notifications-summary {
    width: 100%;
    justify-content: stretch;
  }

  .notifications-summary :deep(.ui-button) {
    width: 100%;
  }

  .notif-item {
    padding: 0.95rem;
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
