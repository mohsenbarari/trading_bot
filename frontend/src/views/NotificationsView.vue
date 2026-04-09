<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useNotificationStore } from '../stores/notifications'
import { Bell, ArrowRight, Trash2 } from 'lucide-vue-next'

const router = useRouter()
const notificationStore = useNotificationStore()

const goBack = () => {
  router.push('/')
}

const clearAll = () => {
  notificationStore.appNotifications = []
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
      <div v-if="notificationStore.appNotifications.length === 0" class="empty-state">
        <div class="empty-icon">
          <Bell :size="48" />
        </div>
        <p>هیچ اعلانی یافت نشد</p>
      </div>

      <div v-else class="notifications-list">
        <div 
          v-for="(notif, index) in notificationStore.appNotifications" 
          :key="index"
          class="notif-item"
        >
          <div class="notif-icon">
            <Bell :size="18" />
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

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1rem;
  padding: 4rem 1rem;
  color: #9ca3af;
  text-align: center;
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
  padding: 1rem;
  border-radius: 1rem;
  display: flex;
  gap: 1rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}

.notif-icon {
  width: 36px;
  height: 36px;
  background: #fef3c7;
  color: #d97706;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

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
</style>
