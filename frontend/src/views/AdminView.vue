<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
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

  const section = route.query.section
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
  <div class="admin-view ds-page">
     <!-- Top Bar (only when inside a sub-section) -->
     <div v-if="currentSection !== 'menu'" class="admin-top-bar">
         <div class="header-row admin-header">
             <div class="header-spacer"></div>
             <div class="header-title">
                 <h2>{{ currentSectionMeta.title }}</h2>
                 <p>{{ currentSectionMeta.description }}</p>
             </div>
             <button @click="handleNavigate('admin_panel')" class="back-button">
                 <ChevronLeft :size="24" />
             </button>
         </div>
     </div>

     <!-- Content Area -->
     <div class="admin-content">
         <div class="admin-inner">
            
            <transition name="fade" mode="out-in">
                <AdminPanel v-if="currentSection === 'menu'" @navigate="handleNavigate" />
                
                <CreateInvitationView 
                   v-else-if="currentSection === 'create_invitation'" 
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
     </div>
  </div>
</template>

<style scoped>
.admin-view {
  display: flex;
  flex-direction: column;
  min-height: 100dvh;
}

.admin-top-bar {
  position: sticky;
  top: 0;
  z-index: 10;
  background: var(--ds-bg-card);
  border-bottom: 1px solid var(--ds-border-light);
}

.admin-header {
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
}

.admin-header .header-title {
  display: flex;
  min-width: 0;
  flex-direction: column;
  align-items: center;
  gap: 0.1rem;
}

.admin-header .header-title h2 {
  margin: 0;
  font-size: var(--ds-font-lg);
  font-weight: 850;
  line-height: 1.35;
}

.admin-header .header-title p {
  max-width: 16rem;
  margin: 0;
  overflow: hidden;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.admin-content {
  flex: 1;
  padding: var(--ds-card-padding);
  overflow-y: auto;
  padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 4rem);
  scroll-padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 4rem);
}

.admin-inner {
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  width: 100%;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
