<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, ChevronLeft } from 'lucide-vue-next'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import AdminPanel from '../components/AdminPanel.vue'
import UserManager from '../components/UserManager.vue'
import CommodityManager from '../components/CommodityManager.vue'
import TradingSettings from '../components/TradingSettings.vue'
import CreateInvitationView from '../components/CreateInvitationView.vue'
import CreateChannelView from '../components/CreateChannelView.vue'
import UserProfile from '../components/UserProfile.vue'

const router = useRouter()
const currentSection = ref('menu')
const jwtToken = ref<string | null>(null)
const apiBaseUrl = '' // Relative path for proxy
const selectedUserForProfile = ref<any>(null)

onMounted(() => {
  jwtToken.value = localStorage.getItem('auth_token')
  // Router guard handles redirect to login if token is missing/expired
})

function goToMenu() {
  currentSection.value = 'menu'
  selectedUserForProfile.value = null
  popBackState()
}

function handleNavigate(section: string, data?: any) {
  console.log('Navigate to:', section, data)
  
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

onUnmounted(() => clearBackStack())
</script>

<template>
  <div class="admin-view ds-page">
     <!-- Top Bar (only when inside a sub-section) -->
     <div v-if="currentSection !== 'menu'" class="admin-top-bar">
         <div class="header-row admin-header">
             <div class="header-spacer"></div>
             <div class="header-title">
                 <h2>
                     {{ currentSection === 'manage_users' ? 'مدیریت کاربران' :
                        currentSection === 'manage_commodities' ? 'مدیریت کالاها' :
                        currentSection === 'settings' ? 'تنظیمات سیستم' : 
                        currentSection === 'user_profile' ? 'پروفایل کاربر' : 
                      currentSection === 'create_channel' ? 'ساخت کانال' :
                        currentSection === 'create_invitation' ? 'ارسال دعوت‌نامه' :
                        'پنل مدیریت' 
                     }}
                 </h2>
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

                <UserProfile
                    v-else-if="currentSection === 'user_profile' && selectedUserForProfile"
                    :user="selectedUserForProfile"
                    :isAdminView="true"
                    :apiBaseUrl="apiBaseUrl"
                    :jwtToken="jwtToken"
                    @navigate="handleNavigate"
                />

                <TradingSettings 
                   v-else-if="currentSection === 'settings'" 
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

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
