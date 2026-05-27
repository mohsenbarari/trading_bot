<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useNotificationStore } from '../stores/notifications'
import { Bell, ChevronLeft, Trash2, Circle, CheckCircle2, Mail, MailOpen } from 'lucide-vue-next'
import { getNotificationIconComponent } from '../utils/notificationUi'
import type { NormalizedAppNotification } from '../types/notifications'

const router = useRouter()
const notificationStore = useNotificationStore()
const isClearingAll = ref(false)

const goBack = () => {
  router.push('/')
}

const formatTime = (ts: any) => {
  if (!ts) return ''
  const date = new Date(ts)
  return date.toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' })
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
  const isWide = label === 'زمان معامله' || label === 'مسیر'

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
  } finally {
    isClearingAll.value = false
  }
}

const openNotificationRoute = (notification: NormalizedAppNotification) => {
  const routePath = typeof notification.route === 'string' ? notification.route.trim() : ''
  if (!routePath) return
  router.push(routePath)
}

onMounted(async () => {
  await notificationStore.openNotificationCenter()
})
</script>


<template>
  <div class="ds-page">
    <header class="header-row">
      <div class="header-spacer">
        <button class="clear-btn" :disabled="isClearingAll" @click="clearAll" v-if="notificationStore.appNotifications.length > 0">
          <Trash2 :size="18" />
        </button>
      </div>
      <div class="header-title">
        <h2>مرکز اعلان‌ها</h2>
      </div>
      <button class="back-button" @click="goBack">
        <ChevronLeft :size="24" />
      </button>
    </header>

    <main class="content">
      <div v-if="notificationStore.isLoadingHistory" class="ds-loading-state">
        <div class="ds-spinner"></div>
      </div>

      <div v-else-if="notificationStore.appNotifications.length === 0" class="ds-empty-state">
        <div class="ds-empty-icon">
          <Bell :size="48" />
        </div>
        <p>هیچ اعلانی یافت نشد</p>
      </div>

      <div v-else class="notifications-list">
        <div 
          v-for="notif in notificationStore.appNotifications" 
          :key="notif.id"
          class="notif-item"
          :class="[`type-${notif.level || 'info'}`, { 'is-unread': !notif.is_read }]"
          @click="openNotificationRoute(notif)"
        >
          <div class="notif-actions">
            <button class="action-btn delete-btn" @click.stop="notificationStore.deleteNotification(notif.id)" title="حذف">
              <Trash2 :size="16" />
            </button>
            <button class="action-btn toggle-read-btn" @click.stop="notificationStore.toggleReadStatus(notif.id, !notif.is_read)" :title="notif.is_read ? 'خوانده نشده' : 'خوانده شده'">
              <component :is="notif.is_read ? Mail : MailOpen" :size="16" />
            </button>
          </div>
          
          <div class="notif-icon">
            <component :is="getNotificationIconComponent(notif)" :size="20" />
            <div v-if="!notif.is_read" class="unread-dot"></div>
          </div>
          <div class="notif-body">
            <h3 class="notif-title">{{ notif.title || 'اعلان جدید' }}</h3>
            <div v-if="shouldUseStructuredLines(notif)" class="notif-lines" :class="{ 'is-trade-lines': notif.category === 'trade' }">
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
      </div>
    </main>
  </div>
</template>

<style scoped>
.clear-btn {
  background: var(--ds-danger-50);
  border: 1px solid var(--ds-danger-100);
  color: var(--ds-danger-500);
  cursor: pointer;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.clear-btn:hover {
  background: var(--ds-danger-100);
}

.clear-btn:active {
  transform: scale(0.95);
}

.content {
  padding: var(--ds-card-padding);
}

.notifications-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: var(--ds-card-padding);
  padding-bottom: 12rem; /* Ensure space for bottom nav and extra buffer */
}

.notif-item {
  position: relative;
  display: flex;
  gap: 1rem;
  padding: 1.25rem;
  background: var(--ds-bg-card);
  border-radius: var(--ds-radius-xl);
  border: 1px solid var(--ds-border-light);
  border-right: 5px solid var(--ds-border-strong);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: var(--ds-shadow-xs);
}

.notif-item.is-unread {
  background: #fdfaf3;
  border-color: var(--ds-primary-100);
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.05);
}

.notif-item.type-info { border-right-color: var(--ds-info-500); }
.notif-item.type-success { border-right-color: var(--ds-success-500); }
.notif-item.type-warning { border-right-color: var(--ds-primary-500); }
.notif-item.type-error { border-right-color: var(--ds-danger-500); }

.notif-actions {
  position: absolute;
  top: 0.75rem;
  left: 0.75rem;
  display: flex;
  gap: 0.5rem;
  opacity: 0;
  transition: opacity 0.2s;
}

.notif-item:hover .notif-actions {
  opacity: 1;
}

.action-btn {
  width: 32px;
  height: 32px;
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
.delete-btn:hover { background: var(--ds-danger-100); transform: scale(1.1); }

.toggle-read-btn {
  background: var(--ds-bg-page);
  color: var(--ds-text-muted);
}
.toggle-read-btn:hover { background: var(--ds-bg-hover); color: var(--ds-text-primary); }

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
  padding-left: 1.5rem; /* Space for actions */
  min-width: 0;
}

.notif-title {
  font-size: var(--ds-font-lg);
  font-weight: 700;
  color: var(--ds-text-primary);
  margin: 0;
}

.notif-text {
  font-size: var(--ds-font-base);
  color: var(--ds-text-secondary);
  margin: 0;
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
  padding-bottom: 0.25rem;
  margin-bottom: 0.1rem;
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

.notif-time {
  font-size: var(--ds-font-sm);
  color: var(--ds-text-placeholder);
  margin-top: 0.15rem;
  font-weight: 500;
  align-self: flex-start;
}

@media (max-width: 640px) {
  .notif-item {
    padding: 0.95rem;
  }

  .notif-body {
    padding-left: 0;
    padding-top: 2.35rem;
  }

  .notif-actions {
    opacity: 1;
  }

  .notif-lines.is-trade-lines {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
