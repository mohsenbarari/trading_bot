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

async function markAllAsRead() {
  try {
    await fetch(`${props.apiBaseUrl}/api/notifications/mark-all-read`, {
      method: 'POST',
      headers: API_HEADERS.value
    });
  } catch (e) {
    console.error("Failed to mark all as read", e);
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

onMounted(async () => {
  // ۱. اول لیست پیام‌ها را بگیر و به کاربر نشان بده
  await fetchNotifications();
  
  // ۲. حالا که کاربر آن‌ها را دیده، در پس‌زمینه به بک‌اند بگو خوانده شدند
  await markAllAsRead();
});
</script>

<template>
  <div class="notif-center-container">
    <div class="card">
      
      <div class="header-row">
        <button class="back-button" @click="$emit('navigate', 'profile')">
          <span>🔙</span>
          بازگشت
        </button>
      </div>
      
      <div v-if="isLoading" class="loading">در حال دریافت...</div>
      <div v-else-if="notifications.length === 0" class="no-data">پیامی ندارید.</div>
      
      <div v-else class="notif-list">
        <div v-for="notif in notifications" :key="notif.id" class="notif-item" :class="{ unread: !notif.is_read }">
          
          <button class="delete-btn" @click="deleteNotification(notif.id)">❌</button>

          <div class="notif-header">
            <span class="date">{{ formatDate(notif.created_at) }}</span>
          </div>
          <div class="notif-body" v-html="notif.message.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>')"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.notif-center-container { 
  display: flex; 
  flex-direction: column; 
  gap: 16px; 
}
.card { 
  background-color: var(--card-bg); 
  border-radius: 12px; 
  padding: 16px; 
  box-shadow: 0 4px 12px rgba(0,0,0,0.08); 
  /* درخواست ۲ (هدر ثابت): 
    اگر می‌خواهید هدر ثابت باشد و کارت اسکرول بخورد، 
    باید به 'card' یک 'max-height' بدهید و 'overflow-y: auto'
    و هدر را 'position: sticky; top: 0; background: white; z-index: 1;' کنید.
  */
}

/* --- درخواست ۱: اصلاح هدر --- */
.header-row { 
  display: flex; 
  justify-content: flex-end; /* دکمه بازگشت را به راست می‌برد (چون h2 حذف شد) */
  align-items: center; 
  margin-bottom: 16px; 
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-color);
}
.back-button { 
  background: transparent; 
  border: none; 
  font-size: 15px; /* اندازه فونت متن */
  font-weight: 600;
  color: var(--primary-color);
  cursor: pointer; 
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 8px;
  transition: background-color 0.2s;
}
.back-button span {
  font-size: 18px; /* اندازه آیکون */
}
.back-button:hover {
  background-color: #f0f5ff;
}

.loading, .no-data { 
  text-align: center; 
  padding: 20px; 
  color: var(--text-secondary); 
}
.notif-list { 
  display: flex; 
  flex-direction: column; 
  gap: 12px; 
}

/* --- درخواست ۳: فشرده‌سازی کادر پیام --- */
.notif-item { 
  background-color: #f9fafb; 
  border: 1px solid var(--border-color); 
  border-radius: 10px; 
  padding: 10px 12px; /* پدینگ عمودی و افقی کمتر شد */
  position: relative; /* برای دکمه حذف */
  transition: all 0.2s;
  padding-left: 36px; /* فضای خالی برای دکمه حذف در سمت چپ */
}
.notif-item.unread { 
  background-color: #f0f9ff; 
  border-color: #bae6fd; 
  border-right: 4px solid #007aff; 
}

/* --- درخواست ۴: دکمه حذف (ضربدر) --- */
.delete-btn {
  position: absolute;
  top: 8px;  /* فاصله از بالا */
  left: 8px; /* انتقال به منتهی‌الیه چپ */
  
  background: transparent;
  border: none;
  cursor: pointer;
  
  font-size: 14px; /* اندازه خود آیکون ❌ */
  padding: 0;      /* حذف پدینگ برای فیت شدن کادر */
  
  /* یک کادر مربعی برای کلیک راحت‌تر */
  width: 26px; 
  height: 26px;
  
  /* وسط‌چینی آیکون در کادر */
  display: flex;
  align-items: center;
  justify-content: center;
  
  opacity: 0.5;
  border-radius: 50%; /* گرد کردن کادر */
  transition: all 0.2s;
}
.delete-btn:hover { 
  opacity: 1; 
  transform: scale(1.1);
  background-color: #f0f0f0; /* نمایش محدوده کلیک در هاور */
}

/* --- اصلاحات جانبی برای درخواست ۴ --- */
.notif-header { 
  display: flex; 
  justify-content: flex-end; /* دکمه حذف از جریان خارج شد، تاریخ به راست می‌رود */
  align-items: center; 
  margin-bottom: 6px; /* فاصله کمتر */
}
.date { 
  font-size: 12px; 
  color: var(--text-secondary); 
}
.notif-body { 
  font-size: 13px; /* فونت کمی کوچک‌تر */
  line-height: 1.5; /* فاصله خطوط فشرده‌تر */
  color: var(--text-color); 
  word-wrap: break-word; 
}
</style>