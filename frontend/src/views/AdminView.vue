<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AppBackButton } from '../components/ui'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import { routeRequestJson } from '../utils/routeRequest'
import { isAppHttpError } from '../utils/httpErrorPolicy'
import AdminPanel from '../components/AdminPanel.vue'
import UserManager from '../components/UserManager.vue'
import CommodityManager from '../components/CommodityManager.vue'
import TradingSettings from '../components/TradingSettings.vue'
import AdminMessagesView from '../components/AdminMessagesView.vue'
import CreateInvitationView from '../components/CreateInvitationView.vue'
import CreateChannelView from '../components/CreateChannelView.vue'
import UserProfile from '../components/UserProfile.vue'
import AppButton from '../components/ui/AppButton.vue'
import AppErrorState from '../components/ui/AppErrorState.vue'
import AppLoadingState from '../components/ui/AppLoadingState.vue'
import AppPage from '../components/ui/AppPage.vue'
import AppPageHeader from '../components/ui/AppPageHeader.vue'
import AppSectionCard from '../components/ui/AppSectionCard.vue'
import { isCachedMiddleManager, isCachedSuperAdmin } from '../utils/adminAccess'

const router = useRouter()
const route = useRoute()
const currentSection = ref('menu')
const jwtToken = ref<string | null>(null)
const apiBaseUrl = '' // Relative path for proxy
const selectedUserForProfile = ref<any>(null)
const isLoadingRouteUserProfile = ref(false)
const routeUserProfileError = ref<{ title: string; message: string } | null>(null)
let routeUserProfileRequestSequence = 0
let routeUserProfileAbortController: AbortController | null = null
let routeUserProfileInFlightId: number | null = null
let routeUserProfileLoadedId: number | null = null
// Search terms can match account names and mobile numbers. Keep that state in
// this component only; route context is deliberately limited to scroll offset.
const userDirectoryQuery = ref('')
const userDirectoryScrollTop = ref(0)
let userDirectoryScrollTarget: HTMLElement | Window | null = null
let isUserDirectoryScrollCaptureSuppressed = false
// A route transition can temporarily retain the outgoing view's height. Keep
// the captured list offset authoritative until UserManager accepts and renders
// its next response; otherwise that transient clamp can rewrite safe scroll
// context before the list exists.
let pendingUserDirectoryRestoreScroll: number | null = null
let userDirectoryRestoreGeneration = 0
let isMenuUserDirectoryNavigationPending = false
const canAccessSystemSettings = computed(() => isCachedSuperAdmin())
const sectionMetaByKey: Record<string, { title: string; description: string }> = {
  menu: {
    title: 'پنل مدیریت',
    description: 'ورود به ابزارهای مجاز مدیریتی',
  },
  create_invitation: {
    title: 'ارسال دعوت‌نامه',
    description: 'ساخت لینک دعوت و مدیریت دعوت‌نامه‌های در انتظار',
  },
  create_channel: {
    title: 'ساخت کانال',
    description: 'ایجاد و تنظیم کانال‌های پیام‌رسان',
  },
  manage_commodities: {
    title: 'مدیریت کالاها',
    description: 'تعریف کالا و نام‌های قابل استفاده در بازار',
  },
  manage_users: {
    title: 'مدیریت کاربران',
    description: 'جستجو، مشاهده و تنظیم کاربران پروژه',
  },
  admin_messages: {
    title: 'پیام‌های مدیریت',
    description: 'پیام بازار و اعلان‌های مدیریتی',
  },
  settings: {
    title: 'تنظیمات سیستم',
    description: 'تنظیمات حساس بازار، دعوت و امنیت',
  },
  user_profile: {
    title: 'پروفایل کاربر',
    description: 'مشاهده و ویرایش تنظیمات کاربر منتخب',
  },
}
const currentSectionMeta = computed(() => (
  sectionMetaByKey[currentSection.value]
  ?? sectionMetaByKey.menu
  ?? { title: 'مرکز مدیریت', description: '' }
))
const isUserDirectoryProfileSubview = computed(() => currentSection.value === 'user_profile')
const adminSubviewReturnLabel = computed(() =>
  isUserDirectoryProfileSubview.value ? 'بازگشت به فهرست کاربران' : 'بازگشت به پنل مدیریت',
)
const routeAdminSections = new Set([
  'create_invitation',
  'create_channel',
  'manage_commodities',
  'manage_users',
  'admin_messages',
  'settings',
])
const adminRouteSectionByName: Record<string, string> = {
  'admin-invitations': 'create_invitation',
  'admin-channels': 'create_channel',
  'admin-commodities': 'manage_commodities',
  'admin-users': 'manage_users',
  'admin-messages': 'admin_messages',
  'admin-system': 'settings',
}
const adminRouteNameBySection: Record<string, string> = {
  create_invitation: 'admin-invitations',
  create_channel: 'admin-channels',
  manage_commodities: 'admin-commodities',
  manage_users: 'admin-users',
  admin_messages: 'admin-messages',
  settings: 'admin-system',
}

function getRouteName() {
  return typeof route.name === 'string' ? route.name : ''
}

function getSingleParam(value: unknown) {
  if (Array.isArray(value)) return value[0] ?? null
  return value ?? null
}

function normalizeLegacyAdminSection(section: unknown) {
  if (section === 'system_settings') {
    return 'settings'
  }
  return section
}

function canAccessAdminSection(section: string) {
  if (!routeAdminSections.has(section)) return false
  if (isCachedSuperAdmin()) return true
  if (isCachedMiddleManager()) {
    return section === 'create_invitation' || section === 'manage_users'
  }
  return section === 'create_invitation' || section === 'manage_users' || section === 'manage_commodities'
}

function getAdminRouteForSection(section: string) {
  if (section === 'manage_users') {
    return {
      name: 'admin-users',
      query: buildUserDirectoryRouteQuery(),
    }
  }

  const routeName = adminRouteNameBySection[section]
  return routeName ? { name: routeName } : { name: 'admin' }
}

function isAdminUserDirectoryRoute() {
  return getRouteName() === 'admin-users'
}

function isAdminUserProfileRoute() {
  return getRouteName() === 'admin-user-profile'
}

function isLegacyAdminUserDirectoryListRoute() {
  return getRouteName() === 'admin' &&
    normalizeLegacyAdminSection(getSingleParam(route.query.section)) === 'manage_users'
}

function isLegacyAdminUserProfileRoute() {
  return getRouteName() === 'admin' && getSingleParam(route.query.section) === 'user_profile'
}

function parseUserDirectoryScroll(value: unknown) {
  const parsed = Number(getSingleParam(value))
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : 0
}

function buildUserDirectoryRouteQuery(scroll = userDirectoryScrollTop.value): Record<string, string> {
  const normalizedScroll = parseUserDirectoryScroll(scroll)
  return normalizedScroll > 0 ? { scroll: String(normalizedScroll) } : {}
}

function routeQueryMatches(query: Record<string, string>) {
  const entries = Object.entries(route.query)
  const expectedEntries = Object.entries(query)
  if (entries.length !== expectedEntries.length) return false
  if (entries.some(([, value]) => Array.isArray(value) || value == null)) return false
  return expectedEntries.every(([key, value]) => getSingleParam(route.query[key]) === value)
}

function syncUserDirectoryRouteQuery() {
  const isListRoute = isAdminUserDirectoryRoute()
  const isProfileRoute = isAdminUserProfileRoute()
  if (!isListRoute && !isProfileRoute) return

  const query = buildUserDirectoryRouteQuery()
  if (routeQueryMatches(query)) return

  if (isListRoute) {
    void router.replace({ name: 'admin-users', query })
    return
  }

  const userId = getRouteUserProfileId()
  if (userId) {
    void router.replace({
      name: 'admin-user-profile',
      params: { id: String(userId) },
      query,
    })
  }
}

function resolveUserDirectoryScrollTarget(): HTMLElement | Window | null {
  if (typeof document !== 'undefined') {
    const routeScroll = document.querySelector<HTMLElement>('.app-route-scroll')
    if (routeScroll) return routeScroll
  }
  return typeof window !== 'undefined' ? window : null
}

function readUserDirectoryScrollTop() {
  const target = userDirectoryScrollTarget ?? resolveUserDirectoryScrollTarget()
  if (typeof HTMLElement !== 'undefined' && target instanceof HTMLElement) {
    return target.scrollTop
  }
  if (typeof window === 'undefined') return 0
  return Math.max(
    window.scrollY,
    document.documentElement?.scrollTop ?? 0,
    document.body?.scrollTop ?? 0,
  )
}

function hasOverlappingAdminViewRoots() {
  return typeof document !== 'undefined' && document.querySelectorAll('.admin-view').length > 1
}

function queueUserDirectoryScrollRestore(scroll = userDirectoryScrollTop.value) {
  userDirectoryRestoreGeneration += 1
  pendingUserDirectoryRestoreScroll = parseUserDirectoryScroll(scroll)
  isUserDirectoryScrollCaptureSuppressed = true
}

function clearUserDirectoryScrollRestore() {
  userDirectoryRestoreGeneration += 1
  pendingUserDirectoryRestoreScroll = null
  isUserDirectoryScrollCaptureSuppressed = false
}

function finalizeUserDirectoryScrollRestore(expectedScroll: number) {
  const actualScroll = parseUserDirectoryScroll(readUserDirectoryScrollTop())
  userDirectoryScrollTop.value = actualScroll
  clearUserDirectoryScrollRestore()

  // A previously valid history offset can become unreachable when the current
  // result set is shorter. Canonicalize to the rendered safe offset rather
  // than leaving capture suppressed or retaining an impossible scroll value.
  if (actualScroll !== expectedScroll) {
    syncUserDirectoryRouteQuery()
  }
}

function captureUserDirectoryScroll(syncRoute = true) {
  if (
    !isAdminUserDirectoryRoute() ||
    isUserDirectoryScrollCaptureSuppressed ||
    hasOverlappingAdminViewRoots()
  ) return
  const nextScroll = parseUserDirectoryScroll(readUserDirectoryScrollTop())
  if (nextScroll === userDirectoryScrollTop.value) return

  userDirectoryScrollTop.value = nextScroll
  if (syncRoute) syncUserDirectoryRouteQuery()
}

function handleUserDirectoryScroll() {
  captureUserDirectoryScroll()
}

function restoreUserDirectoryScroll(releaseWhenLoaded = false) {
  if (!isAdminUserDirectoryRoute()) return
  const restoreGeneration = userDirectoryRestoreGeneration
  const pendingScroll = pendingUserDirectoryRestoreScroll
  const expectedScroll = pendingScroll ?? userDirectoryScrollTop.value
  let remainingTransitionFrames = 24
  isUserDirectoryScrollCaptureSuppressed = true

  const applyScroll = () => {
    if (restoreGeneration !== userDirectoryRestoreGeneration) return
    if (
      !isAdminUserDirectoryRoute() ||
      (pendingScroll === null && expectedScroll !== userDirectoryScrollTop.value) ||
      (pendingScroll !== null && pendingUserDirectoryRestoreScroll !== pendingScroll)
    ) {
      clearUserDirectoryScrollRestore()
      return
    }

    const target = userDirectoryScrollTarget ?? resolveUserDirectoryScrollTarget()
    if (typeof HTMLElement !== 'undefined' && target instanceof HTMLElement) {
      target.scrollTop = expectedScroll
    } else if (typeof window !== 'undefined') {
      window.scrollTo(0, expectedScroll)
    }

    // A list can be temporarily shorter while the route transition or its
    // initial data request is in flight. Do not let that clamped intermediate
    // value overwrite the canonical route context. A pending value stays
    // locked until UserManager has accepted a response and rendered its rows.
    if (pendingScroll !== null) {
      if (releaseWhenLoaded) {
        finalizeUserDirectoryScrollRestore(pendingScroll)
      }
      return
    }

    if (readUserDirectoryScrollTop() !== expectedScroll) {
      finalizeUserDirectoryScrollRestore(expectedScroll)
      return
    }

    if (
      hasOverlappingAdminViewRoots() &&
      remainingTransitionFrames > 0 &&
      typeof window !== 'undefined' &&
      typeof window.requestAnimationFrame === 'function'
    ) {
      remainingTransitionFrames -= 1
      window.requestAnimationFrame(applyScroll)
      return
    }

    isUserDirectoryScrollCaptureSuppressed = false
  }

  void nextTick(() => {
    applyScroll()
  })
}

function syncUserDirectoryRouteContext(waitForAcceptedList = false) {
  const isListRoute = isAdminUserDirectoryRoute()
  const isProfileRoute = isAdminUserProfileRoute()
  const isLegacyListRoute = isLegacyAdminUserDirectoryListRoute()
  const isLegacyProfileRoute = isLegacyAdminUserProfileRoute()
  if (!isListRoute && !isProfileRoute && !isLegacyListRoute && !isLegacyProfileRoute) return

  const nextScroll = parseUserDirectoryScroll(route.query.scroll)
  const shouldRestoreScroll = isListRoute && nextScroll !== userDirectoryScrollTop.value
  userDirectoryScrollTop.value = nextScroll

  // Compatibility URLs are read once, then replaced with the native route. This
  // both preserves a safe scroll offset and removes query fields that may carry
  // raw directory search or identity data.
  if (isLegacyListRoute) {
    queueUserDirectoryScrollRestore(nextScroll)
    void router.replace({ name: 'admin-users', query: buildUserDirectoryRouteQuery() })
    return
  }
  if (isLegacyProfileRoute) {
    const userId = getRouteUserProfileId()
    if (userId) {
      void router.replace({
        name: 'admin-user-profile',
        params: { id: String(userId) },
        query: buildUserDirectoryRouteQuery(),
      })
    } else {
      void router.replace({ name: 'admin' })
    }
    return
  }

  if (isProfileRoute && !getRouteUserProfileId()) {
    queueUserDirectoryScrollRestore(nextScroll)
    void router.replace({ name: 'admin-users', query: buildUserDirectoryRouteQuery() })
    return
  }

  if (isListRoute && waitForAcceptedList) {
    queueUserDirectoryScrollRestore(nextScroll)
  }
  syncUserDirectoryRouteQuery()
  if (shouldRestoreScroll || (isListRoute && waitForAcceptedList)) {
    restoreUserDirectoryScroll()
  }
}

function handleUserDirectoryQueryChange(query: string) {
  // The child normalizes before emitting. Keep the copy in memory only, never
  // mirror it into route state, history, or storage.
  userDirectoryQuery.value = typeof query === 'string' ? query.trim() : ''
}

function handleUserDirectorySettled() {
  if (pendingUserDirectoryRestoreScroll !== null) {
    restoreUserDirectoryScroll(true)
  }
}

function getRouteUserProfileId(): number | null {
  const routeName = getRouteName()
  if (routeName === 'admin-user-profile') {
    const normalized = Number(getSingleParam(route.params?.id))
    return Number.isInteger(normalized) && normalized > 0 ? normalized : null
  }

  if (getSingleParam(route.query.section) !== 'user_profile') {
    return null
  }

  const normalized = Number(getSingleParam(route.query.user_id))
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
}

function shouldClearRouteUserProfile(): boolean {
  return getRouteName().startsWith('admin-') ||
    typeof getSingleParam(route.query.section) === 'string' ||
    typeof getSingleParam(route.query.user_id) === 'string'
}

function getRouteAdminSection(): string | null {
  const routeSection = adminRouteSectionByName[getRouteName()]
  if (routeSection) {
    return canAccessAdminSection(routeSection) ? routeSection : null
  }

  const section = normalizeLegacyAdminSection(getSingleParam(route.query.section))
  if (typeof section !== 'string' || section === 'user_profile' || !canAccessAdminSection(section)) {
    return null
  }

  return section
}

function getDeniedRouteAdminSection(): string | null {
  const routeSection = adminRouteSectionByName[getRouteName()]
  if (routeSection) {
    return canAccessAdminSection(routeSection) ? null : routeSection
  }

  const section = normalizeLegacyAdminSection(getSingleParam(route.query.section))
  if (
    typeof section !== 'string' ||
    section === 'user_profile' ||
    !routeAdminSections.has(section) ||
    canAccessAdminSection(section)
  ) {
    return null
  }

  return section
}

function syncRouteToSection() {
  // This is the outgoing /admin instance during the route transition. Keep it
  // on the menu until unmount so only the freshly keyed /admin/users instance
  // can create the directory and its first request.
  if (isMenuUserDirectoryNavigationPending && isAdminUserDirectoryRoute()) {
    return
  }

  const routeUserId = getRouteUserProfileId()
  if (routeUserId) {
    syncUserDirectoryRouteContext()
    void loadRouteUserProfile(routeUserId)
    return
  }

  if (isLegacyAdminUserProfileRoute()) {
    syncUserDirectoryRouteContext()
    cancelRouteUserProfileRequest()
    currentSection.value = 'menu'
    selectedUserForProfile.value = null
    routeUserProfileError.value = null
    return
  }

  if (isAdminUserProfileRoute()) {
    syncUserDirectoryRouteContext()
    cancelRouteUserProfileRequest()
    currentSection.value = 'manage_users'
    selectedUserForProfile.value = null
    routeUserProfileError.value = null
    return
  }

  if (getDeniedRouteAdminSection()) {
    cancelRouteUserProfileRequest()
    clearUserDirectoryScrollRestore()
    currentSection.value = 'menu'
    selectedUserForProfile.value = null
    routeUserProfileError.value = null
    void router.replace({ name: 'admin' })
    return
  }

  const routeSection = getRouteAdminSection()
  if (routeSection) {
    const isEnteringUserDirectory =
      routeSection === 'manage_users' && currentSection.value !== 'manage_users'
    cancelRouteUserProfileRequest()
    if (routeSection !== 'manage_users') {
      clearUserDirectoryScrollRestore()
    }
    selectedUserForProfile.value = null
    routeUserProfileError.value = null
    currentSection.value = routeSection
    if (routeSection === 'manage_users') {
      syncUserDirectoryRouteContext(isEnteringUserDirectory)
      restoreUserDirectoryScroll()
    }
    return
  }

  cancelRouteUserProfileRequest()
  clearUserDirectoryScrollRestore()
  if (currentSection.value !== 'menu') {
    currentSection.value = 'menu'
    selectedUserForProfile.value = null
  }
}

function cancelRouteUserProfileRequest() {
  routeUserProfileRequestSequence += 1
  routeUserProfileAbortController?.abort()
  routeUserProfileAbortController = null
  routeUserProfileInFlightId = null
  routeUserProfileLoadedId = null
  isLoadingRouteUserProfile.value = false
}

function isAbortError(error: unknown) {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError'
}

async function loadRouteUserProfile(userId: number) {
  if (
    currentSection.value === 'user_profile' &&
    selectedUserForProfile.value?.id === userId &&
    routeUserProfileLoadedId === userId
  ) {
    return
  }
  if (routeUserProfileInFlightId === userId && routeUserProfileAbortController) return

  const requestSequence = ++routeUserProfileRequestSequence
  routeUserProfileAbortController?.abort()
  const controller = new AbortController()
  routeUserProfileAbortController = controller
  routeUserProfileInFlightId = userId
  routeUserProfileLoadedId = null
  currentSection.value = 'user_profile'
  selectedUserForProfile.value = null
  routeUserProfileError.value = null
  isLoadingRouteUserProfile.value = true
  try {
    const user = await routeRequestJson<any>(`/api/users/${userId}`, {
      signal: controller.signal,
      errorContext: {
        surface: 'admin',
        scope: 'page',
        operation: 'load-detail',
        resourceLabel: 'کاربر',
        fallbackMessage: 'دریافت پروفایل کاربر ممکن نشد.',
      },
    })
    if (requestSequence !== routeUserProfileRequestSequence || getRouteUserProfileId() !== userId) return
    if (!user || typeof user !== 'object' || Array.isArray(user) || Number(user.id) !== userId) {
      throw new Error('invalid_route_user_payload')
    }

    selectedUserForProfile.value = user
    routeUserProfileLoadedId = userId
    currentSection.value = 'user_profile'
  } catch (error) {
    if (requestSequence !== routeUserProfileRequestSequence || getRouteUserProfileId() !== userId) return
    if (isAbortError(error)) return

    if (isAppHttpError(error) && error.status === 403) {
      routeUserProfileError.value = {
        title: 'دسترسی به پروفایل مجاز نیست',
        message: 'مجوز مشاهده این پروفایل را ندارید.',
      }
    } else if (isAppHttpError(error) && error.status === 404) {
      routeUserProfileError.value = {
        title: 'کاربر پیدا نشد',
        message: 'این کاربر در دسترس نیست یا دیگر وجود ندارد.',
      }
    } else {
      routeUserProfileError.value = {
        title: 'پروفایل کاربر در دسترس نیست',
        message: 'دریافت اطلاعات کاربر انجام نشد. دوباره تلاش کنید.',
      }
    }
  } finally {
    if (requestSequence === routeUserProfileRequestSequence) {
      isLoadingRouteUserProfile.value = false
      if (routeUserProfileAbortController === controller) {
        routeUserProfileAbortController = null
      }
      if (routeUserProfileInFlightId === userId) {
        routeUserProfileInFlightId = null
      }
    }
  }
}

function retryRouteUserProfile() {
  const userId = getRouteUserProfileId()
  if (userId && !isLoadingRouteUserProfile.value) {
    void loadRouteUserProfile(userId)
  }
}

onMounted(() => {
  jwtToken.value = localStorage.getItem('auth_token')
  // Router guard handles redirect to login if token is missing/expired
  syncRouteToSection()
  void nextTick(() => {
    userDirectoryScrollTarget = resolveUserDirectoryScrollTarget()
    userDirectoryScrollTarget?.addEventListener('scroll', handleUserDirectoryScroll, {
      passive: true,
    })
    restoreUserDirectoryScroll()
  })
})

watch(
  () => [route.name, route.params?.id, route.query],
  () => syncRouteToSection(),
  { deep: true },
)

function goToMenu() {
  cancelRouteUserProfileRequest()
  clearUserDirectoryScrollRestore()
  currentSection.value = 'menu'
  selectedUserForProfile.value = null
  routeUserProfileError.value = null
  isLoadingRouteUserProfile.value = false
  popBackState()
  if (shouldClearRouteUserProfile()) {
    void router.replace({ name: 'admin' })
  }
}

function handleAdminSubviewReturn() {
  if (!isUserDirectoryProfileSubview.value) {
    goToMenu()
    return
  }

  captureUserDirectoryScroll(false)
  queueUserDirectoryScrollRestore()
  cancelRouteUserProfileRequest()
  selectedUserForProfile.value = null
  routeUserProfileError.value = null
  popBackState()
  void router.push({
    name: 'admin-users',
    query: buildUserDirectoryRouteQuery(),
  })
}

function handleNavigate(section: string, data?: any) {
  if (routeAdminSections.has(section) && !canAccessAdminSection(section)) {
    goToMenu()
    return
  }
  
  if (section === 'user_profile' && data) {
    const normalizedId = Number(data.id ?? data.user_id)
    if (!Number.isInteger(normalizedId) || normalizedId <= 0) {
      return
    }
    captureUserDirectoryScroll(false)
    // Vue removes the list before the route change resolves. That can clamp the
    // shared scroll container and emit an intermediate scroll event; keep the
    // captured route context authoritative through the transition.
    queueUserDirectoryScrollRestore()
    if (currentSection.value !== 'menu') {
      // تغییر ساب‌پیج — جایگزینی state قبلی
      popBackState()
    }
    cancelRouteUserProfileRequest()
    selectedUserForProfile.value = null
    routeUserProfileError.value = null
    currentSection.value = 'user_profile'
    isLoadingRouteUserProfile.value = true
    void router.push({
      name: 'admin-user-profile',
      params: { id: String(normalizedId) },
      query: buildUserDirectoryRouteQuery(),
    })
    pushBackState(() => {
      currentSection.value = 'menu'
      selectedUserForProfile.value = null
    })
    return
  }
  
  if (section === 'admin_panel') {
    goToMenu()
  } else {
    const isEnteringUserDirectoryFromMenu =
      section === 'manage_users' && currentSection.value === 'menu'
    if (isEnteringUserDirectoryFromMenu) {
      if (isMenuUserDirectoryNavigationPending) return
      isMenuUserDirectoryNavigationPending = true
      // Let the native destination own the first UserManager mount. Rendering
      // the list while /admin is still active can overlap the outgoing and
      // incoming AdminView instances during the route transition.
      void Promise.resolve(router.push(getAdminRouteForSection(section))).then(
        () => {
          if (!isAdminUserDirectoryRoute()) {
            isMenuUserDirectoryNavigationPending = false
          }
        },
        () => {
          isMenuUserDirectoryNavigationPending = false
        },
      )
      pushBackState(() => {
        currentSection.value = 'menu'
        selectedUserForProfile.value = null
      })
      return
    }

    const isReturningFromUserProfile =
      section === 'manage_users' && currentSection.value === 'user_profile'
    if (isReturningFromUserProfile) {
      // UserProfile can also request the directory return (for example after a
      // bounded action). Keep its captured list context locked until the
      // incoming directory has accepted a response, just like the shell back
      // control above.
      queueUserDirectoryScrollRestore()
      popBackState()
      cancelRouteUserProfileRequest()
      selectedUserForProfile.value = null
      routeUserProfileError.value = null
      void router.push(getAdminRouteForSection(section))
      pushBackState(() => {
        currentSection.value = 'menu'
        selectedUserForProfile.value = null
      })
      return
    }
    if (currentSection.value === 'user_profile') {
      clearUserDirectoryScrollRestore()
    }
    if (currentSection.value !== 'menu') {
      // تغییر ساب‌پیج — جایگزینی state قبلی
      popBackState()
    }
    if (currentSection.value === 'user_profile') {
      cancelRouteUserProfileRequest()
      selectedUserForProfile.value = null
      routeUserProfileError.value = null
    }
    currentSection.value = section
    void router.push(getAdminRouteForSection(section))
    pushBackState(() => {
      currentSection.value = 'menu'
      selectedUserForProfile.value = null
    })
  }
}

watch(
  () => currentSection.value,
  (section) => {
    if ((section === 'settings' || section === 'admin_messages') && !canAccessSystemSettings.value) {
      goToMenu()
    }
  }
)

function handleOpenPublicProfile(payload?: { id?: number; account_name?: string }) {
  const normalizedId = Number(payload?.id)
  if (!Number.isInteger(normalizedId) || normalizedId <= 0) {
    return
  }

  void router.push({
    name: 'public-profile',
    params: { id: String(normalizedId) },
  })
}

onUnmounted(() => {
  isMenuUserDirectoryNavigationPending = false
  cancelRouteUserProfileRequest()
  clearUserDirectoryScrollRestore()
  userDirectoryScrollTarget?.removeEventListener('scroll', handleUserDirectoryScroll)
  userDirectoryScrollTarget = null
  clearBackStack()
})
</script>

<template>
  <AppPage>
    <div class="admin-view">
      <template v-if="currentSection === 'menu'">
        <AppPageHeader
          eyebrow="مدیریت پروژه"
          title="مرکز مدیریت"
        />
        <AdminPanel @navigate="handleNavigate" />
      </template>

      <template v-else>
        <section class="admin-subview-shell">
          <div class="admin-subview-nav">
            <AppBackButton
              class="admin-subview-return"
              :label="adminSubviewReturnLabel"
              @click="handleAdminSubviewReturn"
            />
            <h1 class="admin-subview-title">{{ currentSectionMeta.title }}</h1>
          </div>
          <CreateChannelView
            v-if="currentSection === 'create_channel'"
            :apiBaseUrl="apiBaseUrl"
            :jwtToken="jwtToken"
            @open-public-profile="handleOpenPublicProfile"
          />
          <AppSectionCard v-else class="admin-subview-card">

            <transition name="fade" mode="out-in">
              <CreateInvitationView
                v-if="currentSection === 'create_invitation'"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
              />

              <CommodityManager
                v-else-if="currentSection === 'manage_commodities'"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
                @navigate="handleNavigate"
              />

              <UserManager
                v-else-if="currentSection === 'manage_users'"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
                :query="userDirectoryQuery"
                @navigate="handleNavigate"
                @query-change="handleUserDirectoryQueryChange"
                @settled="handleUserDirectorySettled"
              />

              <AdminMessagesView
                v-else-if="currentSection === 'admin_messages' && canAccessSystemSettings"
              />

              <UserProfile
                v-else-if="currentSection === 'user_profile' && selectedUserForProfile"
                :user="selectedUserForProfile"
                :isAdminView="true"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
                @navigate="handleNavigate"
              />

              <AppLoadingState
                v-else-if="currentSection === 'user_profile' && isLoadingRouteUserProfile"
                label="در حال بارگذاری پروفایل کاربر"
              />

              <AppErrorState
                v-else-if="currentSection === 'user_profile' && routeUserProfileError"
                class="admin-route-profile-error"
                :title="routeUserProfileError.title"
                :message="routeUserProfileError.message"
              >
                <template #actions>
                  <AppButton type="button" variant="secondary" @click="retryRouteUserProfile">
                    تلاش مجدد
                  </AppButton>
                </template>
              </AppErrorState>

              <TradingSettings
                v-else-if="currentSection === 'settings' && canAccessSystemSettings"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
              />
            </transition>
          </AppSectionCard>
        </section>
      </template>
    </div>
  </AppPage>
</template>

<style scoped>
.admin-view {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.admin-subview-shell {
  display: flex;
  flex-direction: column;
  gap: 0.9rem;
}

.admin-subview-card {
  min-width: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
}

.admin-subview-card :deep(.ui-section-card__body) {
  padding: 0;
}

.admin-subview-nav {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: var(--ds-native-row-min-height, 48px);
}

.admin-subview-title {
  margin: 0;
  min-width: 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-native-title-size);
  font-weight: 800;
  line-height: 1.25;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (max-width: 767px) {
  .admin-subview-card :deep(.ui-section-card__header),
  .admin-subview-card :deep(.ui-section-card__body) {
    padding-inline: 0;
  }
}
</style>
