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

interface Notification {
  id: number;
  message: string;
  is_read: boolean;
  created_at: string;
}

const user = ref<any>(null)
const loadingMessage = ref('در حال اتصال...')
const activeView = ref('trade') // منبع حقیقت واحد
const jwtToken = ref<string | null>(null)
const API_BASE_URL = 'https://telegram.362514.ir'
const tg = (window as any).Telegram?.WebApp

// 'showTradePage' حالا یک متغیر محاسباتی است
const showTradePage = computed(() => activeView.value === 'trade');
const isLoading = computed(() => !user.value && loadingMessage.value)

// --- نوتیفیکیشن ---
const notificationMessage = ref<string | null>(null);
const shownBannerIds = ref(new Set<number>());
const unreadCount = ref(0); // تعداد خوانده نشده‌ها برای بج
let notificationInterval: any = null;

// --- پاپ‌اور نوتیفیکیشن ---
const isPopoverOpen = ref(false);
const popoverNotifications = ref<any[]>([]); // لیست پیام‌های داخل پاپ‌اور

// عنوان داینامیک صفحه
const computePageTitle = computed(() => {
  switch (activeView.value) {
    case 'trade': return 'معاملات';
    case 'profile': return 'پنل کاربری';
    case 'notifications': return 'صندوق پیام‌ها';
    case 'settings': return 'تنظیمات';
    case 'admin_panel': return 'پنل مدیریت';
    case 'create_invitation': return 'ایجاد دعوت‌نامه';
    case 'manage_commodities': return 'مدیریت کالاها';
    case 'manage_users': return 'مدیریت کاربران';
    default: return 'Trading Bot';
  }
});

async function checkNotifications() {
  if (!jwtToken.value) return;
  
  if (activeView.value === 'notifications') {
    unreadCount.value = 0;
    popoverNotifications.value = []; 
    return;
  }

  try {
    const res = await fetch(`${API_BASE_URL}/api/notifications/unread`, {
      headers: { Authorization: `Bearer ${jwtToken.value}` }
    });
    if (res.ok) {
      const data = await res.json(); // مثلا: [notifB(101), notifA(100)]
      unreadCount.value = data.length; 
      popoverNotifications.value = data; 
      
      // --- 👇 منطق نمایش بنر را با این جایگزین کنید 👇 ---

      // اگر بنری در حال حاضر فعال است، بنر جدیدی نشان نده (صبر کن تا محو شود)
      if (notificationMessage.value !== null) {
        return; 
      }

      // پیدا کردن اولین پیام خوانده‌نشده که *هنوز در بنر نشان داده نشده*
      const messageToShow = data.find((notif: Notification) => !shownBannerIds.value.has(notif.id));

      if (messageToShow) {
        // ما یک پیام جدید برای نمایش پیدا کردیم
        notificationMessage.value = messageToShow.message.replace(/\*\*/g, '').replace(/`/g, '');
        shownBannerIds.value.add(messageToShow.id); // این ID را به "نشان داده شده" اضافه کن
        
        setTimeout(() => { 
          notificationMessage.value = null; 
        }, 8000);
      }
      
      // --- 👆 پایان بخش جایگزین 👆 ---
    }
  } catch (e) {
    console.error("Notification check failed", e);
  }
}

function handleNavigation(view: string) {
  isPopoverOpen.value = false; // در هر ناوبری (رفتن به صفحه جدید)، پاپ‌اور را ببند
  activeView.value = view;
  
  // وقتی کاربر *واقعا* وارد صندوق پیام می‌شود، تعداد را صفر می‌کنیم
  if (view === 'notifications') {
    unreadCount.value = 0;
    shownBannerIds.value.clear(); // بنر را هم ریست کن
    // TODO: در onMounted کامپوننت NotificationCenter.vue یک درخواست "mark-all-read" به بک‌اند بزنید
  }
}

// این فانکشن برای دکمه‌های منوی پایین (MainMenu) استفاده می‌شود
function toggleTradePageView() {
  // اگر در صفحه معامله هستیم، به پروفایل برو
  if (activeView.value === 'trade') {
    activeView.value = 'profile';
  } else {
    // اگر در هر صفحه دیگری هستیم (پروفایل، تنظیمات، نوتیفیکیشن)، به معامله برگرد
    activeView.value = 'trade';
  }
}

// این فانکشن برای کامپوننت CreateInvitationView استفاده می‌شود
function onInviteCreated(message: string) {
  // TODO: می‌توانید اینجا یک بنر موقت (شبیه نوتیفیکیشن) برای ادمین نشان دهید
  // notificationMessage.value = message;
  // setTimeout(() => { notificationMessage.value = null; }, 5000);
}

// فانکشن برای باز/بسته کردن پاپ‌اور زنگوله
function togglePopover() {
  isPopoverOpen.value = !isPopoverOpen.value;
  // اگر پاپ‌اور باز می‌شود، لیست را یکبار رفرش می‌کنیم
  if (isPopoverOpen.value) {
    checkNotifications();
  }
}

// فانکشن کمکی برای خلاصه‌سازی متن پیام در پاپ‌اور
function truncateMessage(message: string, length = 50) {
  // فرمت‌بندی را حذف می‌کند
  const cleanMessage = message.replace(/\*\*(.*?)\*\*/g, '$1').replace(/`/g, '').replace(/\n/g, ' ');
  if (cleanMessage.length <= length) return cleanMessage;
  return cleanMessage.substring(0, length) + '...';
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
    }
    
    notificationInterval = setInterval(checkNotifications, 10000); // چک کردن دوره‌ای نوتیفیکیشن‌ها
    checkNotifications(); // چک کردن در لحظه اول لود شدن
    
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
    
    <header class="app-header" v-if="user">
      <div class="header-content">
        
        <button class="notification-bell-btn" @click="togglePopover">
          🔔
          <span v-if="unreadCount > 0" class="notification-badge">
            {{ unreadCount > 9 ? '9+' : unreadCount }}
          </span>
        </button>
        
        <span class="header-title">{{ computePageTitle }}</span>
      </div>
    </header>

    <div v-if="isPopoverOpen" class="popover-backdrop" @click="togglePopover"></div>

    <transition name="popover-fade">
      <div v-if="isPopoverOpen" class="notification-popover">
        <div class="popover-header">
          <span>پیام‌ها</span>
        </div>
        
        <div class="popover-list">
          <div v-if="popoverNotifications.length === 0" class="popover-empty">
            پیام جدیدی ندارید.
          </div>
          
          <div v-else>
            <div 
              v-for="notif in popoverNotifications.slice(0, 5)" 
              :key="notif.id" 
              class="popover-item"
              @click="handleNavigation('notifications')"
            >
              <span class="popover-item-text">{{ truncateMessage(notif.message) }}</span>
              <span class="popover-item-date">{{ new Date(notif.created_at).toLocaleTimeString('fa-IR', {hour: '2-digit', minute:'2-digit'}) }}</span>
            </div>
          </div>
        </div>

        <div class="popover-footer">
          <button @click="handleNavigation('notifications')">
            مشاهده همه پیام‌ها
          </button>
        </div>
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

          <PlaceholderView
            v-else-if="activeView === 'settings'"
            title="تنظیمات"
          />

          <AdminPanel
            v-else-if="activeView === 'admin_panel' && user.role === 'مدیر ارشد'"
            @navigate="handleNavigation"
          />

          <CreateInvitationView
            v-else-if="activeView === 'create_invitation' && user.role === 'مدیر ارشد'"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
            @invite-created="onInviteCreated"
          />

          <CommodityManager
            v-else-if="activeView === 'manage_commodities' && user.role === 'مدیر ارشد'"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
            @navigate="handleNavigation"
          />

          <UserManager
            v-else-if="activeView === 'manage_users' && user.role === 'مدیر ارشد'"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
            @navigate="handleNavigation"
          />

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
@import url('https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700&display=swap');
:root { 
  --primary-color: #007AFF; 
  --bg-color: #f0f2f5; 
  --card-bg: #ffffff; 
  --text-color: #1c1c1e; 
  --text-secondary: #8a8a8e; 
  --border-color: #e5e5e5; 
}
html { box-sizing: border-box; } 
*, *:before, *:after { box-sizing: inherit; } 
body { 
  margin: 0; 
  font-family: 'Vazirmatn', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
  background-color: var(--bg-color); 
  color: var(--text-color); 
  overscroll-behavior-y: none; 
  -webkit-font-smoothing: antialiased; 
  -moz-osx-font-smoothing: grayscale; 
  direction: rtl; /* تنظیم جهت کل برنامه به راست-به-چپ */
}

.app-container { 
  display: flex; 
  flex-direction: column; 
  min-height: 100dvh; 
  position: relative; /* برای موقعیت‌دهی پاپ‌اور */
}
.main-content { 
  flex-grow: 1; 
  padding: 16px; 
  position: relative; 
  /* پدینگ بالا برای هدر ثابت در نظر گرفته می‌شود */
  /* (ارتفاع هدر حدود 57 پیکسل است) */
  padding-top: 73px; /* 57 + 16 */
  padding-bottom: 100px; /* فضای کافی برای منوی پایین */
}

.loading-container { display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100%; color: var(--text-secondary); padding-top: 73px; } 
.spinner { width: 40px; height: 40px; border: 4px solid rgba(0, 0, 0, 0.1); border-left-color: var(--primary-color); border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px; } 
@keyframes spin { to { transform: rotate(360deg); } }

/* بنر نوتیفیکیشن موقت */
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
  margin-right: 12px; /* تغییر به راست */
  margin-left: 0; /* حذف مارجین چپ */
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

/* هدر ثابت */
.app-header {
  position: fixed; /* ثابت در بالای صفحه */
  top: 0;
  left: 0;
  right: 0;
  background-color: var(--card-bg, #ffffff);
  border-bottom: 1px solid var(--border-color, #e5e5e5);
  padding: 5px 16px;
  z-index: 10; /* پایین‌تر از پاپ‌اور و نوتیفیکیشن */
  /* padding-top: calc(12px + env(safe-area-inset-top)); */ /* برای آیفون X */
}

.header-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 32px; 
}

.header-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-color);
  text-align: right; /* اطمینان از تراز راست */
}

/* دکمه زنگوله */
.notification-bell-btn {
  position: relative;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 22px; 
  padding: 0; 
  width: 32px; 
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-secondary);
  transition: all 0.2s;
  line-height: 1; 
  border-radius: 50%; 
}
.notification-bell-btn:hover {
  background-color: #f0f0f0; 
  color: var(--text-color);
}

/* بج عددی روی زنگوله */
.notification-badge {
  position: absolute;
  top: 0;
  right: 0;
  background-color: #f44336; 
  color: white;
  border-radius: 50%;
  width: 18px;
  height: 18px;
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
  border: 2px solid var(--card-bg, #ffffff); 
  transform: translate(15%, -15%);
}

/* استایل‌های پاپ‌اور */
.popover-backdrop {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.1);
  z-index: 100;
  backdrop-filter: blur(2px);
}

.notification-popover {
  position: absolute;
  /* (57px ارتفاع هدر) + 8px فاصله = 65px */
  top: 65px; 
  left: 16px; 
  width: 320px; 
  max-width: calc(100% - 32px); 
  background: var(--card-bg, #ffffff);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  z-index: 101; 
  display: flex;
  flex-direction: column;
  overflow: hidden; 
}

.popover-header {
  padding: 12px 16px;
  font-weight: 700;
  font-size: 16px;
  border-bottom: 1px solid var(--border-color, #e5e5e5);
  text-align: right;
}

.popover-list {
  max-height: 300px; 
  overflow-y: auto;
}

.popover-empty, .popover-loading {
  padding: 24px;
  text-align: center;
  color: var(--text-secondary);
}

.popover-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #f0f0f0;
  cursor: pointer;
  transition: background-color 0.15s;
  text-align: right;
}
.popover-item:hover {
  background-color: #f9f9f9;
}
.popover-item:last-child {
  border-bottom: none;
}

.popover-item-text {
  font-size: 14px;
  line-height: 1.5;
  color: var(--text-color);
  flex-grow: 1;
}

.popover-item-date {
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0; 
  direction: ltr; /* برای نمایش صحیح ساعت */
  text-align: left;
}

.popover-footer {
  padding: 8px;
  background-color: #f9f9f9;
  border-top: 1px solid var(--border-color, #e5e5e5);
}
.popover-footer button {
  width: 100%;
  padding: 10px;
  border: none;
  background: transparent;
  color: var(--primary-color, #007AFF);
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  border-radius: 8px;
  transition: background-color 0.15s;
  font-family: 'Vazirmatn', sans-serif; /* اطمینان از فونت */
}
.popover-footer button:hover {
  background-color: #eef;
}

/* انیمیشن پاپ‌اور */
.popover-fade-enter-active,
.popover-fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.popover-fade-enter-from,
.popover-fade-leave-to {
  opacity: 0;
  transform: translateY(-10px) scale(0.95);
}

</style>