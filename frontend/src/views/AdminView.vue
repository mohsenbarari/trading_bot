<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AdminPanel from '../components/AdminPanel.vue'
import UserManager from '../components/UserManager.vue'
import CommodityManager from '../components/CommodityManager.vue'
import TradingSettings from '../components/TradingSettings.vue'
import CreateInvitationView from '../components/CreateInvitationView.vue'
import UserProfile from '../components/UserProfile.vue'

const router = useRouter()
const currentSection = ref('menu')
const jwtToken = ref<string | null>(null)
const apiBaseUrl = '' // Relative path for proxy
const selectedUserForProfile = ref<any>(null)

onMounted(() => {
  jwtToken.value = localStorage.getItem('auth_token')
  if (!jwtToken.value) {
    router.push('/login')
  }
})

function handleNavigate(section: string, data?: any) {
  console.log('Navigate to:', section, data)
  
  if (section === 'user_profile' && data) {
     selectedUserForProfile.value = data
     currentSection.value = 'user_profile'
     return
  }
  
  if (section === 'admin_panel') {
    currentSection.value = 'menu'
    selectedUserForProfile.value = null
  } else {
    currentSection.value = section
  }
}
</script>

<template>
  <div class="admin-view min-h-screen bg-gray-50 flex flex-col">
     <!-- Top Bar -->
     <div class="bg-white shadow-sm p-4 sticky top-0 z-10">
         <div class="max-w-3xl mx-auto flex items-center justify-between">
             <div class="flex items-center gap-3">
                 <button v-if="currentSection !== 'menu'" @click="handleNavigate('admin_panel')" class="p-2 rounded-full hover:bg-gray-100 transition-colors">
                     🔙
                 </button>
                 <h1 class="text-xl font-bold text-gray-800">
                     {{ currentSection === 'menu' ? 'پنل مدیریت' : 
                        currentSection === 'manage_users' ? 'مدیریت کاربران' :
                        currentSection === 'manage_commodities' ? 'مدیریت کالاها' :
                        currentSection === 'settings' ? 'تنظیمات سیستم' : 
                        currentSection === 'user_profile' ? 'پروفایل کاربر' : 
                        'پنل مدیریت' 
                     }}
                 </h1>
             </div>
             <button @click="router.push('/')" class="text-sm text-primary-600 font-medium hover:underline">
                 بازگشت به داشبورد 🏠
             </button>
         </div>
     </div>

     <!-- Content Area -->
     <div class="flex-1 p-4 overflow-y-auto">
         <div class="max-w-3xl mx-auto w-full">
            
            <transition name="fade" mode="out-in">
                <AdminPanel v-if="currentSection === 'menu'" @navigate="handleNavigate" />
                
                <CreateInvitationView 
                   v-else-if="currentSection === 'create_invitation'" 
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
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
