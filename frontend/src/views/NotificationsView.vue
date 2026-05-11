<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useNotificationStore } from '../stores/notifications'
import { Bell, ChevronLeft, Trash2, Circle, CheckCircle2, Mail, MailOpen } from 'lucide-vue-next'
import { getNotificationIconComponent } from '../utils/notificationUi'

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

const clearAll = async () => {
  if (isClearingAll.value || notificationStore.appNotifications.length === 0) return
  isClearingAll.value = true
  try {
    await notificationStore.clearAllNotifications()
  } finally {
    isClearingAll.value = false
  }
}

onMounted(async () => {
  await notificationStore.openNotificationCenter()
})
</script>


<template>
  <div class="notifications-page">
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
      <div v-if="notificationStore.isLoadingHistory" class="loading-state">
        <div class="loading-spinner"></div>
      </div>

      <div v-else-if="notificationStore.appNotifications.length === 0" class="empty-state">
        <div class="empty-icon">
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
            <p class="notif-text">{{ notif.content || notif.body }}</p>
            <span class="notif-time">{{ formatTime(notif.created_at || notif.client_received_at) }}</span>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>

<style scoped>
.notifications-page {
  min-height: 100dvh;
  background: #f9fafb;
}

.clear-btn {
  background: #fef2f2;
  border: 1px solid #fee2e2;
  color: #ef4444;
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
  background: #fee2e2;
}

.clear-btn:active {
  transform: scale(0.95);
}

.content {
  padding: 1rem;
}

.empty-state, .loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 4rem 1rem;
  color: #9ca3af;
  text-align: center;
}

.loading-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid #f59e0b;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}

.empty-icon {
  width: 80px;
  height: 80px;
  background: white;
  border-radius: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto;
  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
}

.notifications-list {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem;
  padding-bottom: 12rem; /* Ensure space for bottom nav and extra buffer */
}

.notif-item {
  position: relative;
  display: flex;
  gap: 1rem;
  padding: 1.25rem;
  background: white;
  border-radius: 1.25rem;
  border: 1px solid #f3f4f6;
  border-right: 5px solid #d1d5db;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  box-shadow: 0 2px 4px rgba(0,0,0,0.02);
}

.notif-item.is-unread {
  background: #fdfaf3;
  border-color: #fef3c7;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.05);
}

.notif-item.type-info { border-right-color: #0ea5e9; }
.notif-item.type-success { border-right-color: #10b981; }
.notif-item.type-warning { border-right-color: #f59e0b; }
.notif-item.type-error { border-right-color: #ef4444; }

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
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}

.delete-btn {
  background: #fef2f2;
  color: #ef4444;
}
.delete-btn:hover { background: #fee2e2; transform: scale(1.1); }

.toggle-read-btn {
  background: #f9fafb;
  color: #6b7280;
}
.toggle-read-btn:hover { background: #f3f4f6; color: #1f2937; }

.notif-icon {
  position: relative;
  width: 48px;
  height: 48px;
  background: #f9fafb;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: #d97706;
}

.type-info .notif-icon { color: #0ea5e9; background: #f0f9ff; }
.type-success .notif-icon { color: #10b981; background: #f0fdf4; }
.type-warning .notif-icon { color: #f59e0b; background: #fffbeb; }
.type-error .notif-icon { color: #ef4444; background: #fef2f2; }

.unread-dot {
  position: absolute;
  top: -4px;
  right: -4px;
  width: 10px;
  height: 10px;
  background: #ef4444;
  border-radius: 50%;
  border: 2px solid white;
}

.notif-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding-left: 1.5rem; /* Space for actions */
}

.notif-title {
  font-size: 1rem;
  font-weight: 700;
  color: #1f2937;
  margin: 0;
}

.notif-text {
  font-size: 0.875rem;
  color: #4b5563;
  margin: 0;
  line-height: 1.5;
}

.notif-time {
  font-size: 0.75rem;
  color: #9ca3af;
  margin-top: 0.25rem;
  font-weight: 500;
}
</style>
