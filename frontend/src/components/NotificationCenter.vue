<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';

const props = defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();

const emit = defineEmits(['navigate']);

interface Notification {
  id: number;
  message: string;
  is_read: boolean;
  created_at: string;
}

const notifications = ref<Notification[]>([]);
const isLoading = ref(false);

const API_HEADERS = computed(() => ({
  'Content-Type': 'application/json',
  Authorization: `Bearer ${props.jwtToken}`,
}));

async function fetchNotifications() {
  isLoading.value = true;
  try {
    const response = await fetch(`${props.apiBaseUrl}/api/notifications/`, { headers: API_HEADERS.value });
    if (response.ok) {
      notifications.value = await response.json();
    }
  } catch (e) {
    console.error("Failed to fetch notifications", e);
  } finally {
    isLoading.value = false;
  }
}

async function deleteNotification(id: number) {
  // حذف خوش‌بینانه (Optimistic UI Update)
  const originalList = [...notifications.value];
  notifications.value = notifications.value.filter(n => n.id !== id);

  try {
    const response = await fetch(`${props.apiBaseUrl}/api/notifications/${id}`, {
      method: 'DELETE',
      headers: API_HEADERS.value
    });
    if (!response.ok) throw new Error();
  } catch (e) {
    // اگر خطا داد، برگردان
    notifications.value = originalList;
    alert("خطا در حذف پیام");
  }
}

function formatDate(dateString: string) {
  const date = new Date(dateString);
  return date.toLocaleDateString('fa-IR') + ' ' + date.toLocaleTimeString('fa-IR', {hour: '2-digit', minute:'2-digit'});
}

onMounted(fetchNotifications);
</script>

<template>
  <div class="notif-center-container">
    <div class="card">
      <div class="header-row">
        <h2>🔔 صندوق پیام‌ها</h2>
        <button class="back-button" @click="$emit('navigate', 'profile')">🔙</button>
      </div>
      
      <div v-if="isLoading" class="loading">در حال دریافت...</div>
      <div v-else-if="notifications.length === 0" class="no-data">پیامی ندارید.</div>
      
      <div v-else class="notif-list">
        <div v-for="notif in notifications" :key="notif.id" class="notif-item" :class="{ unread: !notif.is_read }">
          <div class="notif-header">
            <span class="date">{{ formatDate(notif.created_at) }}</span>
            <button class="delete-btn" @click="deleteNotification(notif.id)">❌</button>
          </div>
          <div class="notif-body" v-html="notif.message.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>')"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.notif-center-container { display: flex; flex-direction: column; gap: 16px; }
.card { background-color: var(--card-bg); border-radius: 12px; padding: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
.header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
h2 { margin: 0; font-size: 18px; }
.back-button { background: transparent; border: none; font-size: 20px; cursor: pointer; }
.loading, .no-data { text-align: center; padding: 20px; color: var(--text-secondary); }
.notif-list { display: flex; flex-direction: column; gap: 12px; }
.notif-item { background-color: #f9fafb; border: 1px solid var(--border-color); border-radius: 10px; padding: 12px; position: relative; transition: all 0.2s; }
.notif-item.unread { background-color: #f0f9ff; border-color: #bae6fd; border-right: 4px solid #007aff; }
.notif-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.date { font-size: 12px; color: var(--text-secondary); }
.delete-btn { background: transparent; border: none; cursor: pointer; font-size: 12px; padding: 4px; opacity: 0.6; }
.delete-btn:hover { opacity: 1; transform: scale(1.1); }
.notif-body { font-size: 14px; line-height: 1.6; color: var(--text-color); word-wrap: break-word; }
</style>