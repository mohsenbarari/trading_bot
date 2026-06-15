<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChevronLeft } from 'lucide-vue-next'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import { apiFetch } from '../utils/auth'
import AdminPanel from '../components/AdminPanel.vue'
import UserManager from '../components/UserManager.vue'
import CommodityManager from '../components/CommodityManager.vue'
import TradingSettings from '../components/TradingSettings.vue'
import AdminMessagesView from '../components/AdminMessagesView.vue'
import CreateInvitationView from '../components/CreateInvitationView.vue'
import CreateChannelView from '../components/CreateChannelView.vue'
import UserProfile from '../components/UserProfile.vue'
import AppLoadingState from '../components/ui/AppLoadingState.vue'
import AppPage from '../components/ui/AppPage.vue'
import AppPageHeader from '../components/ui/AppPageHeader.vue'
import { isCachedMiddleManager, isCachedSuperAdmin } from '../utils/adminAccess'

const router = useRouter()
const route = useRoute()
const currentSection = ref('menu')
const jwtToken = ref<string | null>(null)
const apiBaseUrl = '' // Relative path for proxy
const selectedUserForProfile = ref<any>(null)
const isLoadingRouteUserProfile = ref(false)
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
const currentSectionMeta = computed(() => sectionMetaByKey[currentSection.value] || sectionMetaByKey.menu)
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
  const routeName = adminRouteNameBySection[section]
  return routeName ? { name: routeName } : { name: 'admin' }
}

function getRouteUserProfileId(): number | null {
  const routeName = getRouteName()
  if (routeName === 'admin-user-profile') {
    const normalized = Number(getSingleParam(route.params?.id))
    return Number.isInteger(normalized) && normalized > 0 ? normalized : null
  }

  if (route.query.section !== 'user_profile') {
    return null
  }

  const normalized = Number(route.query.user_id)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
}

function shouldClearRouteUserProfile(): boolean {
  return getRouteName().startsWith('admin-') ||
    typeof route.query.section === 'string' ||
    typeof route.query.user_id === 'string'
}

function getRouteAdminSection(): string | null {
  const routeSection = adminRouteSectionByName[getRouteName()]
  if (routeSection) {
    return canAccessAdminSection(routeSection) ? routeSection : null
  }

  const section = normalizeLegacyAdminSection(route.query.section)
  if (typeof section !== 'string' || section === 'user_profile' || !canAccessAdminSection(section)) {
    return null
  }

  return section
}

function syncRouteToSection() {
  const routeUserId = getRouteUserProfileId()
  if (routeUserId) {
    void loadRouteUserProfile(routeUserId)
    return
  }

  const routeSection = getRouteAdminSection()
  if (routeSection) {
    selectedUserForProfile.value = null
    currentSection.value = routeSection
    return
  }

  if (currentSection.value !== 'menu') {
    currentSection.value = 'menu'
    selectedUserForProfile.value = null
  }
}

async function loadRouteUserProfile(userId: number) {
  if (selectedUserForProfile.value?.id === userId && currentSection.value === 'user_profile') {
    return
  }

  currentSection.value = 'user_profile'
  selectedUserForProfile.value = null
  isLoadingRouteUserProfile.value = true
  try {
    const response = await apiFetch(`/api/users/${userId}`)
    if (!response.ok) {
      goToMenu()
      return
    }

    selectedUserForProfile.value = await response.json()
    currentSection.value = 'user_profile'
  } catch {
    goToMenu()
  } finally {
    isLoadingRouteUserProfile.value = false
  }
}

onMounted(() => {
  jwtToken.value = localStorage.getItem('auth_token')
  // Router guard handles redirect to login if token is missing/expired
  syncRouteToSection()
})

watch(
  () => [route.name, route.params?.id, route.query.section, route.query.user_id],
  () => syncRouteToSection()
)

function goToMenu() {
  currentSection.value = 'menu'
  selectedUserForProfile.value = null
  popBackState()
  if (shouldClearRouteUserProfile()) {
    void router.replace({ name: 'admin' })
  }
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
     if (currentSection.value !== 'menu') {
       // تغییر ساب‌پیج — جایگزینی state قبلی
       popBackState()
     }
     selectedUserForProfile.value = data
     currentSection.value = 'user_profile'
     void router.push({
       name: 'admin-user-profile',
       params: { id: String(normalizedId) },
       query: data.account_name ? { account_name: data.account_name } : {},
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
    if (currentSection.value !== 'menu') {
      // تغییر ساب‌پیج — جایگزینی state قبلی
      popBackState()
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
    query: payload?.account_name ? { account_name: payload.account_name } : undefined,
  })
}

onUnmounted(() => clearBackStack())
</script>

<template>
  <AppPage>
    <div class="admin-view">
      <template v-if="currentSection === 'menu'">
        <AppPageHeader
          eyebrow="مدیریت پروژه"
          title="مرکز مدیریت"
          description="ابزارهای مدیریتی مجاز حساب خود را از این بخش دنبال کنید."
        />
        <AdminPanel @navigate="handleNavigate" />
      </template>

      <template v-else>
        <section class="admin-subview-shell">
          <header class="admin-subview-header">
            <button @click="handleNavigate('admin_panel')" class="admin-subview-return" type="button">
              <ChevronLeft :size="20" />
            </button>
            <div class="admin-subview-copy">
              <h1>{{ currentSectionMeta.title }}</h1>
              <p>{{ currentSectionMeta.description }}</p>
            </div>
          </header>

          <div class="admin-subview-card">
            <transition name="fade" mode="out-in">
              <CreateInvitationView
                v-if="currentSection === 'create_invitation'"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
              />

              <CreateChannelView
                v-else-if="currentSection === 'create_channel'"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
                @open-public-profile="handleOpenPublicProfile"
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
                @navigate="handleNavigate"
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

              <TradingSettings
                v-else-if="currentSection === 'settings' && canAccessSystemSettings"
                :apiBaseUrl="apiBaseUrl"
                :jwtToken="jwtToken"
              />
            </transition>
          </div>
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

.admin-subview-header {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.9rem;
  align-items: start;
  padding: 1rem 1.05rem;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  box-shadow: var(--ds-shadow-sm);
}

.admin-subview-copy {
  min-width: 0;
}

.admin-subview-copy h1 {
  margin: 0 0 0.3rem;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-lg);
  font-weight: 850;
  line-height: 1.45;
}

.admin-subview-copy p {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.admin-subview-card {
  padding: 1rem;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  background: var(--ds-bg-card);
  box-shadow: var(--ds-shadow-sm);
  min-width: 0;
}

.admin-subview-return {
  width: 2.5rem;
  height: 2.5rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
  background: var(--ds-bg-inset);
  color: var(--ds-text-primary);
  cursor: pointer;
  transition: border-color 0.2s ease, background-color 0.2s ease, color 0.2s ease;
}

.admin-subview-return:hover {
  border-color: var(--ds-primary-300);
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
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
  .admin-subview-card {
    padding: 0.85rem;
  }
}
</style>
