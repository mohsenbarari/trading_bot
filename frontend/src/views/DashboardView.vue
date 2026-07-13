<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, Store, LogOut, AlertTriangle, Ban, ChevronDown, PackageCheck, UsersRound } from 'lucide-vue-next'
import { useNotificationStore } from '../stores/notifications'
import { apiFetch, forceLogout } from '../utils/auth'
import { cacheCurrentUserSummary } from '../utils/currentUser'
import { formatIranDateTime, getIranHour, IRAN_TIME_ZONE, parseIranDisplayDate } from '../utils/iranTime'
import { marketRuntime } from '../composables/useMarketRuntime'
import { openTelegramLink, requestTelegramLink } from '../services/telegramLink'
import type { SettlementType } from '../utils/settlementType'
import TelegramConnectPanel from '../components/account/TelegramConnectPanel.vue'
import {
  AppButton,
  AppCard,
  AppChip,
  AppDisclosure,
  AppEmptyState,
  AppErrorState,
  AppIconButton,
  AppInput,
  AppListItem,
  AppLoadingState,
  AppSettlementBadge,
  AppSectionCard,
  AppStatusBadge,
  AppToast,
} from '../components/ui'

interface DashboardTrade {
  id: number
  trade_type: string
  settlement_type: SettlementType
  commodity_name: string
  quantity: number
  price: number
  offer_user_id: number | null
  responder_user_id: number | null
  offer_user_name?: string | null
  responder_user_name?: string | null
  counterparty_name?: string | null
}

interface DashboardCommodityAlias {
  id?: number | null
  alias?: string | null
}

interface DashboardCommodity {
  id: number
  name: string
  aliases: DashboardCommodityAlias[]
}

interface DashboardProjectUser {
  id: number
  account_name: string
  mobile_number?: string | null
  created_at?: string | null
}

const PROJECT_USERS_PAGE_SIZE = 25
const PROJECT_USER_NEW_WINDOW_MS = 7 * 24 * 60 * 60 * 1000

const router = useRouter()
const notificationStore = useNotificationStore()
const user = ref<any>(null)
const loading = ref(true)
const todayTrades = ref<DashboardTrade[]>([])
const todayTradesLoading = ref(false)
const todayTradesError = ref('')
const allowedCommodities = ref<DashboardCommodity[]>([])
const allowedCommoditiesLoading = ref(false)
const allowedCommoditiesError = ref('')
const allowedCommoditiesExpanded = ref(false)
const allowedCommoditiesLoaded = ref(false)
const projectUsersExpanded = ref(false)
const projectUsers = ref<DashboardProjectUser[]>([])
const projectUsersLoading = ref(false)
const projectUsersLoadingMore = ref(false)
const projectUsersError = ref('')
const projectUsersQuery = ref('')
const projectUsersLoaded = ref(false)
const lastLoadedProjectUsersQuery = ref('')
const projectUsersOffset = ref(0)
const projectUsersHasMore = ref(false)
const telegramLinkBusy = ref(false)
const telegramLinkError = ref('')

const isRestricted = computed(() => {
  if (!user.value?.trading_restricted_until) return false
  const restrictedUntil = parseIranDisplayDate(user.value.trading_restricted_until)
  return Boolean(restrictedUntil && restrictedUntil > new Date())
})

const isInactiveAccount = computed(() => user.value?.account_status === 'inactive')
const isAccountant = computed(() => user.value?.is_accountant === true)
const isCustomer = computed(() => user.value?.is_customer === true || Boolean(user.value?.customer_tier))
const showTelegramConnectCard = computed(() => (
  Boolean(user.value?.can_connect_telegram)
  && user.value?.telegram_linked !== true
  && !isInactiveAccount.value
))
const isMarketOpen = computed(() => marketRuntime.value.is_open)
const isMarketClosed = computed(() => !isMarketOpen.value)
const marketEntryStatusLabel = computed(() => (isMarketOpen.value ? 'بازار باز' : 'بازار بسته'))
const marketEntrySubtitle = computed(() => (
  isMarketOpen.value
    ? 'مشاهده و ثبت لفظ‌های خرید و فروش'
    : 'فعلاً امکان ثبت لفظ جدید وجود ندارد'
))

const isGloballyLockedAccount = computed(() => Boolean(user.value?.global_web_locked_at))
const showAllowedCommoditiesSection = computed(() => {
  if (!user.value) return false
  if (isAccountant.value) return false
  return user.value.customer_tier !== 'tier2'
})

const projectUsersDirectoryTargetId = computed(() => {
  if (!user.value || isCustomer.value) return null
  const accountantOwnerUserId = Number(user.value.accountant_owner_user_id)
  if (isAccountant.value && Number.isInteger(accountantOwnerUserId) && accountantOwnerUserId > 0) {
    return accountantOwnerUserId
  }
  const currentUserId = Number(user.value.id)
  return Number.isInteger(currentUserId) && currentUserId > 0 ? currentUserId : null
})

const showProjectUsersSection = computed(() => projectUsersDirectoryTargetId.value !== null)

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
const dashboardHeaderTone = computed(() => {
  if (isInactiveAccount.value) return 'danger'
  if (isRestricted.value) return 'warning'
  return 'primary'
})
const dashboardHeaderStatusSummary = computed(() => {
  const marketLabel = isAccountant.value ? 'مشاهده محدود بازار' : marketEntryStatusLabel.value
  const tradesLabel = todayTradesLoading.value ? 'کار امروز در حال دریافت است' : `کار امروز ${todayTradesCountLabel.value}`
  return [accountStateDescription.value, marketLabel, tradesLabel].filter(Boolean).join(' • ')
})

const greeting = computed(() => {
  const hour = getIranHour()
  if (hour < 12) return 'صبح بخیر'
  if (hour < 17) return 'ظهر بخیر'
  return 'عصر بخیر'
})

const currentUserDisplayName = computed(() => {
  if (!user.value) return ''
  return user.value.customer_management_name || user.value.full_name || user.value.account_name || ''
})

const userInitial = computed(() => {
  if (!user.value) return ''
  const name = currentUserDisplayName.value
  return name ? name[0] : '?'
})

const allowedCommodityCountLabel = computed(() => `${formatDashboardNumber(allowedCommodities.value.length)} کالا`)
const allowedCommoditySummaryLabel = computed(() => {
  if (!allowedCommoditiesLoaded.value && !allowedCommoditiesLoading.value) return 'باز کنید'
  if (allowedCommoditiesLoading.value) return 'در حال دریافت'
  if (allowedCommoditiesError.value) return 'خطا'
  return allowedCommodityCountLabel.value
})
const projectUsersCountLabel = computed(() => `${formatDashboardNumber(projectUsers.value.length)} همکار`)
const projectUsersSummaryLabel = computed(() => {
  if (!projectUsersLoaded.value && !projectUsersLoading.value) return 'باز کنید'
  if (projectUsersLoading.value) return 'در حال دریافت'
  return projectUsersCountLabel.value
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

function normalizeCommodityAliasLabel(alias: unknown) {
  if (typeof alias === 'string') return alias.trim()
  if (alias && typeof alias === 'object' && 'alias' in alias) {
    const value = (alias as DashboardCommodityAlias).alias
    return typeof value === 'string' ? value.trim() : ''
  }
  return ''
}

function getCommodityAliasLabels(commodity: DashboardCommodity) {
  const aliases = Array.isArray(commodity.aliases)
    ? commodity.aliases
        .map((alias) => normalizeCommodityAliasLabel(alias))
        .filter(Boolean)
    : []
  return Array.from(new Set(aliases.filter((alias) => alias !== commodity.name)))
}

function parseDashboardApiError(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) return detail
    if (Array.isArray(detail) && detail.length > 0) return fallback
  }
  return fallback
}

async function loadAllowedCommodities() {
  if (!showAllowedCommoditiesSection.value) {
    allowedCommodities.value = []
    allowedCommoditiesError.value = ''
    allowedCommoditiesLoading.value = false
    allowedCommoditiesLoaded.value = false
    return
  }
  allowedCommoditiesLoading.value = true
  allowedCommoditiesError.value = ''

  try {
    const response = await apiFetch('/api/commodities/')
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail || 'دریافت فهرست کالاها ناموفق بود')
    }
    allowedCommodities.value = Array.isArray(payload)
      ? payload
          .map((commodity) => {
            const id = Number((commodity as { id?: unknown }).id)
            const name = typeof (commodity as { name?: unknown }).name === 'string'
              ? (commodity as { name: string }).name.trim()
              : ''
            if (!Number.isInteger(id) || id <= 0 || !name) return null
            return {
              id,
              name,
              aliases: Array.isArray((commodity as { aliases?: unknown[] }).aliases)
                ? ((commodity as { aliases?: unknown[] }).aliases as DashboardCommodityAlias[])
                : [],
            } satisfies DashboardCommodity
          })
          .filter((commodity): commodity is DashboardCommodity => commodity !== null)
      : []
    allowedCommoditiesLoaded.value = true
  } catch (error: any) {
    allowedCommodities.value = []
    allowedCommoditiesError.value = error?.message || 'دریافت فهرست کالاها ناموفق بود'
    allowedCommoditiesLoaded.value = true
  } finally {
    allowedCommoditiesLoading.value = false
  }
}

function normalizeProjectUser(raw: unknown): DashboardProjectUser | null {
  if (!raw || typeof raw !== 'object') return null
  const id = Number((raw as { id?: unknown }).id)
  const accountName = typeof (raw as { account_name?: unknown }).account_name === 'string'
    ? (raw as { account_name: string }).account_name.trim()
    : ''
  if (!Number.isInteger(id) || id <= 0 || !accountName) return null
  const mobileNumber = typeof (raw as { mobile_number?: unknown }).mobile_number === 'string'
    ? (raw as { mobile_number: string }).mobile_number.trim()
    : ''
  const createdAt = typeof (raw as { created_at?: unknown }).created_at === 'string'
    ? (raw as { created_at: string }).created_at.trim()
    : ''
  return {
    id,
    account_name: accountName,
    mobile_number: mobileNumber || null,
    created_at: createdAt || null,
  }
}

function isProjectUserNew(projectUser: DashboardProjectUser) {
  if (!projectUser.created_at) return false
  const createdAtTime = new Date(projectUser.created_at).getTime()
  if (!Number.isFinite(createdAtTime)) return false
  const ageMs = Date.now() - createdAtTime
  return ageMs >= 0 && ageMs <= PROJECT_USER_NEW_WINDOW_MS
}

async function loadProjectUsersDirectory(force = false) {
  const targetId = projectUsersDirectoryTargetId.value
  const normalizedQuery = projectUsersQuery.value.trim()
  const isLoadMore = !force && projectUsersOffset.value > 0
  if (
    targetId === null
    || projectUsersLoading.value
    || projectUsersLoadingMore.value
    || (!force && !isLoadMore && projectUsersLoaded.value && lastLoadedProjectUsersQuery.value === normalizedQuery)
  ) {
    return
  }

  if (isLoadMore) {
    projectUsersLoadingMore.value = true
  } else {
    projectUsersLoading.value = true
  }
  projectUsersError.value = ''

  try {
    const params = new URLSearchParams()
    params.set('limit', String(PROJECT_USERS_PAGE_SIZE))
    params.set('offset', String(isLoadMore ? projectUsersOffset.value : 0))
    if (normalizedQuery) params.set('q', normalizedQuery)
    const response = await apiFetch(`/api/users-public/${targetId}/project-users?${params.toString()}`)
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseDashboardApiError(payload, 'خطا در دریافت لیست همکاران'))
    }

    const rawRows = Array.isArray(payload) ? payload : []
    const nextRows = rawRows
      .map((entry) => normalizeProjectUser(entry))
      .filter((entry): entry is DashboardProjectUser => entry !== null && entry.id !== targetId)
    if (isLoadMore) {
      const existingIds = new Set(projectUsers.value.map((entry) => entry.id))
      projectUsers.value = [
        ...projectUsers.value,
        ...nextRows.filter((entry) => !existingIds.has(entry.id)),
      ]
    } else {
      projectUsers.value = nextRows
    }
    projectUsersLoaded.value = true
    lastLoadedProjectUsersQuery.value = normalizedQuery
    projectUsersHasMore.value = rawRows.length === PROJECT_USERS_PAGE_SIZE
    projectUsersOffset.value += rawRows.length
  } catch (error: any) {
    projectUsersError.value = error?.message || 'خطا در دریافت لیست همکاران'
  } finally {
    projectUsersLoading.value = false
    projectUsersLoadingMore.value = false
  }
}

function resetProjectUsersDirectoryState() {
  projectUsers.value = []
  projectUsersLoading.value = false
  projectUsersLoadingMore.value = false
  projectUsersError.value = ''
  projectUsersLoaded.value = false
  lastLoadedProjectUsersQuery.value = ''
  projectUsersOffset.value = 0
  projectUsersHasMore.value = false
}

async function submitProjectUsersSearch() {
  resetProjectUsersDirectoryState()
  await loadProjectUsersDirectory(true)
}

async function loadMoreProjectUsers() {
  if (projectUsersLoading.value || projectUsersLoadingMore.value || !projectUsersHasMore.value) return
  await loadProjectUsersDirectory()
}

async function toggleProjectUsersDirectory() {
  projectUsersExpanded.value = !projectUsersExpanded.value
  if (projectUsersExpanded.value && !projectUsersLoaded.value && !projectUsersLoading.value) {
    await loadProjectUsersDirectory(true)
  }
}

async function toggleAllowedCommodities() {
  allowedCommoditiesExpanded.value = !allowedCommoditiesExpanded.value
  if (allowedCommoditiesExpanded.value && !allowedCommoditiesLoaded.value && !allowedCommoditiesLoading.value) {
    await loadAllowedCommodities()
  }
}

function openProjectUserProfile(projectUser: DashboardProjectUser) {
  router.push({
    name: 'public-profile',
    params: { id: projectUser.id },
    query: projectUser.account_name ? { account_name: projectUser.account_name } : undefined,
  })
}

async function fetchUser() {
  try {
    const res = await apiFetch('/api/auth/me')
    if (res.ok) {
      user.value = await res.json()
      cacheCurrentUserSummary(user.value)
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

async function connectTelegram() {
  if (telegramLinkBusy.value) return
  telegramLinkBusy.value = true
  telegramLinkError.value = ''
  try {
    const payload = await requestTelegramLink()
    if (payload.telegram_url) {
      openTelegramLink(payload.telegram_url)
      return
    }
    telegramLinkError.value = payload.detail || 'لینک اتصال تلگرام آماده نشد.'
  } catch (error: any) {
    telegramLinkError.value = error?.message || 'ساخت لینک اتصال تلگرام ناموفق بود.'
  } finally {
    telegramLinkBusy.value = false
  }
}

onMounted(fetchUser)
</script>

<template>
  <div class="dashboard-page">
    
    <!-- Loading -->
    <AppLoadingState v-if="loading" class="ds-loading-state" label="در حال دریافت داشبورد" />

    <div v-else-if="user" class="dashboard-content">

      <AppCard class="dashboard-header-card" :tone="dashboardHeaderTone">
        <div class="dashboard-header-main">
          <div class="dashboard-header-actions">
            <AppIconButton type="button" class="notif-btn" label="اعلان‌ها" size="sm" @click="router.push('/notifications')">
              <Bell :size="22" />
              <div v-if="notificationStore.appNotifications.length > 0" class="notif-dot"></div>
            </AppIconButton>
          </div>

        <button
          type="button"
          class="user-info-center"
          @click="router.push('/profile')"
          :aria-label="`مشاهده پروفایل ${currentUserDisplayName}`"
        >
          <div class="avatar">
            <span>{{ userInitial }}</span>
          </div>
          <div class="user-text">
            <span class="greeting">{{ greeting }}</span>
            <span class="user-name">{{ currentUserDisplayName }}</span>
          </div>
        </button>

          <div class="dashboard-header-actions">
            <AppIconButton v-if="!isAccountant" type="button" class="logout-btn" label="خروج" size="sm" @click="logout">
              <LogOut :size="20" />
            </AppIconButton>
          </div>
        </div>

        <div class="dashboard-header-summary">
          <div class="dashboard-header-copy">
            <strong>{{ accountStateLabel }}</strong>
            <p>{{ dashboardHeaderStatusSummary }}</p>
          </div>
          <div class="dashboard-header-badges">
            <AppStatusBadge :tone="accountStateTone">
              {{ accountStateLabel }}
            </AppStatusBadge>
            <AppStatusBadge :tone="marketStatusTone">
              {{ isAccountant ? 'مشاهده محدود' : marketEntryStatusLabel }}
            </AppStatusBadge>
            <AppStatusBadge :tone="todayActivityTone">
              {{ todayTradesLoading ? 'در حال دریافت' : todayTradesCountLabel }}
            </AppStatusBadge>
            <AppStatusBadge v-if="notificationStore.appNotifications.length > 0" tone="info">
              {{ notificationCountLabel }}
            </AppStatusBadge>
          </div>
        </div>
      </AppCard>

      <!-- ═══ Blocked Warning ═══ -->
      <AppToast
        v-if="isInactiveAccount"
        class="dashboard-alert-card alert-blocked"
        tone="danger"
        role="alert"
        :title="isGloballyLockedAccount ? 'حساب کاربری قفل شده است' : 'حساب کاربری غیرفعال شده است'"
        :message="inactiveAccountMessage"
      >
        <template #icon>
          <span class="alert-icon blocked-icon">
            <Ban :size="28" />
          </span>
        </template>
      </AppToast>

      <!-- ═══ Restricted Warning ═══ -->
      <AppToast
        v-else-if="isRestricted"
        class="dashboard-alert-card alert-restricted"
        tone="warning"
        role="alert"
        title="معاملات محدود شده"
      >
        <template #icon>
          <span class="alert-icon restricted-icon">
            <AlertTriangle :size="24" />
          </span>
        </template>
        <p>دسترسی معاملاتی شما تا <strong>{{ restrictedUntil }}</strong> محدود شده است.</p>
      </AppToast>

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
          <div class="hero-btn-content">
            <div class="hero-icon-box">
              <Store :size="32" />
            </div>
            <div class="hero-text">
              <span class="hero-title-row">
                <span class="hero-title">ورود به بازار</span>
                <AppStatusBadge :tone="isMarketOpen ? 'success' : 'neutral'" class="hero-status-pill">
                  {{ marketEntryStatusLabel }}
                </AppStatusBadge>
              </span>
              <span class="hero-subtitle">{{ marketEntrySubtitle }}</span>
            </div>
          </div>
          <div class="hero-cta-tail">ورود</div>
        </button>

        <AppSectionCard
          class="today-trades-card"
          title="معاملات امروز"
          description="تاریخچه روز جاری بر اساس زمان ایران"
          aria-label="تاریخچه معاملات امروز"
        >
          <template #actions>
            <AppButton
              type="button"
              variant="secondary"
              size="sm"
              class="today-trades-refresh"
              :disabled="todayTradesLoading"
              @click="loadTodayTrades"
            >
              بروزرسانی
            </AppButton>
          </template>

          <AppLoadingState
            v-if="todayTradesLoading"
            class="dashboard-state-card"
            label="در حال دریافت معاملات"
          />
          <AppErrorState
            v-else-if="todayTradesError"
            class="dashboard-state-card"
            title="دریافت معاملات امروز ناموفق بود"
            :message="todayTradesError"
          />
          <AppEmptyState
            v-else-if="todayTrades.length === 0"
            class="dashboard-state-card"
            title="امروز معامله‌ای ثبت نشده است"
            message="بعد از انجام معامله، خلاصه روز جاری همین‌جا نمایش داده می‌شود."
          />
          <div v-else class="today-trades-scroll">
            <div class="today-trades-table" role="table" aria-label="معاملات امروز">
              <div class="today-trades-row today-trades-row--head" role="row">
                <span role="columnheader">طرف مقابل معامله</span>
                <span role="columnheader">نوع معامله</span>
                <span role="columnheader">نوع تسویه</span>
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
                <span role="cell">
                  <AppSettlementBadge :settlement-type="trade.settlement_type" context="trade" />
                </span>
                <span role="cell">{{ trade.commodity_name || 'نامشخص' }}</span>
                <span role="cell">{{ formatDashboardNumber(trade.quantity) }}</span>
                <span role="cell">{{ formatDashboardNumber(trade.price) }}</span>
              </div>
            </div>
          </div>
        </AppSectionCard>

        <AppSectionCard
          v-if="showTelegramConnectCard"
          class="telegram-connect-section"
          title="اتصال تلگرام"
          description="دسترسی سریع به امکانات اپ در بستر تلگرام"
        >
          <TelegramConnectPanel
            :loading="telegramLinkBusy"
            :error="telegramLinkError"
            @connect="connectTelegram"
          />
        </AppSectionCard>

        <AppDisclosure
          v-if="showProjectUsersSection"
          class="dashboard-accordion-card dashboard-project-users-card"
          :open="projectUsersExpanded"
          title="لیست همکاران"
          description="اعضای قابل مشاهده پروژه را جستجو و از همین بخش باز کنید."
          title-id="dashboard-project-users-title"
          panel-id="dashboard-project-users-panel"
          toggle-class="dashboard-accordion-toggle dashboard-accordion-toggle--project-users"
          panel-class="dashboard-accordion-panel"
          @toggle="toggleProjectUsersDirectory"
        >
          <template #leading>
            <span class="dashboard-accordion-icon dashboard-accordion-icon--users" aria-hidden="true">
              <UsersRound :size="20" />
            </span>
          </template>
          <template #meta>
            <span class="dashboard-accordion-meta">
              <AppStatusBadge tone="info">{{ projectUsersSummaryLabel }}</AppStatusBadge>
              <ChevronDown class="dashboard-accordion-chevron" :size="18" />
            </span>
          </template>

            <form class="dashboard-project-users-search" @submit.prevent="submitProjectUsersSearch">
              <AppInput
                v-model="projectUsersQuery"
                class="dashboard-project-users-search-input"
                type="search"
                placeholder="جستجو با نام کاربری یا شماره تماس"
              />
              <AppButton type="submit" size="sm" :loading="projectUsersLoading">جستجو</AppButton>
            </form>

            <AppLoadingState
              v-if="projectUsersLoading"
              class="dashboard-state-card"
              label="در حال دریافت لیست همکاران"
            />
            <AppErrorState
              v-else-if="projectUsersError"
              class="dashboard-state-card"
              title="دریافت لیست همکاران ناموفق بود"
              :message="projectUsersError"
            />
            <AppEmptyState
              v-else-if="projectUsersLoaded && projectUsers.length === 0"
              title="همکاری برای نمایش پیدا نشد"
              :message="projectUsersQuery.trim() ? 'همکاری با این جستجو پیدا نشد.' : 'همکاری برای نمایش وجود ندارد.'"
            />
            <div v-else-if="projectUsers.length > 0" class="dashboard-project-users-list">
              <AppListItem
                v-for="projectUser in projectUsers"
                :key="projectUser.id"
                :title="projectUser.account_name"
                interactive
                class="dashboard-project-user-card"
                @select="openProjectUserProfile(projectUser)"
              >
                <template #trailing>
                  <span class="dashboard-project-user-trailing">
                    <AppChip
                      v-if="isProjectUserNew(projectUser)"
                      class="dashboard-project-user-new-badge"
                      tone="success"
                    >
                      جدید
                    </AppChip>
                    <span v-else class="dashboard-project-user-new-badge dashboard-project-user-new-badge--hidden" aria-hidden="true"></span>
                    <span v-if="projectUser.mobile_number" class="dashboard-project-user-mobile" dir="ltr">
                      {{ projectUser.mobile_number }}
                    </span>
                    <span v-else class="dashboard-project-user-mobile dashboard-project-user-mobile--empty">
                      بدون شماره
                    </span>
                  </span>
                </template>
              </AppListItem>
              <div v-if="projectUsersHasMore" class="dashboard-directory-footer">
                <AppButton
                  type="button"
                  variant="secondary"
                  size="sm"
                  class="dashboard-directory-load-more"
                  :loading="projectUsersLoadingMore"
                  @click="loadMoreProjectUsers"
                >
                  نمایش بیشتر
                </AppButton>
              </div>
            </div>
        </AppDisclosure>

        <AppDisclosure
          v-if="showAllowedCommoditiesSection"
          class="dashboard-accordion-card dashboard-commodities-card"
          :open="allowedCommoditiesExpanded"
          title="کالاهای مجاز برای معامله"
          description="کالاهای فعال بازار و نام‌های مستعار ثبت‌شده را ببینید."
          title-id="dashboard-commodities-title"
          panel-id="dashboard-commodities-panel"
          toggle-class="dashboard-accordion-toggle dashboard-accordion-toggle--commodities"
          panel-class="dashboard-accordion-panel"
          @toggle="toggleAllowedCommodities"
        >
          <template #leading>
            <span class="dashboard-accordion-icon dashboard-accordion-icon--commodities" aria-hidden="true">
              <PackageCheck :size="20" />
            </span>
          </template>
          <template #meta>
            <span class="dashboard-accordion-meta">
              <AppStatusBadge tone="info">{{ allowedCommoditySummaryLabel }}</AppStatusBadge>
              <ChevronDown class="dashboard-accordion-chevron" :size="18" />
            </span>
          </template>

            <AppLoadingState
              v-if="allowedCommoditiesLoading"
              class="dashboard-state-card"
              label="در حال دریافت فهرست کالاها"
            />
            <AppErrorState
              v-else-if="allowedCommoditiesError"
              class="dashboard-state-card"
              title="دریافت فهرست کالاها ناموفق بود"
              :message="allowedCommoditiesError"
            />
            <AppEmptyState
              v-else-if="allowedCommodities.length === 0"
              title="هنوز کالایی برای معامله ثبت نشده است"
              message="پس از تعریف کالاها در مدیریت سیستم، فهرست کامل همین‌جا نمایش داده می‌شود."
            />
            <div v-else class="dashboard-commodities-grid">
              <AppCard
                v-for="commodity in allowedCommodities"
                :key="commodity.id"
                class="dashboard-commodity-card"
                :padded="false"
              >
                <div class="dashboard-commodity-head">
                  <strong class="dashboard-commodity-title">{{ commodity.name }}</strong>
                  <AppStatusBadge tone="neutral">
                    {{ formatDashboardNumber(getCommodityAliasLabels(commodity).length) }} نام مستعار
                  </AppStatusBadge>
                </div>
                <p class="dashboard-commodity-caption">نام‌های قابل استفاده برای جستجو و ثبت سریع این کالا</p>
                <div v-if="getCommodityAliasLabels(commodity).length > 0" class="dashboard-commodity-aliases">
                  <AppChip
                    v-for="alias in getCommodityAliasLabels(commodity)"
                    :key="`${commodity.id}-${alias}`"
                    class="dashboard-commodity-alias-chip"
                    tone="primary"
                  >
                    {{ alias }}
                  </AppChip>
                </div>
                <p v-else class="dashboard-commodity-empty">برای این کالا هنوز نام مستعار جداگانه‌ای ثبت نشده است.</p>
              </AppCard>
            </div>
        </AppDisclosure>

      </main>

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
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 3rem);
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

.dashboard-header-card {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
  margin-bottom: 1rem;
  padding: 0.9rem;
}

.dashboard-header-main {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 0.75rem;
}

.dashboard-header-actions {
  display: inline-flex;
  align-items: center;
  justify-content: center;
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
.today-trades-refresh:focus-visible {
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

.notif-btn {
  position: relative;
  color: var(--ds-primary-600);
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

.dashboard-alert-card {
  max-width: none;
  width: 100%;
  margin-bottom: 1rem;
  animation: slideDown 0.4s ease-out;
}

@keyframes slideDown {
  from { opacity: 0; transform: translateY(-10px); }
  to { opacity: 1; transform: translateY(0); }
}

.dashboard-alert-card p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.85;
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

/* ═══ Main Section ═══ */
.main-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.dashboard-header-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.dashboard-header-copy {
  min-width: 0;
  display: grid;
  gap: 0.2rem;
}

.dashboard-header-copy strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 850;
  line-height: 1.5;
}

.dashboard-header-copy p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.dashboard-header-badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.45rem;
}

.dashboard-accordion-card {
  box-shadow: var(--ds-shadow-xs);
}

.dashboard-accordion-card :deep(.dashboard-accordion-toggle) {
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.96));
}

.dashboard-accordion-icon {
  width: 42px;
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--ds-radius-md);
  flex-shrink: 0;
}

.dashboard-accordion-icon--users {
  color: var(--ds-info-500);
  background: rgba(14, 165, 233, 0.1);
}

.dashboard-accordion-icon--commodities {
  color: var(--ds-primary-700);
  background: rgba(245, 158, 11, 0.12);
}

.dashboard-accordion-meta {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 0.5rem;
  min-width: 0;
}

.dashboard-accordion-chevron {
  color: var(--ds-text-secondary);
  transition: transform 0.18s ease;
  flex-shrink: 0;
}

.dashboard-accordion-card.is-open .dashboard-accordion-chevron {
  transform: rotate(180deg);
}

.dashboard-accordion-card :deep(.dashboard-accordion-panel) {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.dashboard-project-users-search {
  display: flex;
  gap: 0.65rem;
  align-items: center;
  flex-wrap: wrap;
  padding-top: 1rem;
}

.dashboard-project-users-search-input {
  flex: 0 1 320px;
  max-width: 100%;
  min-height: 36px;
}

.dashboard-project-users-search :deep(.ui-input) {
  min-height: 36px;
  padding-block: 0.35rem;
  font-size: var(--ds-font-sm);
}

.dashboard-state-card {
  min-height: 6rem;
  box-shadow: none;
}

.dashboard-project-users-list {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.dashboard-project-user-card {
  border-radius: var(--ds-radius-md);
  background: rgba(248, 250, 252, 0.82);
}

.dashboard-project-user-card :deep(.ui-list-item__copy span) {
  display: none;
}

.dashboard-project-user-card :deep(.ui-list-item__trailing) {
  padding-left: 0.35rem;
}

.dashboard-project-user-trailing {
  display: grid;
  grid-template-columns: 3.25rem minmax(6.8rem, auto);
  align-items: center;
  justify-content: end;
  column-gap: 0.6rem;
  min-width: 0;
}

.dashboard-project-user-new-badge {
  min-height: 1.35rem;
  padding: 0.12rem 0.48rem;
  line-height: 1;
  white-space: nowrap;
}

.dashboard-project-user-new-badge--hidden {
  visibility: hidden;
}

.dashboard-project-user-mobile {
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  direction: ltr;
  text-align: left;
  white-space: nowrap;
}

.dashboard-project-user-mobile--empty {
  color: var(--ds-text-placeholder);
}

.dashboard-directory-footer {
  display: flex;
  justify-content: center;
  padding-top: 0.25rem;
}

.dashboard-directory-load-more {
  min-width: 150px;
}

.dashboard-commodities-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.85rem;
}

.dashboard-commodity-card {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  min-width: 0;
  padding: 0.95rem;
}

.dashboard-commodity-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
}

.dashboard-commodity-title {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-md);
  font-weight: 800;
  line-height: 1.6;
}

.dashboard-commodity-caption,
.dashboard-commodity-empty {
  margin: 0;
  font-size: var(--ds-font-xs);
  line-height: 1.8;
}

.dashboard-commodity-caption {
  color: var(--ds-text-secondary);
}

.dashboard-commodity-empty {
  color: var(--ds-text-placeholder);
}

.dashboard-commodity-aliases {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

@media (min-width: 381px) and (max-width: 680px) {
  .dashboard-header-summary {
    flex-direction: column;
    align-items: stretch;
  }

  .dashboard-header-badges {
    justify-content: flex-start;
  }
}

@media (max-width: 430px) {
  .dashboard-accordion-card :deep(.dashboard-accordion-toggle) {
    grid-template-columns: auto minmax(0, 1fr);
    align-items: flex-start;
  }

  .dashboard-accordion-meta {
    grid-column: 2;
    justify-content: space-between;
    width: 100%;
  }

  .dashboard-project-users-search {
    align-items: flex-start;
    flex-direction: column;
  }

  .dashboard-project-users-search-input {
    flex: 0 0 auto;
    width: min(100%, 320px);
  }
}

/* ═══ Today Trades ═══ */
.today-trades-card {
  width: 100%;
}

.today-trades-card :deep(.ui-section-card__body) {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.today-trades-refresh {
  flex-shrink: 0;
}

.today-trades-scroll {
  overflow-x: auto;
  overscroll-behavior-x: contain;
}

.today-trades-table {
  min-width: 760px;
  display: flex;
  flex-direction: column;
}

.today-trades-row {
  display: grid;
  grid-template-columns: minmax(150px, 1.5fr) minmax(88px, 0.72fr) minmax(128px, 0.9fr) minmax(116px, 1fr) minmax(76px, 0.62fr) minmax(96px, 0.76fr);
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

.telegram-connect-section {
  border: 1px solid var(--ds-telegram-border);
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
  background: var(--ds-trade-buy-bg);
  color: var(--ds-trade-buy-text);
}

.today-trade-type--sell {
  background: var(--ds-trade-sell-bg);
  color: var(--ds-trade-sell-text);
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
  min-height: 112px;
  padding: 1.1rem 1.15rem;
  border-radius: var(--ds-radius-lg);
  border: 1px solid var(--ds-border-subtle);
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  background: var(--ds-bg-card);
  box-shadow: var(--ds-shadow-sm);
  transition: transform 0.2s, box-shadow 0.2s, border-color 0.2s, background 0.2s;
}
.hero-btn:active {
  transform: scale(0.98);
}
.hero-btn:hover {
  box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.04);
}
.hero-btn--open {
  border-color: var(--ds-market-open-border);
  background: linear-gradient(135deg, rgba(240, 253, 250, 0.98), rgba(255, 255, 255, 0.96));
  box-shadow:
    inset 0 0 0 1px rgba(22, 163, 74, 0.1),
    inset 0 -24px 42px rgba(22, 163, 74, 0.16);
}
.hero-btn--closed {
  border-color: var(--ds-market-closed-border);
  background: linear-gradient(135deg, rgba(254, 242, 242, 0.96), rgba(255, 255, 255, 0.96));
  box-shadow:
    inset 0 0 0 1px rgba(220, 38, 38, 0.1),
    inset 0 -24px 42px rgba(220, 38, 38, 0.14);
}

.hero-btn--open:hover {
  box-shadow:
    inset 0 0 0 1px rgba(22, 163, 74, 0.14),
    inset 0 -28px 48px rgba(22, 163, 74, 0.22);
}

.hero-btn--closed:hover {
  box-shadow:
    inset 0 0 0 1px rgba(220, 38, 38, 0.14),
    inset 0 -28px 48px rgba(220, 38, 38, 0.2);
}

.hero-btn-content {
  display: flex;
  align-items: center;
  gap: 1rem;
  min-width: 0;
  flex: 1;
}

.hero-icon-box {
  width: 56px;
  height: 56px;
  background: rgba(15, 23, 42, 0.04);
  border-radius: var(--ds-radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ds-text-primary);
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
  font-size: 1.05rem;
  font-weight: 900;
  color: var(--ds-text-primary);
  line-height: 1.25;
}
.hero-status-pill {
  min-height: 1.55rem;
  font-size: 0.72rem;
  white-space: nowrap;
}
.hero-subtitle {
  font-size: var(--ds-font-sm);
  color: var(--ds-text-secondary);
  margin-top: 0.15rem;
  font-weight: 500;
  line-height: 1.55;
}

.hero-cta-tail {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2rem;
  padding: 0.25rem 0.7rem;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.1);
  color: var(--ds-primary-700);
  font-size: 0.72rem;
  font-weight: 900;
  flex-shrink: 0;
}

</style>
