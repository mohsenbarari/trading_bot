<script setup lang="ts">
import { ref, onMounted, computed, onUnmounted } from 'vue'

import MainMenu from './components/MainMenu.vue'
import UserProfile from './components/UserProfile.vue'
import AdminPanel from './components/AdminPanel.vue'
import CommodityManager from './components/CommodityManager.vue' 
import UserManager from './components/UserManager.vue'
import CreateInvitationView from './components/CreateInvitationView.vue'
import PlaceholderView from './components/PlaceholderView.vue'
import NotificationCenter from './components/NotificationCenter.vue'

const user = ref<any>(null)
const loadingMessage = ref('در حال اتصال...')
const activeView = ref('trade') 
const jwtToken = ref<string | null>(null)
const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp
const isLoading = computed(() => !user.value && loadingMessage.value)
const showTradePage = ref(true) 

// --- نوتیفیکیشن ---
const notificationMessage = ref<string | null>(null);
const lastNotificationId = ref<number | null>(null);
let notificationInterval: any = null;

async function checkNotifications() {
  if (!jwtToken.value) return;
  if (activeView.value === 'notifications') return;

  try {
    const res = await fetch(`${API_BASE_URL}/api/notifications/unread`, {
      headers: { Authorization: `Bearer ${jwtToken.value}` }
    });
    if (res.ok) {
      const data = await res.json();
      if (data.length > 0) {
        const latestMsg = data[0];
        if (latestMsg.id !== lastNotificationId.value) {
            notificationMessage.value = latestMsg.message.replace(/\*\*/g, '').replace(/`/g, '');
            lastNotificationId.value = latestMsg.id;
            
            // علامت‌گذاری به عنوان خوانده شده (فقط برای بنر)
            await fetch(`${API_BASE_URL}/api/notifications/${latestMsg.id}/read`, {
                method: 'PATCH',
                headers: { Authorization: `Bearer ${jwtToken.value}` }
            });

            setTimeout(() => { notificationMessage.value = null; }, 8000);
        }
      }
    }
  } catch (e) {
    console.error("Notification check failed", e);
  }
}

function handleNavigation(view: string) {
  activeView.value = view;
  // اگر به صفحه معامله نمی‌رویم، یعنی در صفحات دیگر هستیم
  if (view !== 'trade') {
    showTradePage.value = false; // <--- اصلاح: باید false شود تا صفحات دیگر دیده شوند
  } else {
    showTradePage.value = true;
  }
}

function onInviteCreated(message: string) {
}

function toggleTradePageView() {
  showTradePage.value = !showTradePage.value;
  if (showTradePage.value) {
     activeView.value = 'trade';
  } else {
     activeView.value = 'profile'; 
  }
}

onMounted(async () => {
  setTimeout(() => { document.body.style.backgroundColor = '#f0f2f5'; }, 100);
  if (tg) { 
    try { 
      tg.setHeaderColor('#ffffff'); 
      tg.setBackgroundColor('#f0f2f5');
    } catch (e) { console.error("Telegram API error:", e); } 
  }
  
  try {
    if (!tg || !tg.initData) throw new Error("لطفاً این برنامه را از طریق تلگرام باز کنید.");
    loadingMessage.value = 'در حال احراز هویت...';
    const loginResp = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ init_data: tg.initData }), });
    if (!loginResp.ok) throw new Error("احراز هویت اولیه ناموفق بود.");
    const loginJson = await loginResp.json();
    jwtToken.value = loginJson.access_token;
    loadingMessage.value = 'در حال دریافت اطلاعات کاربر...';
    const userResp = await fetch(`${API_BASE_URL}/api/auth/me`, { headers: { Authorization: `Bearer ${jwtToken.value}` }, });
    if (!userResp.ok) throw new Error("دریافت اطلاعات کاربر ناموفق بود.");
    user.value = await userResp.json();
    loadingMessage.value = '';
    if (user.value?.role === 'WATCH') { 
        activeView.value = 'profile'; 
        showTradePage.value = false; 
    }
    
    notificationInterval = setInterval(checkNotifications, 10000);
    
  } catch (e: any) { loadingMessage.value = `⚠️ ${e.message}`; }
});

onUnmounted(() => {
  if (notificationInterval) clearInterval(notificationInterval);
});
</script>

<template>
  <div class="app-container">
    
    <transition name="fade">
      <div v-if="notificationMessage" class="app-notification">
        <div class="notif-content">
          {{ notificationMessage }}
        </div>
        <button @click="notificationMessage = null" class="close-notif">×</button>
      </div>
    </transition>
    
    <main class="main-content">
      <div v-if="isLoading" class="loading-container">
        <div class="spinner"></div>
        <p>{{ loadingMessage }}</p>
      </div>
      
      <template v-else-if="user">
        
        <PlaceholderView v-if="showTradePage" title="معاملات" />
        
        <template v-else>
            
            <UserProfile 
              v-if="activeView === 'profile'" 
              :user="user" 
              @navigate="handleNavigation"
            />
            
            <NotificationCenter
              v-else-if="activeView === 'notifications'"
              :api-base-url="API_BASE_URL"
              :jwt-token="jwtToken"
              @navigate="handleNavigation"
            />

            <PlaceholderView v-else-if="activeView === 'settings'" title="تنظیمات" />
            
            <template v-if="user.role === 'مدیر ارشد'">
                <AdminPanel
                  v-if="activeView === 'admin_panel'"
                  @navigate="handleNavigation"
                />
                <CreateInvitationView
                  v-else-if="activeView === 'create_invitation'"
                  :api-base-url="API_BASE_URL" 
                  :jwt-token="jwtToken"
                  @invite-created="onInviteCreated"
                />
                <CommodityManager
                    v-else-if="activeView === 'manage_commodities'"
                    :api-base-url="API_BASE_URL"
                    :jwt-token="jwtToken"
                    @navigate="handleNavigation" 
                />
                <UserManager
                    v-else-if="activeView === 'manage_users'"
                    :api-base-url="API_BASE_URL"
                    :jwt-token="jwtToken"
                    @navigate="handleNavigation" 
                />
            </template>

        </template>

      </template>
    </main>

    <MainMenu 
      v-if="user && user.role !== 'WATCH'" 
      :user-role="user.role"
      :is-trade-page-visible="showTradePage" 
      @navigate="handleNavigation" 
      @toggle-trade-view="toggleTradePageView" 
    />
  </div>
</template>

<style>
/* ... استایل‌ها بدون تغییر ... */
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700&display=swap');
:root { --primary-color: #007AFF; --bg-color: #f0f2f5; --card-bg: #ffffff; --text-color: #1c1c1e; --text-secondary: #8a8a8e; --border-color: #e5e5e5; }
html { box-sizing: border-box; } *, *:before, *:after { box-sizing: inherit; } body { margin: 0; font-family: 'Vazirmatn', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: var(--bg-color); color: var(--text-color); overscroll-behavior-y: none; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }

.app-container { 
  display: flex; 
  flex-direction: column; 
  min-height: 100dvh; 
}
.main-content { 
  flex-grow: 1; 
  padding: 16px; 
  position: relative; 
  padding-bottom: 16px; 
}

.loading-container { display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100%; color: var(--text-secondary); } .spinner { width: 40px; height: 40px; border: 4px solid rgba(0, 0, 0, 0.1); border-left-color: var(--primary-color); border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px; } @keyframes spin { to { transform: rotate(360deg); } }

.app-notification {
  position: fixed;
  top: 16px;
  left: 16px;
  right: 16px;
  background-color: #333;
  color: white;
  padding: 14px 16px;
  border-radius: 12px;
  z-index: 9999;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  font-size: 14px;
  line-height: 1.6;
  direction: rtl;
  border: 1px solid #444;
}
.notif-content {
  flex-grow: 1;
  white-space: pre-line;
}
.close-notif {
  background: none;
  border: none;
  color: #bbb;
  font-size: 24px;
  line-height: 1;
  margin-right: 12px;
  cursor: pointer;
  padding: 0;
}
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.4s ease, transform 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(-20px) scale(0.95);
}
</style>