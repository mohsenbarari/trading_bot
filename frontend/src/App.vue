<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'

import MainMenu from './components/MainMenu.vue'
import UserProfile from './components/UserProfile.vue'
import AdminPanel from './components/AdminPanel.vue'
// --- کامپوننت جدید را import کنید ---
import CommodityManager from './components/CommodityManager.vue' 
import PlaceholderView from './components/PlaceholderView.vue'

const user = ref<any>(null)
const loadingMessage = ref('در حال اتصال...')
const activeView = ref('trade') 
const jwtToken = ref<string | null>(null)
const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp
const isLoading = computed(() => !user.value && loadingMessage.value)
const showTradePage = ref(true) 

function handleNavigation(view: string) {
  if (view !== 'trade') {
    showTradePage.value = true; 
  }
  // اگر از پنل ادمین به ساخت دعوت رفتیم، view را به پنل ادمین برگردان
  // (چون ساخت دعوت حالا داخل پنل ادمین است)
  if (view === 'create_invitation') {
      activeView.value = 'admin_panel';
      // Optionally show a confirmation for creation within the admin panel itself
  } else {
      activeView.value = view;
  }
}

function onInviteCreated(message: string) {
  alert('دعوت‌نامه با موفقیت ایجاد شد!');
}

function toggleTradePageView() {
  showTradePage.value = !showTradePage.value;
  if (!showTradePage.value && activeView.value === 'trade') {
    activeView.value = 'profile'; 
  }
  else if (showTradePage.value) {
     activeView.value = 'trade';
  }
}

onMounted(async () => {
  setTimeout(() => { document.body.style.backgroundColor = '#f0f2f5'; }, 100);
  // ... (بقیه کد onMounted بدون تغییر) ...
  if (tg) { try { tg.ready(); tg.expand(); tg.setHeaderColor('#ffffff'); tg.setBackgroundColor('#f0f2f5'); } catch (e) { console.error("Telegram API error:", e); } }
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
    if (user.value?.role === 'WATCH') { activeView.value = 'profile'; showTradePage.value = false; }
  } catch (e: any) { loadingMessage.value = `⚠️ ${e.message}`; }
});
</script>

<template>
  <div class="app-container">
    
    <main class="main-content">
      <div v-if="isLoading" class="loading-container">
        <div class="spinner"></div>
        <p>{{ loadingMessage }}</p>
      </div>
      <template v-else-if="user">
        
        <PlaceholderView v-if="activeView === 'trade' && showTradePage" title="معاملات" />
        
        <UserProfile 
          v-else-if="activeView === 'profile'" 
          :user="user" 
          @navigate="handleNavigation"
        />
        
        <PlaceholderView v-else-if="activeView === 'settings'" title="تنظیمات" />
        
        <AdminPanel
          v-else-if="activeView === 'admin_panel' && user.role === 'SUPER_ADMIN'"
          :api-base-url="API_BASE_URL" 
          :jwt-token="jwtToken"
          @invite-created="onInviteCreated"
          @navigate="handleNavigation"
        />

        <CommodityManager
            v-else-if="activeView === 'manage_commodities' && user.role === 'SUPER_ADMIN'"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
        />
        <UserProfile 
          v-else-if="!showTradePage || activeView === 'profile'" 
          :user="user" 
          @navigate="handleNavigation" 
        />

        <PlaceholderView v-else title="صفحه اصلی" />

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
/* ... (استایل‌های کلی شما بدون تغییر) ... */
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700&display=swap');
:root { --primary-color: #007AFF; --bg-color: #f0f2f5; --card-bg: #ffffff; --text-color: #1c1c1e; --text-secondary: #8a8a8e; --border-color: #e5e5e5; }
html { box-sizing: border-box; } *, *:before, *:after { box-sizing: inherit; } body { margin: 0; font-family: 'Vazirmatn', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: var(--bg-color); color: var(--text-color); overscroll-behavior-y: none; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
.app-container { display: flex; flex-direction: column; height: 100vh; max-height: 100dvh; overflow: hidden; }
.main-content { flex-grow: 1; overflow-y: auto; padding: 16px; position: relative; padding-bottom: 80px; }
.loading-container { display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100%; color: var(--text-secondary); } .spinner { width: 40px; height: 40px; border: 4px solid rgba(0, 0, 0, 0.1); border-left-color: var(--primary-color); border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px; } @keyframes spin { to { transform: rotate(360deg); } }
</style>