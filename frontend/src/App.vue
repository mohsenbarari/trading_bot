<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'

// Import کامپوننت‌های جدید
import HomeAnimation from './components/HomeAnimation.vue'
import BottomNav from './components/BottomNav.vue'
import UserProfile from './components/UserProfile.vue'
import CreateInvitationView from './components/CreateInvitationView.vue'
import PlaceholderView from './components/PlaceholderView.vue'

// --- تعریف متغیرهای وضعیت برنامه ---
const user = ref<any>(null) // اطلاعات کاربر
const loadingMessage = ref('در حال اتصال...') // پیام هنگام بارگذاری
const activeView = ref('home') // نمای فعلی (صفحه‌ای که کاربر می‌بیند)
const jwtToken = ref<string | null>(null) // توکن احراز هویت

// --- API and Telegram WebApp setup ---
const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp

// کامپیوپروپرتی برای نمایش پیام لودینگ
const isLoading = computed(() => !user.value && loadingMessage.value)

// --- متدها ---
// این تابع وظیفه تغییر صفحه را بر عهده دارد
function handleNavigation(view: string) {
  activeView.value = view
}

// این تابع پس از ایجاد دعوت‌نامه اجرا می‌شود
function onInviteCreated(message: string) {
  // می‌توانید کاربر را به صفحه اصلی برگردانید یا پیام موفقیت را نمایش دهید
  console.log(message);
  // برای سادگی، کاربر را به صفحه اصلی برمی‌گردانیم
  activeView.value = 'home';
  // شما می‌توانید یک پیام پاپ‌آپ زیبا اینجا نمایش دهید
  alert('دعوت‌نامه با موفقیت ایجاد شد!');
}

// --- هوک onMounted ---
// این کد فقط یک بار پس از بارگذاری اولیه برنامه اجرا می‌شود
onMounted(async () => {
  // اعمال تم روشن برای جلوگیری از صفحه تیره
  setTimeout(() => {
    document.body.style.backgroundColor = '#f0f2f5';
  }, 100);

  if (tg) {
    try {
      tg.ready();
      tg.expand();
      tg.setHeaderColor('#ffffff');
      tg.setBackgroundColor('#f0f2f5');
    } catch (e) {
      console.error("Telegram API error:", e);
    }
  }

  // فرآیند احراز هویت
  try {
    if (!tg || !tg.initData) {
      throw new Error("لطفاً این برنامه را از طریق تلگرام باز کنید.");
    }
    
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

    loadingMessage.value = ''; // پاک کردن پیام لودینگ

  } catch (e: any) {
    loadingMessage.value = `⚠️ ${e.message}`;
  }
});
</script>

<template>
  <div class="app-container">
    <!-- هدر برنامه -->
    <header class="app-header">
      <h1>Trading Bot</h1>
      <div v-if="user" class="user-welcome">
        {{ user.full_name }}
      </div>
    </header>

    <!-- محتوای اصلی برنامه -->
    <main class="main-content">
      <div v-if="isLoading" class="loading-container">
        <div class="spinner"></div>
        <p>{{ loadingMessage }}</p>
      </div>
      <template v-else>
        <!-- نمایش کامپوننت‌ها بر اساس نمای فعال -->
        <HomeAnimation v-if="activeView === 'home'" />
        <UserProfile v-if="activeView === 'profile'" :user="user" />
        <CreateInvitationView 
          v-if="activeView === 'create_invitation'" 
          :api-base-url="API_BASE_URL" 
          :jwt-token="jwtToken"
          @invite-created="onInviteCreated"
        />
        <!-- کامپوننت‌های موقت برای سایر گزینه‌ها -->
        <PlaceholderView v-if="activeView === 'view_my_trades'" title="معاملات من" />
        <PlaceholderView v-if="activeView === 'create_trade_offer'" title="ثبت پیشنهاد جدید" />
        <PlaceholderView v-if="activeView === 'manage_users'" title="مدیریت کاربران" />
      </template>
    </main>

    <!-- نوار ناوبری پایین صفحه -->
    <BottomNav v-if="user" :user-role="user.role" @navigate="handleNavigation" />
  </div>
</template>

<style>
/* استایل‌های کلی و اصلی برنامه */
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700&display=swap');

:root {
  --primary-color: #007AFF;
  --bg-color: #f0f2f5;
  --card-bg: #ffffff;
  --text-color: #1c1c1e;
  --text-secondary: #8a8a8e;
  --border-color: #e5e5e5;
}

html {
  box-sizing: border-box;
}

*, *:before, *:after {
  box-sizing: inherit;
}

body {
  margin: 0;
  font-family: 'Vazirmatn', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  background-color: var(--bg-color);
  color: var(--text-color);
  overscroll-behavior-y: none;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.app-container {
  display: flex;
  flex-direction: column;
  height: 100vh;
  padding: 0;
  overflow: hidden;
}

.app-header {
  background-color: var(--card-bg);
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-shrink: 0;
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}

.app-header h1 {
  font-size: 18px;
  font-weight: 700;
  margin: 0;
  color: var(--primary-color);
}

.user-welcome {
    font-size: 14px;
    font-weight: 500;
    color: var(--text-secondary);
}

.main-content {
  flex-grow: 1;
  overflow-y: auto;
  padding: 16px;
  position: relative;
}

/* استایل برای لودینگ */
.loading-container {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  height: 100%;
  color: var(--text-secondary);
}

.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid rgba(0, 0, 0, 0.1);
  border-left-color: var(--primary-color);
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 16px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
