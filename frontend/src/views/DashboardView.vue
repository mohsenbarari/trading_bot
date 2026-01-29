<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Wallet, TrendingUp, PlusCircle, Activity, ArrowUpRight, ArrowDownLeft } from 'lucide-vue-next'

const router = useRouter()
const user = ref<any>(null)
const loading = ref(true)

async function fetchUser() {
  try {
    const token = localStorage.getItem('auth_token')
    if (!token) return router.push('/login')
    
    const res = await fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${token}` }
    })
    
    if (res.ok) {
      user.value = await res.json()
    } else {
      router.push('/login')
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

onMounted(fetchUser)

</script>

<template>
  <div class="p-6 space-y-6">
    
    <!-- Skeleton Loader -->
    <div v-if="loading" class="animate-pulse space-y-4">
      <div class="h-20 bg-gray-200 rounded-2xl w-full"></div>
      <div class="grid grid-cols-2 gap-4">
        <div class="h-32 bg-gray-200 rounded-2xl"></div>
        <div class="h-32 bg-gray-200 rounded-2xl"></div>
      </div>
    </div>

    <div v-else-if="user" class="space-y-6">
      
      <!-- Header -->
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-bold text-gray-800">داشبورد</h1>
          <p class="text-gray-500 text-sm">خوش آمدید، {{ user.full_name || user.account_name }}</p>
        </div>
        <div class="w-10 h-10 bg-primary-100 text-primary-600 rounded-full flex items-center justify-center font-bold">
          {{ (user.full_name || user.account_name)[0] }}
        </div>
      </div>

      <!-- Stats Grid -->
      <div class="grid grid-cols-2 gap-4">
        <div class="card-glass p-4 flex flex-col items-center justify-center text-center gap-2">
          <div class="w-10 h-10 bg-green-100 text-green-600 rounded-xl flex items-center justify-center">
            <TrendingUp :size="20" />
          </div>
          <span class="text-2xl font-bold text-gray-800">{{ user.trades_count || 0 }}</span>
          <span class="text-xs text-gray-500">معاملات موفق</span>
        </div>

        <div class="card-glass p-4 flex flex-col items-center justify-center text-center gap-2">
          <div class="w-10 h-10 bg-blue-100 text-blue-600 rounded-xl flex items-center justify-center">
            <Activity :size="20" />
          </div>
          <span class="text-2xl font-bold text-gray-800">{{ user.channel_messages_count || 0 }}</span>
          <span class="text-xs text-gray-500">لفظ‌های ثبت شده</span>
        </div>
      </div>

      <!-- Quick Actions -->
      <div>
        <h2 class="text-lg font-bold text-gray-800 mb-3">دسترسی سریع</h2>
        <div class="grid grid-cols-1 gap-3">
          
          <button class="btn-primary" @click="router.push('/market')">
            <PlusCircle :size="20" />
            <span>ثبت لفظ جدید (در بازار)</span>
          </button>
          
          <button class="w-full py-3 px-4 bg-white border border-gray-200 text-gray-700 font-bold rounded-xl shadow-sm hover:bg-gray-50 active:scale-95 transition-all duration-200 flex items-center justify-center gap-2">
            <Wallet :size="20" />
            <span>مدیریت کیف پول</span>
          </button>
        </div>
      </div>

      <!-- Recent Activity (Mock) -->
       <div>
        <h2 class="text-lg font-bold text-gray-800 mb-3">فعالیت‌های اخیر</h2>
        <div class="space-y-3">
           <div class="bg-white p-4 rounded-xl shadow-soft flex items-center justify-between">
              <div class="flex items-center gap-3">
                 <div class="w-8 h-8 bg-red-100 text-red-500 rounded-lg flex items-center justify-center">
                    <ArrowDownLeft :size="16" />
                 </div>
                 <div class="flex flex-col">
                    <span class="text-sm font-bold">فروش آبشده</span>
                    <span class="text-xs text-gray-400">۱۰ دقیقه پیش</span>
                 </div>
              </div>
              <span class="font-bold text-gray-800">12,450,000</span>
           </div>
           
           <div class="bg-white p-4 rounded-xl shadow-soft flex items-center justify-between">
              <div class="flex items-center gap-3">
                 <div class="w-8 h-8 bg-green-100 text-green-500 rounded-lg flex items-center justify-center">
                    <ArrowUpRight :size="16" />
                 </div>
                 <div class="flex flex-col">
                    <span class="text-sm font-bold">خرید سکه امامی</span>
                    <span class="text-xs text-gray-400">۲ ساعت پیش</span>
                 </div>
              </div>
              <span class="font-bold text-gray-800">45,200,000</span>
           </div>
        </div>
      </div>

    </div>
  </div>
</template>
