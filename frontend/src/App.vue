<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'

// کامپوننت BottomNav حذف و MainMenu اضافه شد
import MainMenu from './components/MainMenu.vue'
import UserProfile from './components/UserProfile.vue'
import CreateInvitationView from './components/CreateInvitationView.vue'
import PlaceholderView from './components/PlaceholderView.vue'

const user = ref<any>(null)
const loadingMessage = ref('در حال اتصال...')
const activeView = ref('trade') 
const jwtToken = ref<string | null>(null)
const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp
const isLoading = computed(() => !user.value && loadingMessage.value)

function handleNavigation(view: string) {
  activeView.value = view
}

function onInviteCreated(message: string) {
  activeView.value = 'trade'; // بازگشت به صفحه معامله
  alert('دعوت‌نامه با موفقیت ایجاد شد!');
}

onMounted(async () => {
  // کدهای onMounted بدون تغییر باقی می‌مانند
  setTimeout(() => { document.body.style.backgroundColor = '#f0f2f5'; }, 100);
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      tg.setHeaderColor('#ffffff');
      tg.setBackgroundColor('#f0f2f5');
    } catch (e) { console.error("Telegram API error:", e); }
  }
  try {
    if (!tg || !tg.initData) throw new Error("لطفاً این برنامه را از طریق تلگرام باز کنید.");
    loadingMessage.value = 'در حال احراز هویت...';
    const loginResp = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ init_data: tg.initData }),
    });
    if (!loginResp.ok) throw new Error("احراز هویت اولیه ناموفق بود.");
    const loginJson = await loginResp.json();
    jwtToken.value = loginJson.access_token;
    loadingMessage.value = 'در حال دریافت اطلاعات کاربر...';
    const userResp = await fetch(`${API_BASE_URL}/api/auth/me`, {
      headers: { Authorization: `Bearer ${jwtToken.value}` },
    });
    if (!userResp.ok) throw new Error("دریافت اطلاعات کاربر ناموفق بود.");
    user.value = await userResp.json();
    loadingMessage.value = '';
  } catch (e: any) {
    loadingMessage.value = `⚠️ ${e.message}`;
  }
});
</script>

<template>
  <div class="app-container">
    <header class="app-header">
      <h1>Trading Bot</h1>
      <div v-if="user" class="user-welcome">
        {{ user.full_name }}
      </div>
    </header>

    <!-- منوی اصلی جدید جایگزین نوار پایین شد -->
    <MainMenu 
      v-if="user && user.role !== 'WATCH'" 
      :user-role="user.role"
      @navigate="handleNavigation" 
    />

    <main class="main-content">
      <div v-if="isLoading" class="loading-container">
        <div class="spinner"></div>
        <p>{{ loadingMessage }}</p>
      </div>
      <template v-else-if="user">
        <!-- مدیریت نمایش صفحات -->
        <PlaceholderView v-if="activeView === 'trade'" title="معاملات" />
        <UserProfile v-else-if="activeView === 'profile'" :user="user" />
        <PlaceholderView v-else-if="activeView === 'settings'" title="تنظیمات" />
        <CreateInvitationView 
          v-else-if="activeView === 'create_invitation'" 
          :api-base-url="API_BASE_URL" 
          :jwt-token="jwtToken"
          @invite-created="onInviteCreated"
        />
        <!-- اگر هیچ گزینه‌ای انتخاب نشده بود، صفحه معاملات را نشان بده -->
        <PlaceholderView v-else title="معاملات" />
      </template>
    </main>

  </div>
</template>

<style>
/* استایل‌های کلی با اصلاح جزئی */
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700&display=swap');
:root { --primary-color: #007AFF; --bg-color: #f0f2f5; --card-bg: #ffffff; --text-color: #1c1c1e; --text-secondary: #8a8a8e; --border-color: #e5e5e5; }
html { box-sizing: border-box; }
*, *:before, *:after { box-sizing: inherit; }
body { margin: 0; font-family: 'Vazirmatn', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: var(--bg-color); color: var(--text-color); overscroll-behavior-y: none; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }

.app-container { 
  display: flex; 
  flex-direction: column; 
  height: 100vh; 
  max-height: 100dvh; /* برای سازگاری با موبایل */
  overflow: hidden; 
}

.app-header { 
  background-color: var(--card-bg); 
  padding: 12px 16px; 
  border-bottom: 1px solid var(--border-color); 
  flex-shrink: 0; 
  box-shadow: 0 1px 3px rgba(0,0,0,0.02); 
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.app-header h1 { font-size: 18px; font-weight: 700; margin: 0; color: var(--primary-color); }
.user-welcome { font-size: 14px; font-weight: 500; color: var(--text-secondary); }

.main-content {
  flex-grow: 1; /* باعث می‌شود این بخش باقی‌مانده فضا را پر کند */
  overflow-y: auto; /* فقط این بخش اسکرول می‌خورد */
  padding: 16px;
  position: relative;
}

.loading-container { display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100%; color: var(--text-secondary); }
.spinner { width: 40px; height: 40px; border: 4px solid rgba(0, 0, 0, 0.1); border-left-color: var(--primary-color); border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
