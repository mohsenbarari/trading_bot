<script setup lang="ts">
import { ref, onMounted, computed, onUnmounted } from 'vue'

import MainMenu from './components/MainMenu.vue'
import HomePage from './components/HomePage.vue'
import UserProfile from './components/UserProfile.vue'
import AdminPanel from './components/AdminPanel.vue'
import CommodityManager from './components/CommodityManager.vue'
import UserManager from './components/UserManager.vue'
import CreateInvitationView from './components/CreateInvitationView.vue'
import PlaceholderView from './components/PlaceholderView.vue'
import NotificationCenter from './components/NotificationCenter.vue'
import TradingSettings from './components/TradingSettings.vue'
import TradingView from './components/TradingView.vue'
import UserSettings from './components/UserSettings.vue'
import PublicProfile from './components/PublicProfile.vue'

interface Notification {
  id: number;
  message: string;
  is_read: boolean;
  created_at: string;
  level?: string;
  category?: string;
}

const user = ref<any>(null)
const loadingMessage = ref('در حال اتصال...')
const activeView = ref('home')
const jwtToken = ref<string | null>(null)
// Use relative path for API calls since frontend is served by the same backend
const API_BASE_URL = '';
const tg = (window as any).Telegram?.WebApp

const showTradePage = computed(() => activeView.value === 'trade');
const isLoading = computed(() => !user.value && loadingMessage.value)

// --- نوتیفیکیشن ---
const bannerTitle = ref<string>('');
const bannerBody = ref<string>('');
const notificationLevel = ref<string>('info');
const notificationCategory = ref<string>('system');

// نگهداری آبجکت کامل نوتیفیکیشنِ در حال نمایش (برای کلیک روی بنر)
const currentBannerNotification = ref<Notification | null>(null);

// نگهداری نوتیفیکیشنی که کاربر برای خواندن انتخاب کرده (مودال)
const selectedNotification = ref<Notification | null>(null);

const shownBannerIds = ref(new Set<number>());
const unreadCount = ref(0);
const bannerQueue = ref<Notification[]>([]);
const isBannerActive = ref(false);

// برای کنترل SSE (قطع اتصال هنگام خروج)
let sseController: AbortController | null = null;

// --- متغیرهای Swipe ---
const bannerRef = ref<HTMLElement | null>(null);
const startX = ref(0);
const currentX = ref(0);
const isSwiping = ref(false);
const swipeThreshold = 100;

// --- پاپ‌اور ---
const isPopoverOpen = ref(false);
const popoverNotifications = ref<any[]>([]);

// --- لاگین (OTP) ---
const loginStep = ref<'none' | 'mobile' | 'otp'>('none');
const loginMobile = ref('');
const loginCode = ref('');
const loginError = ref('');
const isLoginLoading = ref(false);

async function handleRequestOtp() {
    if (!loginMobile.value) {
        loginError.value = 'لطفاً شماره موبایل را وارد کنید.';
        return;
    }
    // Normalize logic could be added here if needed, but backend handles it too
    
    isLoginLoading.value = true;
    loginError.value = '';
    
    try {
        const res = await fetch(`${API_BASE_URL}/api/auth/request-otp`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ mobile_number: loginMobile.value })
        });
        
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'خطا در ارسال کد');
        }
        
        // Success
        loginStep.value = 'otp';
        loadingMessage.value = ''; // Clear loading message to show the UI
    } catch (e: any) {
        loginError.value = e.message;
    } finally {
        isLoginLoading.value = false;
    }
}

async function tryRefreshToken(): Promise<boolean> {
    const refreshToken = localStorage.getItem('refresh_token');
    if (!refreshToken) return false;
    
    try {
        const res = await fetch(`${API_BASE_URL}/api/auth/refresh`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ refresh_token: refreshToken })
        });
        
        if (!res.ok) return false;
        
        const data = await res.json();
        jwtToken.value = data.access_token;
        localStorage.setItem('auth_token', data.access_token);
        localStorage.setItem('refresh_token', data.refresh_token);
        
        // Retry loading user with new token
        await loadUser();
        return true;
    } catch (e) {
        console.warn("Refresh token failed:", e);
        return false;
    }
}

async function handleVerifyOtp() {
    if (!loginCode.value) {
        loginError.value = 'لطفاً کد تایید را وارد کنید.';
        return;
    }
    
    isLoginLoading.value = true;
    loginError.value = '';
    
    try {
        const res = await fetch(`${API_BASE_URL}/api/auth/verify-otp`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ mobile_number: loginMobile.value, otp_code: String(loginCode.value) }) // Corrected field name + Ensure string type
        });
        
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'کد نادرست است');
        }
        
        const data = await res.json();
        jwtToken.value = data.access_token;
        localStorage.setItem('auth_token', data.access_token);
        localStorage.setItem('refresh_token', data.refresh_token); // Store refresh token for 30-day persistence
        
        // Proceed to load user
        await loadUser();
        
        loginStep.value = 'none'; // Exit login mode
    } catch (e: any) {
        loginError.value = e.message;
    } finally {
        isLoginLoading.value = false;
    }
}

async function loadUser() {
    loadingMessage.value = 'در حال دریافت اطلاعات کاربر...';
    try {
        const userResp = await fetch(`${API_BASE_URL}/api/auth/me`, { headers: { Authorization: `Bearer ${jwtToken.value}` }, });
        if (!userResp.ok) throw new Error("Token expired or invalid");
        user.value = await userResp.json();
        loadingMessage.value = '';
        
        if (user.value?.role === 'WATCH') activeView.value = 'profile'; 
        
        await checkNotifications();
        startSSE();
    } catch (e: any) {
        // If load user fails, try to refresh token first
        console.warn("Auth check failed:", e);
        const refreshed = await tryRefreshToken();
        if (!refreshed) {
            // Clear everything and show login
            jwtToken.value = null;
            user.value = null;
            localStorage.removeItem('auth_token');
            localStorage.removeItem('refresh_token');
            loadingMessage.value = '';
            if (!tg?.initData) {
                 loginStep.value = 'mobile';
            }
        }
    }
}

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
    case 'user_profile': return 'پروفایل کاربر';
    default: return 'Trading Bot';
  }
});

function getIcon(level: string, category: string) {
  if (category === 'system') return '🛡️';
  if (level === 'success') return '✅';
  if (level === 'warning') return '⚠️';
  if (level === 'error') return '⛔';
  return '📌';
}

function parseNotificationMessage(rawMessage: string) {
  const cleanText = rawMessage.replace(/\*\*/g, '').replace(/`/g, '');
  const lines = cleanText.split('\n').map(l => l.trim()).filter(l => l);
  let title = lines[0] || 'پیام جدید';
  let body = '';
  if (lines.length > 1) {
    const userLine = lines.find(l => l.includes('نام کاربری:'));
    const secondLine = lines[1] || ''; 
    body = userLine ? userLine : secondLine.substring(0, 40) + (secondLine.length > 40 ? '...' : '');
  } else {
    body = cleanText.substring(0, 50);
  }
  return { title, body };
}

// --- 👇 تابع باز کردن و خواندن پیام (بدون نویگیشن) 👇 ---
async function openNotificationDetails(notif: Notification) {
  // 1. باز کردن مودال
  selectedNotification.value = notif;
  isPopoverOpen.value = false; // بستن پاپ‌اور زنگوله
  
  // اگر بنر باز است، آن را ببندیم تا مزاحم مودال نباشد
  if (isBannerActive.value) {
      closeNotificationBanner();
  }

  // 2. اگر پیام خوانده نشده است، آن را خوانده شده کن
  if (!notif.is_read) {
    // آپدیت لوکال (برای سرعت UI)
    notif.is_read = true;
    if (unreadCount.value > 0) unreadCount.value--;
    
    // آپدیت سرور
    try {
      await fetch(`${API_BASE_URL}/api/notifications/${notif.id}/read`, {
         method: 'PATCH',
         headers: { Authorization: `Bearer ${jwtToken.value}` }
      });
    } catch (e) {
      console.error("Failed to mark as read", e);
    }
  }
}

function closeNotificationModal() {
  selectedNotification.value = null;
}
// -------------------------------------------------------

const onTouchStart = (e: TouchEvent) => {
  const firstTouch = e.touches[0];
  if (firstTouch) {
    startX.value = firstTouch.clientX;
    isSwiping.value = true;
  }
};

const onTouchMove = (e: TouchEvent) => {
  if (!isSwiping.value) return;
  const firstTouch = e.touches[0];
  if (firstTouch) {
    currentX.value = firstTouch.clientX - startX.value;
  }
};

const onTouchEnd = () => {
  if (!isSwiping.value) return;
  isSwiping.value = false;
  
  if (Math.abs(currentX.value) > swipeThreshold) {
    const endX = currentX.value > 0 ? window.innerWidth : -window.innerWidth;
    if (bannerRef.value) {
        bannerRef.value.style.transition = 'transform 0.3s ease-out';
        bannerRef.value.style.transform = `translateX(${endX}px)`;
    }
    setTimeout(() => {
      closeNotificationBanner();
      setTimeout(() => {
          if (bannerRef.value) {
            bannerRef.value.style.transition = '';
            bannerRef.value.style.transform = '';
          }
          currentX.value = 0;
      }, 300);
    }, 300);
    
  } else {
    currentX.value = 0;
  }
};

const bannerStyle = computed(() => {
  if (isSwiping.value) {
    return {
      transform: `translateX(${currentX.value}px)`,
      transition: 'none', 
      opacity: `${1 - Math.abs(currentX.value) / (window.innerWidth * 0.8)}`
    };
  }
  return {
      transform: `translateX(${currentX.value}px)`,
      transition: 'transform 0.3s cubic-bezier(0.25, 0.8, 0.5, 1), opacity 0.3s ease'
  };
});

const enqueueBanners = (messages: Notification[]) => {
  let added = false;
  messages.sort((a, b) => a.id - b.id);
  for (const msg of messages) {
    const alreadyInQueue = bannerQueue.value.some(q => q.id === msg.id);
    if (!alreadyInQueue) {
      bannerQueue.value.push(msg);
      shownBannerIds.value.add(msg.id);
      added = true;
    }
  }
  if (added) processQueue();
};

const processQueue = () => {
  if (isBannerActive.value || bannerQueue.value.length === 0) return;
  const nextMsg = bannerQueue.value.shift();
  if (!nextMsg) return;
  
  // ذخیره برای استفاده در کلیک
  currentBannerNotification.value = nextMsg;

  currentX.value = 0;
  if (bannerRef.value) {
      bannerRef.value.style.transform = '';
      bannerRef.value.style.transition = '';
  }

  isBannerActive.value = true;
  const parsed = parseNotificationMessage(nextMsg.message);
  bannerTitle.value = parsed.title;
  bannerBody.value = parsed.body;
  notificationLevel.value = (nextMsg.level || 'info').toLowerCase();
  notificationCategory.value = (nextMsg.category || 'system').toLowerCase();
  setTimeout(() => { closeNotificationBanner(); }, 5000);
};

function closeNotificationBanner() {
  if (isSwiping.value && Math.abs(currentX.value) > swipeThreshold) return;

  isBannerActive.value = false;
  setTimeout(() => { processQueue(); }, 500); 
}

// --- 👇 سیستم دریافت نوتیفیکیشن (اصلاح شده برای SSE) 👇 ---

// 1. دریافت وضعیت اولیه (هنوز لازم است تا لیست پر شود)
async function checkNotifications() {
  if (!jwtToken.value) return;
  if (activeView.value === 'notifications') {
    unreadCount.value = 0;
    popoverNotifications.value = []; 
    return;
  }
  try {
    const countRes = await fetch(`${API_BASE_URL}/api/notifications/unread-count`, { headers: { Authorization: `Bearer ${jwtToken.value}` } });
    if (countRes.ok) {
      const serverCount = await countRes.json();
      unreadCount.value = serverCount;
      const listRes = await fetch(`${API_BASE_URL}/api/notifications/unread`, { headers: { Authorization: `Bearer ${jwtToken.value}` } });
      if (listRes.ok) {
          const data: Notification[] = await listRes.json();
          popoverNotifications.value = data; 
          // بنر برای پیام‌های قدیمی نشان نمی‌دهیم، فقط در حافظه نگه می‌داریم
          data.forEach(n => shownBannerIds.value.add(n.id));
      }
    }
  } catch (e) { console.error("Initial check failed", e); }
}

// 2. اتصال زنده و امن به SSE (بدون نیاز به کتابخانه اضافی)
// 2. اتصال زنده و امن به SSE (بدون نیاز به کتابخانه اضافی)

const isTradeBannerActive = ref(false);
const tradeBannerData = ref<any>(null);

async function startSSE() {
  if (!jwtToken.value) return;
  
  if (sseController) sseController.abort();
  sseController = new AbortController();
  
  try {
    const response = await fetch(`${API_BASE_URL}/api/notifications/stream`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${jwtToken.value}`,
        'Accept': 'text/event-stream',
      },
      signal: sseController.signal,
    });

    if (!response.ok) throw new Error(response.statusText);

    const reader = response.body?.getReader();
    const decoder = new TextDecoder("utf-8");
    if (!reader) return;

    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      
      const parts = buffer.split('\n\n');
      buffer = parts.pop() || '';

      for (const part of parts) {
        // Simple parser for event and data
        let eventType = 'message';
        let dataStr = '';

        const lines = part.split('\n');
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            eventType = line.substring(7).trim();
          } else if (line.startsWith('data: ')) {
            dataStr = line.substring(6).trim();
          }
        }

        if (dataStr) {
           try {
             const data = JSON.parse(dataStr);
             
             if (eventType === 'trade:created') {
                handleTradeEvent(data);
             } else if (eventType === 'message' || !eventType) {
                // Standard notification
                const newNotif: Notification = data;
                popoverNotifications.value.unshift(newNotif);
                unreadCount.value++;
                // Do NOT show banner for standard notifications if trade banner is high priority? 
                // Or just standard enqueue.
                // NOTE: Trade notifications also come as "message" from backend (via create_user_notification)
                // We should prevent double banners if possible.
                // Ideally, 'trade:created' event is for the special UI, 'message' is for history/badge.
                
                // If the message category is 'trade', maybe skip banner if we have the special banner?
                // Let's rely on eventType 'trade:created' for the special banner.
                // Standard notifications still go to the queue (top banner).
                if (!shownBannerIds.value.has(newNotif.id)) {
                    enqueueBanners([newNotif]);
                }
             }
           } catch (err) {
             console.error("SSE Parse Error:", err);
           }
        }
      }
    }
  } catch (error: any) {
    if (error.name === 'AbortError') return;
    console.error("SSE Connection lost. Retrying in 5s...", error);
    setTimeout(() => {
       if (user.value) startSSE(); 
    }, 5000);
  }
}

const navigationPayload = ref<any>(null);

function handleNavigation(view: string, payload: any = null) {
  isPopoverOpen.value = false; 
  activeView.value = view;
  navigationPayload.value = payload;
  if (view === 'notifications') {
    unreadCount.value = 0;
    shownBannerIds.value.clear(); 
    bannerQueue.value = []; 
    isBannerActive.value = false;
  }
}

function toggleTradePageView() {
  if (activeView.value === 'trade') {
    activeView.value = 'profile';
  } else {
    activeView.value = 'trade';
  }
}

function onInviteCreated(message: string) {}
function togglePopover() {
  isPopoverOpen.value = !isPopoverOpen.value;
  // وقتی پاپ‌اور باز می‌شود، لیست را رفرش نمی‌کنیم چون SSE آن را آپدیت نگه داشته
}

// ===== TRADE BANNER LOGIC =====
function handleTradeEvent(data: any) {
    if (!data || !user.value) return;
    
    // Check if we are involved in this trade
    if (data.offer_user_id !== user.value.id && data.responder_user_id !== user.value.id) {
        return; // Irrelevant trade
    }

    tradeBannerData.value = data;
    isTradeBannerActive.value = true;
    
    // Play sound if possible? (Browser policy might block)
    
    setTimeout(() => {
        closeTradeBanner();
    }, 6000);
}

function closeTradeBanner() {
    isTradeBannerActive.value = false;
    setTimeout(() => tradeBannerData.value = null, 300);
}

function getTradeSide(data: any) {
    if (!user.value) return 'unknown';
    // Responder action determines trade_type.
    // If trade_type is 'buy': Responder BOUGHT.
    // If I am Responder -> I Bought (Buy side).
    // If I am Offer Owner -> I Sold (Sell side).
    
    const isResponder = data.responder_user_id === user.value.id;
    const actionIsBuy = data.trade_type === 'buy';
    
    if (isResponder) {
        return actionIsBuy ? 'buy' : 'sell';
    } else {
        return actionIsBuy ? 'sell' : 'buy';
    }
}

function getTradeBannerClass(data: any) {
    return getTradeSide(data) === 'buy' ? 'banner-buy' : 'banner-sell';
}

function getTradeBannerIcon(data: any) {
    return getTradeSide(data) === 'buy' ? '🎉' : '💰';
}

function getTradeBannerTitle(data: any) {
    return getTradeSide(data) === 'buy' ? 'خرید موفق!' : 'فروش موفق!';
}

function getTradeCounterparty(data: any) {
    if (!user.value) return '...';
    return data.offer_user_id === user.value.id 
        ? data.responder_user_name 
        : data.offer_user_name;
}
// ==============================

function truncateMessage(message: string, length = 50) {
  const cleanMessage = message.replace(/\*\*(.*?)\*\*/g, '$1').replace(/`/g, '').replace(/\n/g, ' ');
  if (cleanMessage.length <= length) return cleanMessage;
  return cleanMessage.substring(0, length) + '...';
}

onMounted(async () => {
  setTimeout(() => { document.body.style.backgroundColor = '#f0f2f5'; }, 100);
  if (tg) { try { tg.setHeaderColor('#ffffff'); tg.setBackgroundColor('#f0f2f5'); } catch (e) { console.error("Telegram API error:", e); } }
  try {
    let token = null;

    if (tg && tg.initData) {
        loadingMessage.value = 'در حال احراز هویت...';
        const loginResp = await fetch(`${API_BASE_URL}/api/auth/webapp-login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ init_data: tg.initData }), });
        if (!loginResp.ok) throw new Error("احراز هویت اولیه ناموفق بود.");
        const loginJson = await loginResp.json();
        token = loginJson.access_token;
        jwtToken.value = token;
        await loadUser();
    } else {
        // Browser Mode
        const storedToken = localStorage.getItem('auth_token');
        if (storedToken) {
            console.log("Found stored token, attempting auto-login...");
            jwtToken.value = storedToken;
            try {
                await loadUser();
            } catch (e) {
                console.warn("Stored token invalid, showing login.");
                loginStep.value = 'mobile';
                loadingMessage.value = '';
            }
        } else {
             console.warn("No stored token. Waiting for OTP login.");
             loadingMessage.value = ''; 
             loginStep.value = 'mobile';
        }
    }
    
  } catch (e: any) { 
      // If we are in browser (no tg) and error occurred, show login
      if (!tg && !tg?.initData) {
          loadingMessage.value = '';
          loginStep.value = 'mobile';
      } else {
          loadingMessage.value = `⚠️ ${e.message}`; 
      }
  }
});

onUnmounted(() => {
  // قطع اتصال SSE هنگام بسته شدن کامپوننت
  if (sseController) sseController.abort();
});
</script>

<template>
  <div class="app-container">
    
    <transition name="fade">
      <div 
        v-if="isBannerActive" 
        ref="bannerRef"
        class="app-notification" 
        :class="[`type-${notificationLevel}`, `cat-${notificationCategory}`]"
        :style="bannerStyle"
        @touchstart="onTouchStart"
        @touchmove="onTouchMove"
        @touchend="onTouchEnd"
      >
        <button @click.stop="closeNotificationBanner" class="close-notif">×</button>

        <div class="notif-content-wrapper" @click="currentBannerNotification && openNotificationDetails(currentBannerNotification)">
          <div class="notif-icon-col">
             <span class="icon">{{ getIcon(notificationLevel, notificationCategory) }}</span>
          </div>
          
          <div class="notif-text-col">
             <div v-if="notificationCategory === 'system'" class="banner-meta-row">
                <span class="badge-system-banner">مدیریت</span>
             </div>
             <div class="banner-title-row">
                <span class="banner-title">{{ bannerTitle }}</span>
             </div>
             <div class="notif-content">
               {{ bannerBody }}
             </div>
          </div>
        </div>
      </div>
    </transition>

    <transition name="slide-down">
      <div 
        v-if="isTradeBannerActive && tradeBannerData" 
        class="trade-banner"
        :class="getTradeBannerClass(tradeBannerData)"
        @click="closeTradeBanner"
      >
        <div class="trade-banner-content">
          <div class="trade-banner-icon">
            {{ getTradeBannerIcon(tradeBannerData) }}
          </div>
          <div class="trade-banner-text">
            <div class="trade-banner-title">
              {{ getTradeBannerTitle(tradeBannerData) }}
            </div>
            <div class="trade-banner-details">
              <span>{{ tradeBannerData.quantity }} {{ tradeBannerData.commodity_name }}</span>
              <span class="separator">•</span>
              <span>{{ tradeBannerData.price.toLocaleString() }} تومان</span>
            </div>
            <div class="trade-banner-user">
              طرف حساب: {{ getTradeCounterparty(tradeBannerData) }}
            </div>
          </div>
        </div>
      </div>
    </transition>

    <transition name="fade">
      <div v-if="selectedNotification" class="details-modal-backdrop" @click="closeNotificationModal">
        <div class="details-modal-card" @click.stop>
          <div class="details-modal-header">
            <span class="details-title">جزئیات پیام</span>
            <button class="details-close-btn" @click="closeNotificationModal">×</button>
          </div>
          <div class="details-modal-body" v-html="selectedNotification.message.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\n/g, '<br>')"></div>
          <div class="details-modal-footer">
             <span class="details-date">{{ new Date(selectedNotification.created_at).toLocaleDateString('fa-IR') + ' ' + new Date(selectedNotification.created_at).toLocaleTimeString('fa-IR', {hour:'2-digit', minute:'2-digit'}) }}</span>
          </div>
        </div>
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
        
        <span 
          v-if="activeView === 'home'" 
          class="header-title"
        >Trading Bot</span>
        <span 
          v-else 
          class="header-title back-link" 
          @click="activeView = 'home'"
        >← بازگشت به صفحه اصلی</span>
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
              :class="{ 'unread-item': !notif.is_read }"
              @click="openNotificationDetails(notif)" 
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
        
        <!-- صفحه اصلی -->
        <HomePage 
          v-if="activeView === 'home'"
          :user-role="user.role"
          @navigate="handleNavigation"
        />
        
        <TradingView 
          v-else-if="showTradePage" 
          :api-base-url="API_BASE_URL" 
          :jwt-token="jwtToken"
          :user="user"
          :initial-tab="navigationPayload?.tab"
          @navigate="handleNavigation"
        /> 
        
        <template v-else>
          <UserProfile
            v-if="activeView === 'profile'"
            :user="user"
            @navigate="handleNavigation"
          />

          <UserProfile
            v-else-if="activeView === 'user_profile'"
            :user="navigationPayload"
            :is-admin-view="true"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
            @navigate="handleNavigation"
          />

          <NotificationCenter
            v-else-if="activeView === 'notifications'"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
            @navigate="handleNavigation"
          />

          <TradingSettings
            v-else-if="activeView === 'settings' && user.role === 'مدیر ارشد'"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
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

          <UserSettings
            v-else-if="activeView === 'user_settings'"
            @navigate="handleNavigation"
          />

          <PublicProfile
            v-else-if="activeView === 'public_profile'"
            :user="navigationPayload"
            :api-base-url="API_BASE_URL"
            :jwt-token="jwtToken"
            @navigate="handleNavigation"
          />

          </template>
        
      </template>

      <!-- Login UI -->
      <template v-else-if="loginStep !== 'none'">
        <div class="login-container">
            <div class="login-card">
                <h2>🔐 ورود به پنل</h2>
                
                <div v-if="loginStep === 'mobile'">
                    <p>لطفاً شماره موبایل خود را وارد کنید.</p>
                    <input v-model="loginMobile" placeholder="شماره موبایل (مثال: 09123456789)" dir="ltr" />
                    <button @click="handleRequestOtp" :disabled="isLoginLoading">
                        {{ isLoginLoading ? 'در حال ارسال...' : 'ارسال کد تایید' }}
                    </button>
                </div>
                
                <div v-else-if="loginStep === 'otp'">
                    <p>کد ارسال شده به ربات تلگرام را وارد کنید:</p>
                    <input v-model="loginCode" placeholder="کد تایید" dir="ltr" type="text" inputmode="numeric" />
                    <button @click="handleVerifyOtp" :disabled="isLoginLoading">
                        {{ isLoginLoading ? 'در حال بررسی...' : 'تایید و ورود' }}
                    </button>
                    <button @click="loginStep = 'mobile'" class="secondary-btn">بازگشت</button>
                </div>

                <p v-if="loginError" class="error-text">{{ loginError }}</p>
            </div>
        </div>
      </template>
    </main>

    <MainMenu 
      v-if="user && user.role !== 'WATCH' && activeView !== 'trade'" 
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
  direction: rtl;
}

.app-container { 
  display: flex; 
  flex-direction: column; 
  min-height: 100dvh; 
  position: relative; 
}
.main-content { 
  flex-grow: 1; 
  padding: 16px; 
  position: relative; 
  padding-top: 73px; 
  padding-bottom: 100px; 
}

.loading-container { display: flex; flex-direction: column; justify-content: center; align-items: center; height: 100%; color: var(--text-secondary); padding-top: 73px; } 
.spinner { width: 40px; height: 40px; border: 4px solid rgba(0, 0, 0, 0.1); border-left-color: var(--primary-color); border-radius: 50%; animation: spin 1s linear infinite; margin-bottom: 16px; } 
@keyframes spin { to { transform: rotate(360deg); } }

/* --- استایل‌های بنر --- */
.app-notification {
  position: fixed;
  top: 16px;
  left: 16px;
  right: 16px;
  background-color: rgba(255, 255, 255, 0.75) !important; 
  backdrop-filter: blur(20px) saturate(180%); 
  -webkit-backdrop-filter: blur(20px) saturate(180%); 
  padding: 12px;
  border-radius: 14px; 
  z-index: 10000;
  box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.15);
  font-size: 14px;
  line-height: 1.5;
  direction: rtl;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-right-width: 5px;
  border-right-style: solid; 
  display: flex;
  flex-direction: column;
  user-select: none; 
}

.app-notification.type-success { border-right-color: #34c759; }
.app-notification.type-warning { border-right-color: #ffcc00; }
.app-notification.type-error   { border-right-color: #ff3b30; }
.app-notification.type-info    { border-right-color: #007aff; }

.notif-content-wrapper {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding-left: 24px; 
  cursor: pointer; /* نشان دادن قابل کلیک بودن */
}

.notif-icon-col {
  font-size: 24px;
  line-height: 1;
  padding-top: 2px;
}

.notif-text-col {
  flex-grow: 1;
  display: flex;
  flex-direction: column;
  gap: 2px; 
}

.banner-meta-row {
  display: flex;
  align-items: center;
  margin-bottom: 2px;
}

.banner-title-row {
  display: flex;
  align-items: center;
}

.banner-title {
    font-weight: 800;
    font-size: 13.5px;
    color: var(--text-color);
}

.notif-content {
  font-size: 12.5px;
  color: var(--text-secondary);
  white-space: pre-line;
  line-height: 1.4;
  margin-top: 2px;
}

.badge-system-banner {
  background-color: #333;
  color: #fff;
  font-size: 9px;
  padding: 2px 5px;
  border-radius: 4px;
  font-weight: bold;
  line-height: 1;
  opacity: 0.8; 
}

.close-notif {
  position: absolute;
  top: 8px;
  left: 8px;
  background-color: transparent !important;
  border: none !important;
  box-shadow: none !important;
  width: 24px !important;
  height: 24px !important;
  padding: 0 !important;
  margin: 0 !important;
  color: #999;
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0.6;
  border-radius: 50%;
  transition: all 0.2s;
  z-index: 10;
}
.close-notif:hover {
  opacity: 1;
  background-color: rgba(0,0,0,0.05) !important;
  color: #ff3b30;
}
.close-notif:active {
  transform: scale(0.9);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.4s cubic-bezier(0.25, 0.8, 0.5, 1), transform 0.4s cubic-bezier(0.25, 0.8, 0.5, 1);
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(-30px) scale(0.9); 
}

/* --- استایل‌های مودال جزئیات (جدید) --- */
.details-modal-backdrop {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: rgba(0, 0, 0, 0.4);
  z-index: 11000; /* بالاتر از همه چیز */
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  padding: 20px;
}

.details-modal-card {
  background-color: #fff;
  border-radius: 16px;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 10px 40px rgba(0,0,0,0.2);
  animation: slideUp 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
  display: flex;
  flex-direction: column;
  max-height: 80vh;
}

@keyframes slideUp {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.details-modal-header {
  padding: 16px;
  border-bottom: 1px solid #eee;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.details-title {
  font-weight: 700;
  font-size: 16px;
}

.details-close-btn {
  background: #f5f5f5;
  border: none;
  border-radius: 50%;
  width: 30px;
  height: 30px;
  font-size: 18px;
  color: #666;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.details-modal-body {
  padding: 20px;
  font-size: 14px;
  line-height: 1.7;
  color: #333;
  overflow-y: auto;
}

.details-modal-footer {
  padding: 12px 16px;
  background-color: #fafafa;
  border-top: 1px solid #eee;
  text-align: left;
  border-bottom-left-radius: 16px;
  border-bottom-right-radius: 16px;
}

.details-date {
  font-size: 12px;
  color: #999;
}

/* --- پاپ‌اور و هدر --- */
.app-header {
  position: fixed; top: 0; left: 0; right: 0;
  background-color: var(--card-bg, #ffffff);
  border-bottom: 1px solid var(--border-color, #e5e5e5);
  padding: 5px 16px;
  z-index: 10; 
}

.header-content {
  display: flex; justify-content: space-between; align-items: center; min-height: 32px; 
}

.header-title {
  font-size: 18px; font-weight: 700; color: var(--text-color); text-align: right; 
}

.header-title.back-link {
  color: #007aff;
  cursor: pointer;
  font-size: 14px;
}
.header-title.back-link:hover {
  text-decoration: underline;
}

.notification-bell-btn {
  position: relative; background: none; border: none; cursor: pointer; font-size: 22px; 
  padding: 0; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;
  color: var(--text-secondary); transition: all 0.2s; line-height: 1; border-radius: 50%; 
}
.notification-bell-btn:hover {
  background-color: #f0f0f0; color: var(--text-color);
}

.notification-badge {
  position: absolute; top: 0; right: 0; background-color: #f44336; color: white;
  border-radius: 50%; width: 18px; height: 18px; font-size: 11px; font-weight: 600;
  display: flex; align-items: center; justify-content: center; line-height: 1;
  border: 2px solid var(--card-bg, #ffffff); transform: translate(15%, -15%);
}

.popover-backdrop {
  position: fixed; top: 0; left: 0; width: 100%; height: 100%;
  background: rgba(0, 0, 0, 0.1); z-index: 100;
  backdrop-filter: blur(2px);
}

.notification-popover {
  position: absolute; top: 65px; left: 16px; width: 320px; max-width: calc(100% - 32px);
  background: var(--card-bg, #ffffff); border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15); z-index: 101; 
  display: flex; flex-direction: column; overflow: hidden; 
}

.popover-header {
  padding: 12px 16px; font-weight: 700; font-size: 16px;
  border-bottom: 1px solid var(--border-color, #e5e5e5); text-align: right;
}

.popover-list {
  max-height: 300px; overflow-y: auto;
}

.popover-empty, .popover-loading {
  padding: 24px; text-align: center; color: var(--text-secondary);
}

.popover-item {
  display: flex; justify-content: space-between; align-items: center;
  gap: 12px; padding: 12px 16px; border-bottom: 1px solid #f0f0f0;
  cursor: pointer; transition: background-color 0.15s; text-align: right;
}
.popover-item:hover { background-color: #f9f9f9; }
.popover-item:last-child { border-bottom: none; }

.unread-item {
    background-color: #f0f9ff;
    font-weight: 500;
}

.popover-item-text {
  font-size: 14px; line-height: 1.5; color: var(--text-color); flex-grow: 1;
}

.popover-item-date {
  font-size: 12px; color: var(--text-secondary); flex-shrink: 0; 
  direction: ltr; text-align: left;
}

.popover-footer {
  padding: 8px; background-color: #f9f9f9; border-top: 1px solid var(--border-color, #e5e5e5);
}
.popover-footer button {
  width: 100%; padding: 10px; border: none; background: transparent;
  color: var(--primary-color, #007AFF); font-size: 14px; font-weight: 600;
  cursor: pointer; border-radius: 8px; transition: background-color 0.15s;
  font-family: 'Vazirmatn', sans-serif; 
}
.popover-footer button:hover { background-color: #eef; }

/* Login Styles */
.login-container {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
    padding: 20px;
    background-color: var(--bg-color);
}

.login-card {
    background: white;
    padding: 30px;
    border-radius: 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.1);
    width: 100%;
    max-width: 400px;
    text-align: center;
}

.login-card h2 {
    margin-bottom: 24px;
    color: var(--text-color);
}

.login-card input {
    width: 100%;
    padding: 12px;
    margin-bottom: 16px;
    border: 1px solid #ddd;
    border-radius: 8px;
    font-size: 16px;
    font-family: inherit;
}

.login-card button {
    width: 100%;
    padding: 12px;
    background-color: var(--primary-color);
    color: white;
    border: none;
    border-radius: 8px;
    font-size: 16px;
    cursor: pointer;
    font-weight: bold;
    margin-bottom: 8px;
}

.login-card button:disabled {
    opacity: 0.7;
    cursor: not-allowed;
}

.login-card .secondary-btn {
    background-color: transparent;
    color: var(--text-secondary);
    border: 1px solid #ddd;
}

.error-text {
    color: #ff3b30;
    margin-top: 10px;
    font-size: 14px;
}
/* Trade Banner Styles */
.trade-banner {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  z-index: 9999;
  padding: 16px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.2);
  cursor: pointer;
  direction: rtl;
  color: white;
}

.banner-buy {
  background: linear-gradient(135deg, #059669, #10b981);
}

.banner-sell {
  background: linear-gradient(135deg, #dc2626, #ef4444);
}

.trade-banner-content {
  display: flex;
  align-items: center;
  gap: 16px;
  max-width: 600px;
  margin: 0 auto;
}

.trade-banner-icon {
  font-size: 32px;
  background: rgba(255,255,255,0.2);
  width: 50px;
  height: 50px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  flex-shrink: 0;
}

.trade-banner-text {
  flex: 1;
}

.trade-banner-title {
  font-weight: 800;
  font-size: 18px;
  margin-bottom: 4px;
}

.trade-banner-details {
  font-size: 15px;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
}

.separator {
  opacity: 0.6;
}

.trade-banner-user {
  font-size: 13px;
  opacity: 0.9;
  margin-top: 4px;
}

/* Animations */
.slide-down-enter-active,
.slide-down-leave-active {
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.slide-down-enter-from,
.slide-down-leave-to {
  transform: translateY(-100%);
  opacity: 0;
}
</style>