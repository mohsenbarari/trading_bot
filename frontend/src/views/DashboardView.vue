<script setup lang="ts">
import { ref, onMounted, computed, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, Store, LogOut, AlertTriangle, Ban, Users } from 'lucide-vue-next'
import { useNotificationStore } from '../stores/notifications'
import { apiFetch, forceLogout } from '../utils/auth'
import { formatIranDateTime, getIranHour, parseIranDisplayDate } from '../utils/iranTime'

const router = useRouter()
const notificationStore = useNotificationStore()
const user = ref<any>(null)
const loading = ref(true)
const isSwitchModalOpen = ref(false)
const switchUsers = ref<any[]>([])
const switchLoading = ref(false)
const switchError = ref('')
const switchSearch = ref('')
const switchingUserId = ref<number | null>(null)
let switchSearchTimer: ReturnType<typeof setTimeout> | null = null
let switchRequestId = 0

const isRestricted = computed(() => {
  if (!user.value?.trading_restricted_until) return false
  const restrictedUntil = parseIranDisplayDate(user.value.trading_restricted_until)
  return Boolean(restrictedUntil && restrictedUntil > new Date())
})

const isInactiveAccount = computed(() => user.value?.account_status === 'inactive')
const isAccountant = computed(() => user.value?.is_accountant === true)

const isGloballyLockedAccount = computed(() => Boolean(user.value?.global_web_locked_at))

const globalLockGraceExpiresAtText = computed(() => {
  if (!user.value?.global_lock_grace_expires_at) return ''
  return formatIranDateTime(user.value.global_lock_grace_expires_at)
})

const inactiveAccountMessage = computed(() => {
  if (!isInactiveAccount.value) return ''
  if (isGloballyLockedAccount.value) {
    return 'نشست‌های وب و پیام‌رسان این حساب تا زمان فعال‌سازی مجدد بسته شده است.'
  }
  if (globalLockGraceExpiresAtText.value) {
    return `دسترسی شما به بازار بسته شده است. اگر حساب تا ${globalLockGraceExpiresAtText.value} دوباره فعال نشود، همه نشست‌های وب و پیام‌رسان شما هم بسته می‌شود.`
  }
  return 'دسترسی شما به بازار بسته شده است. برای فعال‌سازی مجدد با مدیریت تماس بگیرید.'
})

const restrictedUntil = computed(() => {
  if (!user.value?.trading_restricted_until) return ''
  return formatIranDateTime(user.value.trading_restricted_until)
})

const greeting = computed(() => {
  const hour = getIranHour()
  if (hour < 12) return 'صبح بخیر'
  if (hour < 17) return 'ظهر بخیر'
  return 'عصر بخیر'
})

const userInitial = computed(() => {
  if (!user.value) return ''
  const name = user.value.full_name || user.value.account_name
  return name ? name[0] : '?'
})

const canUseTestAccountSwitcher = computed(() => {
  if (!user.value) return false
  return user.value.role === 'مدیر ارشد' || hasTestAccountSwitchClaim()
})

const currentSwitchUserLabel = computed(() => {
  if (!user.value) return ''
  return user.value.full_name || user.value.account_name || ''
})

function hasTestAccountSwitchClaim() {
  const token = localStorage.getItem('auth_token')
  if (!token) return false

  try {
    const payloadPart = token.split('.')[1]
    if (!payloadPart) return false
    const base64 = payloadPart.replace(/-/g, '+').replace(/_/g, '/')
    const decoded = JSON.parse(window.atob(base64))
    return decoded?.dev_account_switch === true
  } catch {
    return false
  }
}

function buildSwitchUsersUrl(search: string) {
  const params = new URLSearchParams()
  const trimmed = search.trim()
  if (trimmed) params.set('search', trimmed)
  const query = params.toString()
  return query ? `/api/auth/dev-switch/users?${query}` : '/api/auth/dev-switch/users'
}

async function loadSwitchUsers(search = switchSearch.value) {
  const requestId = ++switchRequestId
  switchLoading.value = true
  switchError.value = ''

  try {
    const response = await apiFetch(buildSwitchUsersUrl(search))
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail || 'دریافت لیست کاربران تستی ممکن نشد')
    }
    if (requestId !== switchRequestId) return
    switchUsers.value = Array.isArray(payload) ? payload : []
  } catch (error: any) {
    if (requestId !== switchRequestId) return
    switchUsers.value = []
    switchError.value = error?.message || 'دریافت لیست کاربران تستی ممکن نشد'
  } finally {
    if (requestId === switchRequestId) {
      switchLoading.value = false
    }
  }
}

async function openAccountSwitchModal() {
  isSwitchModalOpen.value = true
  await loadSwitchUsers(switchSearch.value)
}

function closeAccountSwitchModal() {
  isSwitchModalOpen.value = false
  switchError.value = ''
  switchSearch.value = ''
  if (switchSearchTimer) {
    clearTimeout(switchSearchTimer)
    switchSearchTimer = null
  }
}

function queueSwitchSearch() {
  if (switchSearchTimer) {
    clearTimeout(switchSearchTimer)
  }
  switchSearchTimer = setTimeout(() => {
    void loadSwitchUsers(switchSearch.value)
  }, 220)
}

function customerTierLabel(tier: string | null | undefined) {
  if (tier === 'tier1') return 'مشتری سطح ۱'
  if (tier === 'tier2') return 'مشتری سطح ۲'
  return 'مشتری'
}

async function switchToAccount(targetUser: any) {
  if (!targetUser?.id || switchingUserId.value !== null) return
  switchingUserId.value = Number(targetUser.id)
  switchError.value = ''

  try {
    const response = await apiFetch(`/api/auth/dev-switch/${targetUser.id}`, { method: 'POST' })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.access_token || !payload?.refresh_token) {
      throw new Error(payload?.detail || 'سوییچ حساب انجام نشد')
    }

    localStorage.setItem('auth_token', payload.access_token)
    localStorage.setItem('refresh_token', payload.refresh_token)
    localStorage.removeItem('current_user_summary')
    window.location.assign('/')
  } catch (error: any) {
    switchError.value = error?.message || 'سوییچ حساب انجام نشد'
  } finally {
    switchingUserId.value = null
  }
}

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

function openMarket() {
  if (isInactiveAccount.value || isAccountant.value) return
  router.push('/market')
}

onMounted(fetchUser)

onBeforeUnmount(() => {
  if (switchSearchTimer) {
    clearTimeout(switchSearchTimer)
    switchSearchTimer = null
  }
})
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
        <button v-if="!isAccountant" class="ds-icon-btn logout-btn" @click="logout" aria-label="خروج">
          <LogOut :size="20" />
        </button>
      </header>

      <!-- ═══ Blocked Warning ═══ -->
      <div v-if="isInactiveAccount" class="alert-card alert-blocked">
        <div class="alert-icon blocked-icon">
          <Ban :size="28" />
        </div>
        <div class="alert-body">
          <h3>{{ isGloballyLockedAccount ? 'حساب کاربری قفل شده است' : 'حساب کاربری غیرفعال شده است' }}</h3>
          <p>{{ inactiveAccountMessage }}</p>
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
        <button v-if="!isAccountant" class="hero-btn" :disabled="isInactiveAccount" @click="openMarket">
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

        <button
          v-if="canUseTestAccountSwitcher"
          class="switcher-entry-btn"
          type="button"
          @click="openAccountSwitchModal"
        >
          <span class="switcher-entry-icon"><Users :size="20" /></span>
          <span class="switcher-entry-text">
            <strong>سوییچ موقت حساب</strong>
            <small>بدون OTP و خروج، بین اکانت‌های موجود جابه‌جا شوید</small>
          </span>
        </button>

      </main>

      <!-- Footer -->
      <footer class="dashboard-footer">
        <span>نسخه ۲.۵.۰</span>
      </footer>

    </div>



  </div>

  <div v-if="isSwitchModalOpen" class="switcher-modal-backdrop" @click.self="closeAccountSwitchModal">
    <div class="switcher-modal" role="dialog" aria-modal="true" aria-label="سوییچ موقت حساب">
      <div class="switcher-modal-header">
        <div>
          <h3>سوییچ موقت حساب</h3>
          <p>حساب فعلی: <strong>{{ currentSwitchUserLabel }}</strong></p>
        </div>
        <button type="button" class="switcher-close-btn" @click="closeAccountSwitchModal">×</button>
      </div>

      <div class="switcher-search-box">
        <input
          v-model="switchSearch"
          type="text"
          placeholder="جستجو با نام، نام کاربری یا موبایل"
          @input="queueSwitchSearch"
        />
      </div>

      <p v-if="switchError" class="switcher-error">{{ switchError }}</p>
      <p v-else-if="switchLoading" class="switcher-empty">در حال دریافت کاربران...</p>
      <p v-else-if="switchUsers.length === 0" class="switcher-empty">کاربری برای سوییچ پیدا نشد.</p>

      <div v-else class="switcher-user-list">
        <button
          v-for="switchUser in switchUsers"
          :key="switchUser.id"
          type="button"
          class="switcher-user-row"
          :disabled="switchingUserId !== null || Number(switchUser.id) === Number(user.id)"
          @click="switchToAccount(switchUser)"
        >
          <div class="switcher-user-main">
            <div class="switcher-user-title-row">
              <strong>{{ switchUser.full_name || switchUser.account_name }}</strong>
              <span v-if="Number(switchUser.id) === Number(user.id)" class="switcher-current-pill">حساب فعلی</span>
              <span v-else-if="switchingUserId === Number(switchUser.id)" class="switcher-current-pill switcher-current-pill--busy">در حال سوییچ...</span>
            </div>
            <span class="switcher-user-meta">@{{ switchUser.account_name }} · {{ switchUser.mobile_number }}</span>
            <div class="switcher-badges">
              <span class="switcher-badge">{{ switchUser.role }}</span>
              <span v-if="switchUser.is_accountant" class="switcher-badge switcher-badge--accountant">حسابدار</span>
              <span v-if="switchUser.is_customer" class="switcher-badge switcher-badge--customer">{{ customerTierLabel(switchUser.customer_tier) }}</span>
            </div>
          </div>
          <span class="switcher-user-arrow">←</span>
        </button>
      </div>
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

.hero-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* ═══ Top Bar ═══ */
.top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1.25rem;
  background: var(--ds-bg-card);
  padding: 0.75rem 0.5rem;
  border-radius: var(--ds-radius-lg);
}

.user-info-center {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 0.875rem;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
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
  align-items: flex-start;
  text-align: right;
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
  margin-bottom: 1rem;
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

.switcher-entry-btn {
  display: flex;
  align-items: center;
  gap: 0.9rem;
  width: 100%;
  padding: 1rem 1.1rem;
  border-radius: var(--ds-radius-xl);
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: linear-gradient(135deg, rgba(15, 23, 42, 0.05), rgba(148, 163, 184, 0.08));
  color: var(--ds-text-primary);
  text-align: right;
  cursor: pointer;
  transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}

.switcher-entry-btn:active {
  transform: scale(0.985);
}

.switcher-entry-btn:hover {
  border-color: rgba(245, 158, 11, 0.35);
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.08);
}

.switcher-entry-icon {
  width: 42px;
  height: 42px;
  border-radius: 14px;
  background: rgba(245, 158, 11, 0.12);
  color: var(--ds-primary-600);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.switcher-entry-text {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.switcher-entry-text strong {
  font-size: var(--ds-font-md);
}

.switcher-entry-text small {
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.5;
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

.switcher-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.5);
  backdrop-filter: blur(10px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
  z-index: 1200;
}

.switcher-modal {
  width: min(100%, 620px);
  max-height: min(78vh, 760px);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid rgba(148, 163, 184, 0.22);
  border-radius: 24px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.24);
}

.switcher-modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  padding: 1.2rem 1.2rem 1rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.18);
}

.switcher-modal-header h3 {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 800;
  color: var(--ds-text-primary);
}

.switcher-modal-header p {
  margin: 0.3rem 0 0;
  font-size: 0.85rem;
  color: var(--ds-text-secondary);
}

.switcher-close-btn {
  width: 38px;
  height: 38px;
  border: none;
  border-radius: 12px;
  background: rgba(148, 163, 184, 0.14);
  color: var(--ds-text-primary);
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
}

.switcher-search-box {
  padding: 1rem 1.2rem 0.25rem;
}

.switcher-search-box input {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 16px;
  padding: 0.92rem 1rem;
  font: inherit;
  background: rgba(248, 250, 252, 0.92);
  color: var(--ds-text-primary);
}

.switcher-search-box input:focus {
  outline: none;
  border-color: rgba(245, 158, 11, 0.42);
  box-shadow: 0 0 0 4px rgba(245, 158, 11, 0.12);
}

.switcher-error,
.switcher-empty {
  padding: 0.9rem 1.2rem;
  margin: 0;
  font-size: 0.88rem;
}

.switcher-error {
  color: var(--ds-danger-600);
}

.switcher-empty {
  color: var(--ds-text-secondary);
}

.switcher-user-list {
  padding: 0.35rem 0.7rem 1rem;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
}

.switcher-user-row {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.85rem;
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.88);
  padding: 0.9rem 1rem;
  cursor: pointer;
  text-align: right;
}

.switcher-user-row:disabled {
  cursor: default;
  opacity: 0.72;
}

.switcher-user-main {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
}

.switcher-user-title-row {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  flex-wrap: wrap;
}

.switcher-user-title-row strong {
  font-size: 0.95rem;
  color: var(--ds-text-primary);
}

.switcher-user-meta {
  font-size: 0.82rem;
  color: var(--ds-text-secondary);
  direction: ltr;
  text-align: right;
}

.switcher-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.switcher-badge,
.switcher-current-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0.22rem 0.55rem;
  border-radius: 999px;
  font-size: 0.74rem;
  font-weight: 700;
}

.switcher-badge {
  background: rgba(148, 163, 184, 0.16);
  color: var(--ds-text-secondary);
}

.switcher-badge--accountant {
  background: rgba(14, 165, 233, 0.14);
  color: #0369a1;
}

.switcher-badge--customer {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.switcher-current-pill {
  background: rgba(15, 23, 42, 0.08);
  color: var(--ds-text-secondary);
}

.switcher-current-pill--busy {
  background: rgba(245, 158, 11, 0.16);
  color: #b45309;
}

.switcher-user-arrow {
  color: var(--ds-text-placeholder);
  font-size: 1.1rem;
  flex-shrink: 0;
}
</style>
