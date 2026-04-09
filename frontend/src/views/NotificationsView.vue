<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useNotificationStore } from '../stores/notifications'
import { Bell, ArrowRight, Trash2, ShieldAlert, CheckCircle, AlertTriangle, AlertCircle } from 'lucide-vue-next'

const router = useRouter()
const notificationStore = useNotificationStore()

const goBack = () => {
  router.push('/')
}

const clearAll = () => {
  // Not implemented in backend as a single DELETE all, but we mark all as read.
  // We can just rely on delete individual for now, or just let them disappear over time.
}

onMounted(async () => {
  // Fetch history when entering this page
  await notificationStore.fetchHistory()
  // As a convenience, we mark all as read upon opening the center
  await notificationStore.markAllAsRead()
})

const getIconForType = (level: string, category: string) => {
  if (category === 'system') return ShieldAlert;
  if (level === 'success') return CheckCircle;
  if (level === 'warning') return AlertTriangle;
  if (level === 'error') return AlertCircle;
  return Bell;
}
</script>


<template>
  <div class="notifications-page">
    <header class="top-nav">
      <button class="back-btn" @click="goBack">
        <ArrowRight :size="20" />
      </button>
      <h1 class="title">مرکز اعلان‌ها</h1>
      <button class="clear-btn" @click="clearAll" v-if="notificationStore.appNotifications.length > 0">
        <Trash2 :size="18" />
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
          :class="`type-${notif.level || 'info'}`"
        >
          <button class="delete-btn-corner" @click="notificationStore.deleteNotification(notif.id)">
            <Trash2 :size="14" />
          </button>
          
          <div class="notif-icon">
            <component :is="getIconForType(notif.level, notif.category)" :size="18" />
          </div>
          <div class="notif-body">
            <h3 class="notif-title">{{ notif.title || 'اعلان جدید' }}</h3>
            <p class="notif-text">{{ notif.content || notif.body }}</p>
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

.top-nav {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1rem;
  background: white;
  border-bottom: 1px solid #f3f4f6;
  position: sticky;
  top: 0;
  z-index: 10;
}

.back-btn, .clear-btn {
  background: none;
  border: none;
  color: #6b7280;
  cursor: pointer;
  padding: 0.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
}

.title {
  flex: 1;
  font-size: 1.1rem;
  font-weight: 700;
  text-align: right;
  margin: 0;
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
  gap: 0.75rem;
}

.notif-item {
  background: white;
  padding: 1rem 1rem 1rem 2rem;
  border-radius: 1rem;
  display: flex;
  gap: 1rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  position: relative;
  overflow: hidden;
  border-right: 4px solid #0ea5e9; /* default info color */
}

.notif-item.type-success { border-right-color: #10b981; }
.notif-item.type-warning { border-right-color: #f59e0b; }
.notif-item.type-error { border-right-color: #ef4444; }

.notif-icon {
  width: 36px;
  height: 36px;
  background: #f1f5f9;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.type-info .notif-icon { color: #0ea5e9; background: #e0f2fe; }
.type-success .notif-icon { color: #10b981; background: #d1fae5; }
.type-warning .notif-icon { color: #f59e0b; background: #fef3c7; }
.type-error .notif-icon { color: #ef4444; background: #fee2e2; }

.notif-body {
  flex: 1;
}

.notif-title {
  font-size: 0.9rem;
  font-weight: 700;
  margin: 0 0 0.25rem 0;
  color: #1f2937;
}

.notif-text {
  font-size: 0.8rem;
  color: #6b7280;
  margin: 0;
  line-height: 1.5;
}

.delete-btn-corner {
  position: absolute;
  top: 8px;
  left: 8px;
  background: transparent;
  border: none;
  color: #d1d5db;
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}
.delete-btn-corner:hover {
  color: #ef4444;
  background: #fee2e2;
}
</style>
