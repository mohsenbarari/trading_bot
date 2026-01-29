<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { Filter, Search, ArrowDown } from 'lucide-vue-next'

const offers = ref<any[]>([])
const loading = ref(true)
const filterType = ref<'all' | 'buy' | 'sell'>('all')

async function fetchOffers() {
  loading.value = true
  try {
    const token = localStorage.getItem('auth_token')
    let url = '/api/offers/'
    if (filterType.value !== 'all') {
        url += `?offer_type=${filterType.value}`
    }
    
    const res = await fetch(url, {
      headers: { Authorization: `Bearer ${token}` }
    })
    
    if (res.ok) {
      offers.value = await res.json()
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function getStatusBadge(type: string) {
  return type === 'buy' 
    ? { text: 'خرید', bg: 'bg-green-100', color: 'text-green-700' }
    : { text: 'فروش', bg: 'bg-red-100', color: 'text-red-700' }
}

function timeAgo(dateString: string) {
    // Simple placeholder, ideally use date-fns or moment-jalaali
    return dateString // Backend returns Jalali string currently
}

onMounted(fetchOffers)
</script>

<template>
  <div class="p-4 pb-24 space-y-4">
    
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-2xl font-bold text-gray-800">بازار آزاد</h1>
      <button class="w-10 h-10 bg-white rounded-xl shadow-sm flex items-center justify-center text-gray-500">
        <Filter :size="20" />
      </button>
    </div>

    <!-- Stats / Ticker (Generic) -->
    <div class="bg-gradient-to-r from-gray-900 to-gray-800 rounded-2xl p-4 text-white shadow-lg overflow-hidden relative">
       <div class="absolute right-0 top-0 w-32 h-32 bg-white opacity-5 rounded-full -mr-10 -mt-10"></div>
       <div class="flex justify-between items-end relative z-10">
          <div>
             <p class="text-gray-400 text-xs mb-1">مظنه بازار (آبشده)</p>
             <h2 class="text-2xl font-bold font-mono">15,420,000 <span class="text-sm font-sans text-gray-400">تومان</span></h2>
          </div>
          <div class="flex items-center text-green-400 text-sm bg-green-400/10 px-2 py-1 rounded-lg">
             <span dir="ltr">+1.2%</span>
             <ArrowDown class="rotate-180 ml-1" :size="14"/>
          </div>
       </div>
    </div>

    <!-- Tabs -->
    <div class="flex p-1 bg-gray-200 rounded-xl">
      <button 
        v-for="tab in ['all', 'buy', 'sell']" 
        :key="tab"
        @click="filterType = tab as any; fetchOffers()"
        class="flex-1 py-2 text-sm font-medium rounded-lg transition-all duration-200"
        :class="filterType === tab ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500 hover:text-gray-700'"
      >
        {{ tab === 'all' ? 'همه' : (tab === 'buy' ? 'خریدار' : 'فروشنده') }}
      </button>
    </div>

    <!-- List -->
    <div v-if="loading" class="space-y-3">
       <div v-for="i in 3" :key="i" class="h-24 bg-gray-200 rounded-2xl animate-pulse"></div>
    </div>

    <div v-else-if="offers.length === 0" class="text-center py-10">
       <div class="w-16 h-16 bg-gray-100 rounded-full flex items-center justify-center mx-auto mb-4 text-gray-400">
          <Search :size="32" />
       </div>
       <p class="text-gray-500">هیچ لفظ فعالی یافت نشد.</p>
    </div>

    <div v-else class="space-y-3">
      <div 
        v-for="offer in offers" 
        :key="offer.id"
        class="bg-white p-4 rounded-2xl shadow-sm border border-gray-100 flex flex-col gap-3"
      >
        <!-- Top Row -->
        <div class="flex justify-between items-start">
           <div class="flex items-center gap-3">
              <div class="w-10 h-10 rounded-xl flex items-center justify-center font-bold"
                 :class="offer.offer_type === 'buy' ? 'bg-green-50 text-green-600' : 'bg-red-50 text-red-600'">
                 {{ offer.offer_type === 'buy' ? 'خ' : 'ف' }}
              </div>
              <div>
                 <h3 class="font-bold text-gray-800">{{ offer.commodity_name }}</h3>
                 <span class="text-xs text-gray-400">{{ timeAgo(offer.created_at) }}</span>
              </div>
           </div>
           <span class="px-2 py-1 rounded-lg text-xs font-bold" :class="getStatusBadge(offer.offer_type).bg + ' ' + getStatusBadge(offer.offer_type).color">
              {{ getStatusBadge(offer.offer_type).text }}
           </span>
        </div>

        <!-- Details Row -->
        <div class="grid grid-cols-2 gap-2 bg-gray-50 p-3 rounded-xl border border-gray-100/50">
           <div class="text-center border-l border-gray-200 pl-2">
              <p class="text-xs text-gray-400 mb-1">تعداد</p>
              <p class="font-bold text-gray-900">{{ offer.quantity }}</p>
           </div>
           <div class="text-center">
              <p class="text-xs text-gray-400 mb-1">قیمت واحد</p>
              <p class="font-bold text-gray-900">{{ offer.price.toLocaleString() }}</p>
           </div>
        </div>

        <!-- Action Button -->
         <button class="w-full py-2.5 rounded-xl font-bold text-sm transition-colors"
           :class="offer.offer_type === 'buy' 
             ? 'bg-red-500 hover:bg-red-600 text-white' 
             : 'bg-green-500 hover:bg-green-600 text-white'">
            {{ offer.offer_type === 'buy' ? 'فروش به این خریدار' : 'خرید از این فروشنده' }}
         </button>

      </div>
    </div>

  </div>
</template>
