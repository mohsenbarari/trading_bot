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
import { isCachedSuperAdmin } from '../utils/adminAccess'

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

function getRouteUserProfileId(): number | null {
  if (route.query.section !== 'user_profile') {
    return null
  }

  const normalized = Number(route.query.user_id)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
}

function shouldClearRouteUserProfile(): boolean {
  return typeof route.query.section === 'string' || typeof route.query.user_id === 'string'
}

function getRouteAdminSection(): string | null {
  const section = route.query.section
  if (typeof section !== 'string' || section === 'user_profile' || !routeAdminSections.has(section)) {
    return null
  }

  if ((section === 'settings' || section === 'admin_messages') && !canAccessSystemSettings.value) {
    return null
  }

  return section
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
  const routeUserId = getRouteUserProfileId()
  if (routeUserId) {
    void loadRouteUserProfile(routeUserId)
    return
  }

  const routeSection = getRouteAdminSection()
  if (routeSection) {
    selectedUserForProfile.value = null
    currentSection.value = routeSection
  }
})

watch(
  () => [route.query.section, route.query.user_id],
  ([section, userId]) => {
    if (section === 'user_profile') {
      const normalized = Number(userId)
      if (Number.isInteger(normalized) && normalized > 0) {
        void loadRouteUserProfile(normalized)
      }
      return
    }

    const routeSection = getRouteAdminSection()
    if (routeSection) {
      selectedUserForProfile.value = null
      currentSection.value = routeSection
      return
    }

    if (section === undefined && currentSection.value !== 'menu') {
      currentSection.value = 'menu'
      selectedUserForProfile.value = null
    }
  }
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
  console.log('Navigate to:', section, data)

  if ((section === 'settings' || section === 'admin_messages') && !canAccessSystemSettings.value) {
    goToMenu()
    return
  }
  
  if (section === 'user_profile' && data) {
     if (currentSection.value !== 'menu') {
       // تغییر ساب‌پیج — جایگزینی state قبلی
       popBackState()
     }
     selectedUserForProfile.value = data
     currentSection.value = 'user_profile'
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

                <div v-else-if="currentSection === 'user_profile' && isLoadingRouteUserProfile" class="admin-route-loading">
                  <strong>در حال بارگذاری پروفایل کاربر...</strong>
                  <p>پس از دریافت اطلاعات، تنظیمات کاربر منتخب نمایش داده می‌شود.</p>
                </div>

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
  padding-bottom: 6rem;
}

.admin-inner {
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  width: 100%;
}

.admin-route-loading {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  padding: 0.85rem;
  border-radius: var(--ds-radius-md);
  border: 1px dashed var(--ds-border-medium);
  background: var(--ds-bg-inset);
  color: var(--ds-text-secondary);
}

.admin-route-loading strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 850;
  line-height: 1.5;
}

.admin-route-loading p {
  margin: 0;
  font-size: var(--ds-font-xs);
  line-height: 1.8;
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
