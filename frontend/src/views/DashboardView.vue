<script setup lang="ts">
import { ref, onMounted, computed, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, BriefcaseBusiness, ChevronLeft, Store, LogOut, AlertTriangle, Ban, UserRound, Users } from 'lucide-vue-next'
import { useNotificationStore } from '../stores/notifications'
import { apiFetch, forceLogout } from '../utils/auth'
import { formatIranDateTime, getIranHour, IRAN_TIME_ZONE, parseIranDisplayDate } from '../utils/iranTime'
import { marketRuntime } from '../composables/useMarketRuntime'
import AppLoadingState from '../components/ui/AppLoadingState.vue'
import AppStatusBadge from '../components/ui/AppStatusBadge.vue'
import AppSectionCard from '../components/ui/AppSectionCard.vue'

interface DashboardTrade {
  id: number
  trade_type: string
  commodity_name: string
  quantity: number
  price: number
  offer_user_id: number | null
  responder_user_id: number | null
  offer_user_name?: string | null
  responder_user_name?: string | null
  counterparty_name?: string | null
}

const router = useRouter()
const notificationStore = useNotificationStore()
const user = ref<any>(null)
const loading = ref(true)
const todayTrades = ref<DashboardTrade[]>([])
const todayTradesLoading = ref(false)
const todayTradesError = ref('')
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
const isMarketOpen = computed(() => marketRuntime.value.is_open)
const isMarketClosed = computed(() => !isMarketOpen.value)
const marketEntryStatusLabel = computed(() => (isMarketOpen.value ? 'بازار باز' : 'بازار بسته'))
const marketEntrySubtitle = computed(() => (
  isMarketOpen.value
    ? 'مشاهده و ثبت لفظ‌های خرید و فروش'
    : 'فعلاً امکان ثبت لفظ جدید وجود ندارد'
))

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

const accountStateLabel = computed(() => {
  if (isInactiveAccount.value) return isGloballyLockedAccount.value ? 'حساب قفل شده' : 'حساب غیرفعال'
  if (isRestricted.value) return 'معاملات محدود'
  return 'حساب فعال'
})

const accountStateDescription = computed(() => {
  if (isInactiveAccount.value) return 'دسترسی بازار تا فعال‌سازی مجدد بسته است'
  if (isRestricted.value) return 'محدودیت معاملاتی زمان‌دار فعال است'
  return isAccountant.value ? 'دسترسی حسابدار طبق مجوز سرگروه' : 'آماده انجام عملیات روزانه'
})

const accountStateTone = computed(() => {
  if (isInactiveAccount.value) return 'danger'
  if (isRestricted.value) return 'warning'
  return 'success'
})

const todayTradesCountLabel = computed(() => `${formatDashboardNumber(todayTrades.value.length)} معامله`)
const notificationCountLabel = computed(() => {
  const count = notificationStore.appNotifications.length
  return count > 0 ? `${formatDashboardNumber(count)} اعلان` : 'بدون اعلان'
})

const todayActivityTone = computed(() => (todayTradesError.value ? 'danger' : 'primary'))
const marketStatusTone = computed(() => (isMarketOpen.value && !isAccountant.value ? 'success' : 'neutral'))

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

const tradeHistoryPerspectiveUserId = computed(() => {
  if (!user.value) return null
  const ownerUserId = Number(user.value.accountant_owner_user_id)
  if (user.value.is_accountant === true && Number.isInteger(ownerUserId) && ownerUserId > 0) {
    return ownerUserId
  }
  const currentUserId = Number(user.value.id)
  return Number.isInteger(currentUserId) && currentUserId > 0 ? currentUserId : null
})

function getTodayIranGregorianDate() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: IRAN_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const year = parts.find((part) => part.type === 'year')?.value || '1970'
  const month = parts.find((part) => part.type === 'month')?.value || '01'
  const day = parts.find((part) => part.type === 'day')?.value || '01'
  return `${year}-${month}-${day}`
}

function normalizeDashboardUserId(value: unknown) {
  const id = Number(value)
  return Number.isInteger(id) && id > 0 ? id : null
}

function isTradeParticipantForPerspective(trade: DashboardTrade) {
  const perspectiveUserId = normalizeDashboardUserId(tradeHistoryPerspectiveUserId.value)
  if (perspectiveUserId === null) return false
  return normalizeDashboardUserId(trade.offer_user_id) === perspectiveUserId
    || normalizeDashboardUserId(trade.responder_user_id) === perspectiveUserId
}

function formatDashboardNumber(value: number | string | null | undefined) {
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized.toLocaleString('fa-IR') : '۰'
}

function getTradeCounterpartyLabel(trade: DashboardTrade) {
  if (typeof trade.counterparty_name === 'string' && trade.counterparty_name.trim()) {
    return trade.counterparty_name
  }
  return Number(trade.responder_user_id) === Number(tradeHistoryPerspectiveUserId.value)
    ? trade.offer_user_name || 'نامشخص'
    : trade.responder_user_name || 'نامشخص'
}

function getTradeTypeForPerspective(trade: DashboardTrade) {
  const tradeType = String(trade.trade_type || '').toLowerCase()
  const isPerspectiveResponder = Number(trade.responder_user_id) === Number(tradeHistoryPerspectiveUserId.value)
  if (tradeType !== 'buy' && tradeType !== 'sell') return 'unknown'
  return isPerspectiveResponder ? tradeType : (tradeType === 'buy' ? 'sell' : 'buy')
}

function getTradeTypeLabel(trade: DashboardTrade) {
  const type = getTradeTypeForPerspective(trade)
  if (type === 'buy') return 'خرید'
  if (type === 'sell') return 'فروش'
  return 'نامشخص'
}

async function loadTodayTrades() {
  const today = getTodayIranGregorianDate()
  todayTradesLoading.value = true
  todayTradesError.value = ''

  try {
    const response = await apiFetch(`/api/trades/my?from_date=${today}&to_date=${today}&limit=20`)
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail || 'دریافت معاملات امروز ناموفق بود')
    }
    todayTrades.value = Array.isArray(payload)
      ? (payload as DashboardTrade[]).filter(isTradeParticipantForPerspective)
      : []
  } catch (error: any) {
    todayTrades.value = []
    todayTradesError.value = error?.message || 'دریافت معاملات امروز ناموفق بود'
  } finally {
    todayTradesLoading.value = false
  }
}

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
      void loadTodayTrades()
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

function openOperations() {
  router.push('/operations')
}

function openAccountHub() {
  router.push('/account')
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
    <AppLoadingState v-if="loading" class="ds-loading-state" label="در حال دریافت داشبورد" />

    <div v-else-if="user" class="dashboard-content">

      <!-- ═══ Top Bar ═══ -->
      <header class="top-bar">
        <!-- Notifications on the far Right (in RTL) -->
        <button type="button" class="ds-icon-btn notif-btn" @click="router.push('/notifications')" aria-label="اعلان‌ها">
          <Bell :size="22" />
          <div v-if="notificationStore.appNotifications.length > 0" class="notif-dot"></div>
        </button>

        <!-- User Info in the Center -->
        <button
          type="button"
          class="user-info-center"
          @click="router.push('/profile')"
          :aria-label="`مشاهده پروفایل ${user.full_name || user.account_name}`"
        >
          <div class="avatar">
            <span>{{ userInitial }}</span>
          </div>
          <div class="user-text">
            <span class="greeting">{{ greeting }}</span>
            <span class="user-name">{{ user.full_name || user.account_name }}</span>
          </div>
        </button>

        <!-- Logout on the far Left (in RTL) -->
        <button v-if="!isAccountant" type="button" class="ds-icon-btn logout-btn" @click="logout" aria-label="خروج">
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

      <section class="dashboard-status-strip" aria-label="خلاصه وضعیت روزانه">
        <div class="dashboard-status-copy">
          <strong>{{ accountStateLabel }}</strong>
          <p>{{ accountStateDescription }}</p>
        </div>
        <div class="dashboard-status-badges">
          <AppStatusBadge :tone="accountStateTone">
            وضعیت حساب: {{ accountStateLabel }}
          </AppStatusBadge>
          <AppStatusBadge :tone="marketStatusTone">
            {{ isAccountant ? 'بازار: مشاهده محدود' : `بازار: ${marketEntryStatusLabel}` }}
          </AppStatusBadge>
          <AppStatusBadge :tone="todayActivityTone">
            {{ todayTradesLoading ? 'کار امروز: در حال دریافت' : `کار امروز: ${todayTradesCountLabel}` }}
          </AppStatusBadge>
          <AppStatusBadge v-if="notificationStore.appNotifications.length > 0" tone="info">
            {{ notificationCountLabel }}
          </AppStatusBadge>
        </div>
      </section>

      <!-- ═══ Main Content ═══ -->
      <main class="main-section">

        <!-- Market Entry — Hero Button -->
        <button
          v-if="!isAccountant"
          type="button"
          class="hero-btn"
          :class="{ 'hero-btn--open': isMarketOpen, 'hero-btn--closed': isMarketClosed }"
          :disabled="isInactiveAccount"
          @click="openMarket"
        >
          <div class="hero-btn-bg"></div>
          <div class="hero-btn-content">
            <div class="hero-icon">
              <Store :size="32" />
            </div>
            <div class="hero-text">
              <span class="hero-title-row">
                <span class="hero-title">ورود به بازار</span>
                <span class="hero-status-pill" :class="isMarketOpen ? 'hero-status-pill--open' : 'hero-status-pill--closed'">
                  {{ marketEntryStatusLabel }}
                </span>
              </span>
              <span class="hero-subtitle">{{ marketEntrySubtitle }}</span>
            </div>
          </div>
          <div class="hero-arrow">←</div>
        </button>

        <section class="dashboard-shortcuts" aria-label="میانبرهای اصلی">
          <button type="button" class="dashboard-shortcut-card" @click="openOperations">
            <span class="shortcut-icon">
              <BriefcaseBusiness :size="20" />
            </span>
            <span class="shortcut-copy">
              <strong>عملیات</strong>
              <small>مشتریان، حسابداران و مدیریت</small>
            </span>
            <ChevronLeft :size="18" class="shortcut-chevron" />
          </button>

          <button type="button" class="dashboard-shortcut-card" @click="openAccountHub">
            <span class="shortcut-icon">
              <UserRound :size="20" />
            </span>
            <span class="shortcut-copy">
              <strong>حساب</strong>
              <small>پروفایل، تنظیمات و اعلان‌ها</small>
            </span>
            <ChevronLeft :size="18" class="shortcut-chevron" />
          </button>
        </section>

        <button
          v-if="canUseTestAccountSwitcher"
          class="switcher-entry-btn"
          type="button"
          @click="openAccountSwitchModal"
        >
          <span class="switcher-entry-icon"><Users :size="20" /></span>
          <span class="switcher-entry-text">
            <strong>سوییچ حساب</strong>
            <small>بدون OTP و خروج، بین اکانت‌های موجود جابه‌جا شوید</small>
          </span>
        </button>

        <AppSectionCard
          class="today-trades-card"
          title="معاملات امروز"
          description="تاریخچه روز جاری بر اساس زمان ایران"
          aria-label="تاریخچه معاملات امروز"
        >
          <template #actions>
            <button
              type="button"
              class="today-trades-refresh"
              :disabled="todayTradesLoading"
              @click="loadTodayTrades"
            >
              بروزرسانی
            </button>
          </template>

          <div v-if="todayTradesLoading" class="today-trades-state">در حال دریافت معاملات...</div>
          <div v-else-if="todayTradesError" class="today-trades-state today-trades-state--error">
            {{ todayTradesError }}
          </div>
          <div v-else-if="todayTrades.length === 0" class="today-trades-state">
            امروز معامله‌ای ثبت نشده است.
          </div>
          <div v-else class="today-trades-scroll">
            <div class="today-trades-table" role="table" aria-label="معاملات امروز">
              <div class="today-trades-row today-trades-row--head" role="row">
                <span role="columnheader">طرف مقابل معامله</span>
                <span role="columnheader">نوع معامله</span>
                <span role="columnheader">کالا</span>
                <span role="columnheader">تعداد</span>
                <span role="columnheader">فی</span>
              </div>
              <div v-for="trade in todayTrades" :key="trade.id" class="today-trades-row" role="row">
                <span class="today-trades-counterparty" role="cell">{{ getTradeCounterpartyLabel(trade) }}</span>
                <span role="cell">
                  <span class="today-trade-type" :class="`today-trade-type--${getTradeTypeForPerspective(trade)}`">
                    {{ getTradeTypeLabel(trade) }}
                  </span>
                </span>
                <span role="cell">{{ trade.commodity_name || 'نامشخص' }}</span>
                <span role="cell">{{ formatDashboardNumber(trade.quantity) }}</span>
                <span role="cell">{{ formatDashboardNumber(trade.price) }}</span>
              </div>
            </div>
          </div>
        </AppSectionCard>

      </main>

      <!-- Footer -->
      <footer class="dashboard-footer">
        <span>نسخه ۲.۵.۰</span>
      </footer>

    </div>



  </div>

  <div v-if="isSwitchModalOpen" class="switcher-modal-backdrop" @click.self="closeAccountSwitchModal">
    <div class="switcher-modal" role="dialog" aria-modal="true" aria-label="سوییچ حساب">
      <div class="switcher-modal-header">
        <div>
          <h3>سوییچ حساب</h3>
          <p>حساب فعلی: <strong>{{ currentSwitchUserLabel }}</strong></p>
        </div>
        <button type="button" class="switcher-close-btn" aria-label="بستن سوییچ حساب" @click="closeAccountSwitchModal">×</button>
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
  min-width: 0;
  padding: 0.25rem;
  border: 0;
  border-radius: var(--ds-radius-lg);
  background: transparent;
  font: inherit;
  cursor: pointer;
  text-align: right;
  -webkit-tap-highlight-color: transparent;
}

.user-info-center:focus-visible,
.notif-btn:focus-visible,
.logout-btn:focus-visible,
.hero-btn:focus-visible,
.dashboard-shortcut-card:focus-visible,
.switcher-entry-btn:focus-visible,
.today-trades-refresh:focus-visible,
.switcher-close-btn:focus-visible,
.switcher-user-row:focus-visible {
  outline: 3px solid rgba(245, 158, 11, 0.34);
  outline-offset: 3px;
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

.dashboard-status-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 1rem;
  padding: 0.85rem 1rem;
  border: 1px solid var(--ds-border-subtle);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  box-shadow: var(--ds-shadow-xs);
}

.dashboard-status-copy {
  min-width: 0;
  display: grid;
  gap: 0.2rem;
}

.dashboard-status-copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 850;
  line-height: 1.5;
}

.dashboard-status-copy p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.dashboard-status-badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.45rem;
}

.dashboard-stat-card {
  min-width: 0;
}

.dashboard-overview :deep(.ui-metric-card) {
  min-height: 100%;
}

.dashboard-overview :deep(.ui-metric-card__label) {
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  font-weight: 800;
}

.dashboard-overview :deep(.ui-metric-card__value) {
  min-width: 0;
  color: var(--ds-text-primary);
  font-size: 0.98rem;
  font-weight: 900;
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.dashboard-overview :deep(.ui-metric-card__hint) {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.65;
}

.dashboard-stat-card--warning {
  border-color: rgba(217, 119, 6, 0.24);
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.95), var(--ds-bg-card));
}

.dashboard-stat-card--danger {
  border-color: rgba(220, 38, 38, 0.2);
  background: linear-gradient(135deg, rgba(254, 242, 242, 0.95), var(--ds-bg-card));
}

.dashboard-stat-card--primary {
  border-color: rgba(245, 158, 11, 0.2);
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.92), var(--ds-bg-card));
}

.dashboard-shortcuts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.dashboard-shortcut-card {
  min-width: 0;
  min-height: 86px;
  display: grid;
  grid-template-columns: 38px 1fr 18px;
  align-items: center;
  gap: 0.55rem;
  padding: 0.75rem;
  border-radius: var(--ds-radius-lg);
  border: 1px solid var(--ds-border-accent);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  box-shadow: var(--ds-shadow-sm);
  font: inherit;
  text-align: right;
  cursor: pointer;
  transition: transform 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}

.dashboard-shortcut-card:active {
  transform: scale(0.985);
  background: var(--ds-primary-50);
}

.shortcut-icon {
  width: 38px;
  height: 38px;
  border-radius: var(--ds-radius-md);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
}

.shortcut-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.18rem;
}

.shortcut-copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 850;
  line-height: 1.35;
}

.shortcut-copy small {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.55;
}

.shortcut-chevron {
  color: var(--ds-text-placeholder);
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

@media (max-width: 380px) {
  .dashboard-overview,
  .dashboard-shortcuts {
    grid-template-columns: 1fr;
  }
}

@media (min-width: 381px) and (max-width: 680px) {
  .dashboard-status-strip {
    flex-direction: column;
    align-items: stretch;
  }

  .dashboard-status-badges {
    justify-content: flex-start;
  }
}

/* ═══ Today Trades ═══ */
.today-trades-card {
  width: 100%;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.08);
  overflow: hidden;
}

.today-trades-card :deep(.ui-section-card__body) {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.today-trades-refresh {
  flex-shrink: 0;
  border: 1px solid rgba(245, 158, 11, 0.22);
  border-radius: 12px;
  background: rgba(245, 158, 11, 0.1);
  color: #b45309;
  font: inherit;
  font-size: 0.78rem;
  font-weight: 800;
  padding: 0.45rem 0.75rem;
  cursor: pointer;
}

.today-trades-refresh:disabled {
  opacity: 0.58;
  cursor: default;
}

.today-trades-state {
  padding: 1.15rem;
  color: var(--ds-text-secondary);
  font-size: 0.86rem;
  line-height: 1.7;
}

.today-trades-state--error {
  color: var(--ds-danger-600);
}

.today-trades-scroll {
  overflow-x: auto;
  overscroll-behavior-x: contain;
}

.today-trades-table {
  min-width: 620px;
  display: flex;
  flex-direction: column;
}

.today-trades-row {
  display: grid;
  grid-template-columns: minmax(150px, 1.5fr) minmax(88px, 0.72fr) minmax(116px, 1fr) minmax(76px, 0.62fr) minmax(96px, 0.76fr);
  align-items: center;
  column-gap: 0.65rem;
  padding: 0.72rem 1rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.12);
  color: var(--ds-text-primary);
  font-size: 0.82rem;
}

.today-trades-row:last-child {
  border-bottom: none;
}

.today-trades-row--head {
  background: rgba(248, 250, 252, 0.94);
  color: var(--ds-text-secondary);
  font-size: 0.74rem;
  font-weight: 800;
}

.today-trades-row > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.today-trades-counterparty {
  font-weight: 700;
}

.today-trade-type {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 52px;
  padding: 0.18rem 0.48rem;
  border-radius: 999px;
  font-size: 0.74rem;
  font-weight: 800;
}

.today-trade-type--buy {
  background: rgba(22, 163, 74, 0.12);
  color: #15803d;
}

.today-trade-type--sell {
  background: rgba(220, 38, 38, 0.1);
  color: #b91c1c;
}

.today-trade-type--unknown {
  background: rgba(148, 163, 184, 0.16);
  color: var(--ds-text-secondary);
}

/* ═══ Hero Button ═══ */
.hero-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 126px;
  padding: 1.5rem;
  border-radius: var(--ds-radius-xl);
  border: none;
  cursor: pointer;
  overflow: hidden;
  -webkit-tap-highlight-color: transparent;
  box-shadow: 0 16px 36px rgba(15, 23, 42, 0.14);
  transition: transform 0.2s, box-shadow 0.2s;
}
.hero-btn:active {
  transform: scale(0.98);
}
.hero-btn:hover {
  box-shadow: 0 18px 42px rgba(15, 23, 42, 0.18);
}

.hero-btn-bg {
  position: absolute;
  inset: 0;
  background: var(--ds-gradient-primary);
}
.hero-btn--open .hero-btn-bg {
  background: linear-gradient(135deg, #f59e0b 0%, #d97706 58%, #16a34a 125%);
}
.hero-btn--closed .hero-btn-bg {
  background: linear-gradient(135deg, #991b1b 0%, #dc2626 54%, #334155 128%);
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
  min-width: 0;
  flex: 1;
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
  min-width: 0;
}
.hero-title-row {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  flex-wrap: wrap;
}
.hero-title {
  font-size: var(--ds-font-2xl);
  font-weight: 800;
  color: white;
  line-height: 1.25;
}
.hero-status-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.55rem;
  padding: 0.18rem 0.65rem;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.32);
  color: white;
  font-size: 0.72rem;
  font-weight: 800;
  white-space: nowrap;
}
.hero-status-pill--open {
  background: rgba(22, 163, 74, 0.38);
  box-shadow: inset 0 0 0 1px rgba(187, 247, 208, 0.22);
}
.hero-status-pill--closed {
  background: rgba(254, 226, 226, 0.2);
  box-shadow: inset 0 0 0 1px rgba(254, 202, 202, 0.18);
}
.hero-subtitle {
  font-size: var(--ds-font-sm);
  color: rgba(255,255,255,0.84);
  margin-top: 0.15rem;
  font-weight: 500;
  line-height: 1.55;
}

.hero-arrow {
  position: relative;
  z-index: 1;
  color: rgba(255,255,255,0.6);
  font-size: 1.5rem;
  font-weight: 300;
  flex-shrink: 0;
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
