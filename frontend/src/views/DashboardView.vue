<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, Store, LogOut, AlertTriangle, Ban } from 'lucide-vue-next'
import { useNotificationStore } from '../stores/notifications'
import { apiFetch, forceLogout } from '../utils/auth'

const router = useRouter()
const notificationStore = useNotificationStore()
const user = ref<any>(null)
const loading = ref(true)

const isRestricted = computed(() => {
  if (!user.value?.trading_restricted_until) return false
  return new Date(user.value.trading_restricted_until) > new Date()
})

const isBlocked = computed(() => {
  return user.value && !user.value.has_bot_access
})

const restrictedUntil = computed(() => {
  if (!user.value?.trading_restricted_until) return ''
  const d = new Date(user.value.trading_restricted_until)
  return d.toLocaleDateString('fa-IR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })
})

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 12) return 'صبح بخیر'
  if (hour < 17) return 'ظهر بخیر'
  return 'عصر بخیر'
})

const userInitial = computed(() => {
  if (!user.value) return ''
  const name = user.value.full_name || user.value.account_name
  return name ? name[0] : '?'
})

async function fetchUser() {
  try {
    const res = await apiFetch('/api/auth/me')
    if (res.ok) {
      user.value = await res.json()
    }
    // 401 handling is automatic via apiFetch → forceLogout
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

async function logout() {
  try {
    const res = await apiFetch('/api/sessions/active')
    const activeSessions = await res.json()
    const currentSession = activeSessions.find((s: any) => s.is_current)
    if (currentSession) {
      await apiFetch(`/api/sessions/${currentSession.id}`, { method: 'DELETE' })
    }
  } catch (e) {
    console.error(e)
  }
  forceLogout()
}

onMounted(fetchUser)
</script>

<template>
  <div class="dashboard-page">
    
    <!-- Loading -->
    <div v-if="loading" class="ds-loading-state">
      <div class="ds-spinner"></div>
    </div>

    <div v-else-if="user" class="dashboard-content">

      <!-- ═══ Top Bar ═══ -->
      <header class="top-bar">
        <!-- Notifications on the far Right (in RTL) -->
        <button class="ds-icon-btn notif-btn" @click="router.push('/notifications')" aria-label="اعلان‌ها">
          <Bell :size="22" />
          <div v-if="notificationStore.appNotifications.length > 0" class="notif-dot"></div>
        </button>

        <!-- User Info in the Center -->
        <div class="user-info-center" @click="router.push('/profile')">
          <div class="avatar">
            <span>{{ userInitial }}</span>
          </div>
          <div class="user-text">
            <span class="greeting">{{ greeting }} 👋</span>
            <span class="user-name">{{ user.full_name || user.account_name }}</span>
          </div>
        </div>

        <!-- Logout on the far Left (in RTL) -->
        <button class="ds-icon-btn logout-btn" @click="logout" aria-label="خروج">
          <LogOut :size="20" />
        </button>
      </header>

      <!-- ═══ Blocked Warning ═══ -->
      <div v-if="isBlocked" class="alert-card alert-blocked">
        <div class="alert-icon blocked-icon">
          <Ban :size="28" />
        </div>
        <div class="alert-body">
          <h3>حساب کاربری مسدود شده</h3>
          <p>دسترسی شما به سیستم توسط مدیریت مسدود شده است. برای اطلاعات بیشتر با پشتیبانی تماس بگیرید.</p>
        </div>
      </div>

      <!-- ═══ Restricted Warning ═══ -->
      <div v-else-if="isRestricted" class="alert-card alert-restricted">
        <div class="alert-icon restricted-icon">
          <AlertTriangle :size="24" />
        </div>
        <div class="alert-body">
          <h3>معاملات محدود شده</h3>
          <p>دسترسی معاملاتی شما تا <strong>{{ restrictedUntil }}</strong> محدود شده است.</p>
        </div>
      </div>

      <!-- ═══ Main Content ═══ -->
      <main class="main-section">

        <!-- Market Entry — Hero Button -->
        <button class="hero-btn" @click="router.push('/market')">
          <div class="hero-btn-bg"></div>
          <div class="hero-btn-content">
            <div class="hero-icon">
              <Store :size="32" />
            </div>
            <div class="hero-text">
              <span class="hero-title">ورود به بازار</span>
              <span class="hero-subtitle">مشاهده و ثبت لفظ‌های خرید و فروش</span>
            </div>
          </div>
          <div class="hero-arrow">←</div>
        </button>

      </main>

      <!-- Footer -->
      <footer class="dashboard-footer">
        <span>نسخه ۲.۵.۰</span>
      </footer>

    </div>



  </div>
</template>

<style scoped>
.dashboard-page {
  /* No fixed height here, let parent manage it */
  position: relative;
}

/* Content */
.dashboard-content {
  padding: 1.25rem;
  padding-bottom: 2rem; /* Reduced since App.vue handles scroll margin */
  width: 100%;
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
}

/* ═══ Top Bar ═══ */
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2rem;
  background: var(--ds-bg-card);
  padding: 0.5rem 0.25rem;
  border-radius: var(--ds-radius-lg);
}

.user-info-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  flex: 1;
}

.avatar {
  width: 52px;
  height: 52px;
  background: linear-gradient(135deg, var(--ds-primary-500), var(--ds-primary-600));
  border-radius: var(--ds-radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-weight: 800;
  font-size: 1.4rem;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
  transition: all 0.2s;
}
.user-info-center:active .avatar {
  transform: scale(0.95);
}

.user-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}
.greeting {
  font-size: var(--ds-font-sm);
  color: var(--ds-text-placeholder);
  font-weight: 500;
}
.user-name {
  font-size: 0.95rem;
  font-weight: 700;
  color: var(--ds-text-primary);
}

.logout-btn {
  color: var(--ds-danger-500);
}
.logout-btn:active {
  background: var(--ds-danger-50);
}

.notif-btn {
  color: var(--ds-primary-600);
}
.notif-btn:active {
  background: var(--ds-primary-50);
}

.notif-dot {
  position: absolute;
  top: 10px;
  right: 10px;
  width: 8px;
  height: 8px;
  background: var(--ds-danger-500);
  border-radius: 50%;
  border: 2px solid var(--ds-bg-card);
}

/* ═══ Alert Cards ═══ */
.alert-card {
  display: flex;
  align-items: flex-start;
  gap: 0.875rem;
  padding: 1rem 1.25rem;
  border-radius: var(--ds-radius-lg);
  margin-bottom: 1.5rem;
  animation: slideDown 0.4s ease-out;
}
@keyframes slideDown {
  from { opacity: 0; transform: translateY(-10px); }
  to { opacity: 1; transform: translateY(0); }
}

.alert-blocked {
  background: linear-gradient(135deg, var(--ds-danger-50), var(--ds-danger-100));
  border: 1px solid var(--ds-danger-200);
}
.alert-restricted {
  background: linear-gradient(135deg, var(--ds-warning-50), var(--ds-warning-100));
  border: 1px solid var(--ds-primary-200);
}

.alert-icon {
  flex-shrink: 0;
  width: 44px;
  height: 44px;
  border-radius: var(--ds-radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
}
.blocked-icon {
  background: var(--ds-danger-100);
  color: var(--ds-danger-600);
}
.restricted-icon {
  background: var(--ds-primary-100);
  color: var(--ds-primary-600);
}

.alert-body h3 {
  font-size: var(--ds-font-md);
  font-weight: 700;
  margin: 0 0 0.25rem 0;
}
.alert-blocked .alert-body h3 { color: #991b1b; }
.alert-restricted .alert-body h3 { color: #92400e; }

.alert-body p {
  font-size: 0.78rem;
  margin: 0;
  line-height: 1.6;
}
.alert-blocked .alert-body p { color: var(--ds-danger-700); }
.alert-restricted .alert-body p { color: #a16207; }

/* ═══ Main Section ═══ */
.main-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

/* ═══ Hero Button ═══ */
.hero-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.5rem;
  border-radius: var(--ds-radius-xl);
  border: none;
  cursor: pointer;
  overflow: hidden;
  -webkit-tap-highlight-color: transparent;
  transition: transform 0.2s;
}
.hero-btn:active {
  transform: scale(0.98);
}

.hero-btn-bg {
  position: absolute;
  inset: 0;
  background: var(--ds-gradient-primary);
}
.hero-btn-bg::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, transparent 30%, rgba(255,255,255,0.15) 50%, transparent 70%);
  animation: shimmer 3s ease-in-out infinite;
}
@keyframes shimmer {
  0%, 100% { transform: translateX(-100%); }
  50% { transform: translateX(100%); }
}

.hero-btn-content {
  display: flex;
  align-items: center;
  gap: 1rem;
  position: relative;
  z-index: 1;
}

.hero-icon {
  width: 56px;
  height: 56px;
  background: rgba(255,255,255,0.2);
  backdrop-filter: blur(10px);
  border-radius: var(--ds-radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
}

.hero-text {
  display: flex;
  flex-direction: column;
  text-align: right;
}
.hero-title {
  font-size: var(--ds-font-2xl);
  font-weight: 800;
  color: white;
}
.hero-subtitle {
  font-size: var(--ds-font-sm);
  color: rgba(255,255,255,0.8);
  margin-top: 0.15rem;
  font-weight: 500;
}

.hero-arrow {
  position: relative;
  z-index: 1;
  color: rgba(255,255,255,0.6);
  font-size: 1.5rem;
  font-weight: 300;
  animation: arrowBounce 2s ease-in-out infinite;
}
@keyframes arrowBounce {
  0%, 100% { transform: translateX(0); }
  50% { transform: translateX(-6px); }
}

/* ═══ Footer ═══ */
.dashboard-footer {
  text-align: center;
  padding: 1.5rem 0 1rem;
  font-size: var(--ds-font-xs);
  color: var(--ds-text-faint);
  font-weight: 500;
}
</style>
