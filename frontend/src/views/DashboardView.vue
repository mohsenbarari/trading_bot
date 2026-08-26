<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bell, ChevronDown, LogOut, Store, UserRound } from 'lucide-vue-next'
import { useNotificationStore } from '../stores/notifications'
import { apiFetch, forceLogout, isAppConnecting } from '../utils/auth'
import {
  clearCurrentUserSummary,
  currentUserSummary,
  loadCurrentUserSummary,
  type CurrentUserSummary,
} from '../utils/currentUser'
import { formatIranDateTime, parseIranDisplayDate } from '../utils/iranTime'
import { marketRuntime } from '../composables/useMarketRuntime'
import PWAInstallOverlay from '../components/PWAInstallOverlay.vue'
import DashboardDailySections from '../components/dashboard/DashboardDailySections.vue'
import {
  AppBottomSheet,
  AppButton,
  AppDesignSystemScope,
  AppErrorState,
  AppIconButton,
  AppListItem,
  AppLoadingState,
  AppPage,
  AppStatusBadge,
  AppToast,
} from '../components/ui'

type DashboardUser = CurrentUserSummary
type HomeIdentityState = 'loading' | 'ready' | 'stale' | 'offline' | 'error'

function isDashboardUser(value: CurrentUserSummary | null): value is DashboardUser {
  return Boolean(
    value &&
      value.role.trim() &&
      (value.account_status === 'active' || value.account_status === 'inactive') &&
      typeof value.is_accountant === 'boolean' &&
      typeof value.is_customer === 'boolean' &&
      (value.customer_management_name || value.full_name || value.account_name),
  )
}

const router = useRouter()
const notificationStore = useNotificationStore()
const cachedUser = isDashboardUser(currentUserSummary.value) ? currentUserSummary.value : null
if (currentUserSummary.value && !cachedUser) clearCurrentUserSummary()
const user = ref<DashboardUser | null>(cachedUser)
const identityState = ref<HomeIdentityState>(cachedUser ? 'stale' : 'loading')
const browserOnline = ref(typeof navigator === 'undefined' || navigator.onLine !== false)
const userError = ref('')
const accountMenuOpen = ref(false)
const accountMenuBusy = ref(false)
const accountMenuTrigger = ref<HTMLButtonElement | null>(null)
let userRequestInFlight = false

const loading = computed(() => identityState.value === 'loading')

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
const hasUnreadNotifications = computed(() =>
  notificationStore.appUnreadCount > 0 ||
  notificationStore.appNotifications.some((notification) => notification.is_read !== true),
)

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

const currentUserDisplayName = computed(() => {
  if (!user.value) return ''
  return user.value.customer_management_name || user.value.full_name || user.value.account_name || ''
})

const userInitial = computed(() => currentUserDisplayName.value[0] || '?')

const identityFreshnessText = computed(() => {
  if (!user.value?.cached_at) return 'نسخه ذخیره‌شده قبلی نمایش داده شده است.'
  return `آخرین به‌روزرسانی ذخیره‌شده: ${formatIranDateTime(user.value.cached_at)}`
})

const connectionNoticeState = computed<'offline' | 'stale' | null>(() => {
  if (isAppConnecting.value) return null
  if (!browserOnline.value || identityState.value === 'offline') return 'offline'
  if (identityState.value === 'stale') return 'stale'
  return null
})

const pwaPromptEligible = computed(
  () =>
    Boolean(user.value) &&
    identityState.value === 'ready' &&
    !isAppConnecting.value &&
    !isInactiveAccount.value &&
    !isGloballyLockedAccount.value &&
    !isRestricted.value,
)

async function fetchUser() {
  if (userRequestInFlight) return
  userRequestInFlight = true
  if (!user.value) identityState.value = 'loading'
  userError.value = ''
  try {
    const result = await loadCurrentUserSummary({ force: true })
    if (!isDashboardUser(result.user)) {
      clearCurrentUserSummary()
      user.value = null
      identityState.value = browserOnline.value ? 'error' : 'offline'
      userError.value = 'دریافت اطلاعات خانه انجام نشد. برای ادامه دوباره تلاش کنید.'
      return
    }

    user.value = result.user
    identityState.value =
      result.state === 'ready'
        ? browserOnline.value
          ? 'ready'
          : 'offline'
        : browserOnline.value
          ? 'stale'
          : 'offline'
    if (identityState.value !== 'ready') {
      userError.value = 'اطلاعات تازه حساب دریافت نشد.'
    }
  } catch {
    user.value = isDashboardUser(currentUserSummary.value) ? currentUserSummary.value : null
    identityState.value = browserOnline.value
      ? user.value
        ? 'stale'
        : 'error'
      : 'offline'
    userError.value = 'دریافت اطلاعات خانه انجام نشد. برای ادامه دوباره تلاش کنید.'
  } finally {
    userRequestInFlight = false
  }
}

function handleBrowserOffline() {
  browserOnline.value = false
  identityState.value = 'offline'
  userError.value = 'اتصال اینترنت در دسترس نیست.'
}

function handleBrowserOnline() {
  browserOnline.value = true
  if (identityState.value !== 'offline') return
  identityState.value = user.value ? 'stale' : 'error'
  userError.value = user.value
    ? 'اطلاعات تازه حساب دریافت نشده است.'
    : 'دریافت اطلاعات خانه انجام نشد. برای ادامه دوباره تلاش کنید.'
}

function openMarket() {
  if (isInactiveAccount.value || isAccountant.value) return
  router.push('/market')
}

function closeAccountMenu() {
  accountMenuOpen.value = false
  void nextTick(() => accountMenuTrigger.value?.focus())
}

function toggleAccountMenu() {
  accountMenuOpen.value = !accountMenuOpen.value
}

function openAccountMenuAndFocusFirst() {
  accountMenuOpen.value = true
}

function openProfile() {
  closeAccountMenu()
  void router.push('/profile')
}

async function logout() {
  if (accountMenuBusy.value) return
  accountMenuBusy.value = true
  closeAccountMenu()
  try {
    const response = await apiFetch('/api/sessions/active', { retryNetwork: false })
    const payload = response.ok ? await response.json().catch(() => null) : null
    const currentSession = Array.isArray(payload)
      ? payload.find(
          (session) => session && typeof session === 'object' && session.is_current === true,
        )
      : null
    const sessionId = Number(currentSession?.id)
    if (Number.isInteger(sessionId) && sessionId > 0) {
      await apiFetch(`/api/sessions/${sessionId}`, {
        method: 'DELETE',
        retryNetwork: false,
      })
    }
  } catch {
    // Local logout must finish even when server-side session cleanup is unavailable.
  } finally {
    forceLogout()
  }
}

onMounted(() => {
  window.addEventListener('offline', handleBrowserOffline)
  window.addEventListener('online', handleBrowserOnline)
  void fetchUser()
})

onUnmounted(() => {
  window.removeEventListener('offline', handleBrowserOffline)
  window.removeEventListener('online', handleBrowserOnline)
})
</script>

<template>
  <AppPage class="dashboard-page">
    <AppLoadingState v-if="loading" class="ds-loading-state" label="در حال دریافت خانه" />

    <AppErrorState
      v-else-if="(identityState === 'error' || identityState === 'offline') && !user"
      class="dashboard-identity-error ds-loading-state"
      :title="
        identityState === 'offline'
          ? 'خانه در حالت آفلاین آماده نیست'
          : 'دریافت اطلاعات خانه انجام نشد'
      "
      :message="userError"
    >
      <template v-if="browserOnline" #actions>
        <AppButton type="button" class="dashboard-identity-retry" @click="fetchUser">
          تلاش دوباره
        </AppButton>
      </template>
    </AppErrorState>

    <div v-else-if="user" class="dashboard-content">
      <div class="dashboard-home-top">
        <header
          class="dashboard-header"
          aria-labelledby="dashboard-page-title"
        >
          <div class="dashboard-header-main">
            <div class="dashboard-account-menu">
              <button
                ref="accountMenuTrigger"
                type="button"
                class="user-info-center dashboard-account-menu__trigger"
                aria-haspopup="dialog"
                :aria-expanded="accountMenuOpen"
                :aria-label="`باز کردن منوی حساب ${currentUserDisplayName}`"
                @click="toggleAccountMenu"
                @keydown.down.prevent="openAccountMenuAndFocusFirst"
              >
                <span class="avatar" aria-hidden="true">{{ userInitial }}</span>
                <span class="user-name">{{ currentUserDisplayName }}</span>
                <ChevronDown
                  :size="17"
                  class="dashboard-account-menu__chevron"
                  :class="{ 'is-open': accountMenuOpen }"
                  aria-hidden="true"
                />
              </button>

              <AppBottomSheet
                :open="accountMenuOpen"
                title="حساب"
                close-label="بستن"
                panel-class="dashboard-account-sheet"
                @close="closeAccountMenu"
              >
                <AppListItem
                  title="پروفایل"
                  interactive
                  role="menuitem"
                  @select="openProfile"
                >
                  <template #leading>
                    <UserRound :size="18" aria-hidden="true" />
                  </template>
                </AppListItem>
                <AppListItem
                  title="خروج"
                  class="dashboard-account-menu__logout"
                  interactive
                  role="menuitem"
                  :disabled="accountMenuBusy"
                  @select="logout"
                >
                  <template #leading>
                    <LogOut :size="18" aria-hidden="true" />
                  </template>
                </AppListItem>
              </AppBottomSheet>
            </div>

            <AppIconButton
              type="button"
              class="notif-btn"
              :label="hasUnreadNotifications ? 'اعلان‌های خوانده‌نشده' : 'اعلان‌ها'"
              @click="router.push('/account/notifications')"
            >
              <Bell :size="22" />
              <span
                v-if="hasUnreadNotifications"
                class="notif-dot"
                aria-hidden="true"
              ></span>
            </AppIconButton>
          </div>
          <h1 id="dashboard-page-title" class="dashboard-page-title">خانه</h1>
        </header>

        <AppToast
          v-if="connectionNoticeState"
          class="dashboard-notice dashboard-connectivity-notice"
          tone="warning"
          role="status"
          :title="
            connectionNoticeState === 'offline'
              ? 'اتصال اینترنت در دسترس نیست'
              : 'اطلاعات خانه به‌روز نشد'
          "
          :message="identityFreshnessText"
        >
          <AppButton
            v-if="connectionNoticeState === 'stale'"
            type="button"
            size="sm"
            variant="secondary"
            class="dashboard-identity-retry"
            @click="fetchUser"
          >
            به‌روزرسانی
          </AppButton>
        </AppToast>

        <AppToast
          v-if="isInactiveAccount"
          class="dashboard-notice dashboard-alert-card alert-blocked"
          tone="danger"
          role="alert"
          :title="isGloballyLockedAccount ? 'حساب کاربری قفل شده است' : 'حساب کاربری غیرفعال شده است'"
          :message="inactiveAccountMessage"
        >
          <AppButton
            type="button"
            class="dashboard-account-follow-up"
            @click="router.push({ name: 'account' })"
          >
            پیگیری در حساب
          </AppButton>
        </AppToast>

        <AppToast
          v-else-if="isRestricted"
          class="dashboard-notice dashboard-alert-card alert-restricted"
          tone="warning"
          role="alert"
          title="معاملات موقتاً محدود است"
          :message="`دسترسی معاملاتی شما تا ${restrictedUntil} محدود شده است.`"
        />
      </div>

      <section class="main-section">

        <template v-if="!isInactiveAccount">
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
        </template>

        <DashboardDailySections :user="user" />

        <AppDesignSystemScope as="div" class="ui-v2-pwa-section">
          <PWAInstallOverlay :eligible="pwaPromptEligible" />
        </AppDesignSystemScope>
      </section>
    </div>
  </AppPage>
</template>

<style scoped>
.dashboard-page {
  position: relative;
}

.user-name {
  min-width: 0;
  overflow: visible;
  text-overflow: unset;
  white-space: normal;
  overflow-wrap: anywhere;
}

.dashboard-content {
  width: 100%;
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  padding: var(--ds-page-padding);
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 3rem);
}

.main-section {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: var(--ds-page-padding);
}

.hero-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.user-info-center:focus-visible,
.notif-btn:focus-visible,
.logout-btn:focus-visible,
.hero-btn:focus-visible,
.today-trades-refresh:focus-visible {
  outline: 3px solid rgba(245, 158, 11, 0.34);
  outline-offset: 3px;
}

.dashboard-account-menu {
  position: relative;
  min-width: 0;
}

.dashboard-account-menu__trigger {
  max-width: min(18rem, calc(100vw - 7rem));
}

.dashboard-account-menu__chevron {
  flex: none;
  transition: transform 0.18s ease;
}

.dashboard-account-menu__chevron.is-open {
  transform: rotate(180deg);
}

.dashboard-account-sheet :deep(.ui-list-item) {
  padding-inline: 0.25rem;
}

.dashboard-account-menu__logout {
  color: var(--ds-danger-700);
}

@media (prefers-reduced-motion: reduce) {
  .dashboard-account-menu__chevron {
    transition-duration: 1ms;
  }
}

.dashboard-home-top {
  display: grid;
  width: 100%;
  gap: var(--ds-page-padding);
  margin-block-end: var(--ds-page-padding);
}

.dashboard-header {
  display: grid;
  gap: 0.75rem;
}

.dashboard-header-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.user-info-center {
  display: inline-flex;
  min-width: 0;
  min-height: var(--ds-native-row-min-height);
  align-items: center;
  gap: 0.5rem;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ds-text-primary);
  font: inherit;
  text-align: start;
}

.avatar {
  display: inline-flex;
  width: var(--ds-native-row-min-height);
  height: var(--ds-native-row-min-height);
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  border-radius: var(--ds-radius-full);
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
  font-size: var(--ds-font-lg);
  font-weight: 750;
}

.dashboard-page-title {
  margin: 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-native-title-size);
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.2;
}

.dashboard-notice {
  box-sizing: border-box;
  max-width: none;
  width: 100%;
  box-shadow: none;
  padding: 0.7rem 0.85rem;
  border-radius: var(--ds-inset-group-radius, 12px);
}

.dashboard-notice :deep(.ui-button) {
  min-width: 0;
  margin-block-start: 0.4rem;
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
