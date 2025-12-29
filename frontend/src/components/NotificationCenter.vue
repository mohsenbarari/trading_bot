<script setup lang="ts">
import { ref, onMounted, computed } from 'vue';
import LoadingSkeleton from './LoadingSkeleton.vue';

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
  level: 'info' | 'success' | 'warning' | 'error';
  category: 'system' | 'user' | 'trade';
}

const notifications = ref<Notification[]>([]);
const isLoading = ref(true);

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
  const originalList = [...notifications.value];
  notifications.value = notifications.value.filter(n => n.id !== id);

  try {
    const response = await fetch(`${props.apiBaseUrl}/api/notifications/${id}`, {
      method: 'DELETE',
      headers: API_HEADERS.value
    });
    if (!response.ok) throw new Error();
  } catch (e) {
    notifications.value = originalList;
    alert("خطا در حذف پیام");
  }
}

function formatDate(dateString: string) {
  const date = new Date(dateString);
  return date.toLocaleDateString('fa-IR') + ' ' + date.toLocaleTimeString('fa-IR', {hour: '2-digit', minute:'2-digit'});
}

onMounted(async () => {
  await fetchNotifications();
  await markAllAsRead();
});

function getIcon(level: string, category: string) {
  if (category === 'system') return '🛡️';
  if (level === 'success') return '✅';
  if (level === 'warning') return '⚠️';
  if (level === 'error') return '⛔';
  return '📌';
}
</script>

<template>
  <div class="notif-center-container">
    <div class="card">
       
       <div class="header-row">
         <span class="page-title">صندوق پیام‌ها</span>
         
         <button class="back-button" @click="$emit('navigate', 'profile')">
           🔙
         </button>
       </div>
       
       <div v-if="isLoading">
          <LoadingSkeleton :count="5" :height="100" />
       </div>
       <div v-else-if="notifications.length === 0" class="no-data">پیامی ندارید.</div>
       
       <div v-else class="notif-list">
        <div 
          v-for="notif in notifications" 
          :key="notif.id" 
          class="notif-item" 
          :class="[`type-${notif.level}`, `cat-${notif.category}`, { unread: !notif.is_read }]"
        >
           
           <button class="delete-btn" @click="deleteNotification(notif.id)">❌</button>
 
           <div class="notif-content-wrapper">
             <div class="notif-icon-col">
               <span class="icon">{{ getIcon(notif.level, notif.category) }}</span>
             </div>
             <div class="notif-text-col">
                <div class="notif-header">
                   <span v-if="notif.category === 'system'" class="badge-system">مدیریت</span>
                   <span class="date">{{ formatDate(notif.created_at) }}</span>
                </div>
                <div class="notif-body" v-html="notif.message.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>')"></div>
             </div>
           </div>
 
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
 }
 
 /* --- هدر اصلاح شده --- */
 .header-row { 
  display: flex; 
  justify-content: space-between; /* فاصله بین عنوان و دکمه */
  align-items: center; 
  margin-bottom: 16px; 
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-color);
 }

 .page-title {
    font-weight: 700;
    font-size: 16px;
    color: var(--text-color);
 }

 /* دکمه بازگشت کوچک */
 .back-button { 
  background: transparent; 
  border: none; 
  font-size: 20px; /* اندازه آیکون */
  cursor: pointer; 
  padding: 4px;    /* پدینگ کم */
  border-radius: 50%; /* گرد */
  transition: background-color 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;     /* عرض ثابت کوچک */
  height: 32px;    /* ارتفاع ثابت کوچک */
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
 
 /* --- کادر پیام --- */
 .notif-item { 
  background-color: #ffffff; 
  border: 1px solid var(--border-color); 
  border-radius: 12px; 
  padding: 12px; 
  position: relative; 
  transition: all 0.2s;
  display: flex;
  flex-direction: column;
  
  /* نوار رنگی سمت راست */
  border-right: 4px solid #007aff; 
 }
 
 /* --- دکمه حذف (اصلاح شده) --- */
 .delete-btn {
  position: absolute;
  top: 6px; 
  left: 6px; 
  
  background-color: transparent !important; 
  border: none !important;
  box-shadow: none !important;
  width: 24px !important;
  height: 24px !important;
  padding: 0 !important;
  min-width: auto !important;
  
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  
  color: #999; 
  opacity: 0.7;
  border-radius: 50%; 
  cursor: pointer;
  transition: all 0.2s;
  z-index: 5; 
 }
 
 .delete-btn:hover { 
  opacity: 1; 
  color: #ff3b30; 
  background-color: #f0f0f0 !important; 
  transform: scale(1.1);
 }
 
 /* --- استایل‌های رنگی بر اساس Level --- */
 .notif-item.type-success { border-right-color: #34c759; background-color: #f2fcf5; }
 .notif-item.type-warning { border-right-color: #ffcc00; background-color: #fffdf2; }
 .notif-item.type-error   { border-right-color: #ff3b30; background-color: #fff2f2; }
 .notif-item.type-info    { border-right-color: #007aff; background-color: #f0f9ff; }
 
 .notif-item.cat-system {
  background-color: #f8f9fa; 
  border-style: dashed; 
 }
 
 /* لی‌اوت داخلی */
 .notif-content-wrapper {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding-left: 20px; 
 }
 
 .notif-icon-col {
  font-size: 24px;
  line-height: 1;
  padding-top: 4px;
 }
 
 .notif-text-col {
  flex-grow: 1;
 }
 
 .notif-header { 
  display: flex; 
  justify-content: space-between; 
  align-items: center; 
  margin-bottom: 4px; 
 }
 
 .badge-system {
  background-color: #333;
  color: #fff;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: bold;
 }
 
 .date { font-size: 11px; color: var(--text-secondary); }
 
 .notif-body { 
  font-size: 13px; 
  line-height: 1.6; 
  color: var(--text-color); 
 }
 
 .notif-item.unread { 
  box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  font-weight: 500;
 }
 </style>