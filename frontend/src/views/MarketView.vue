<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { Filter, Search, ArrowDown } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import OffersList from '../components/OffersList.vue'

const { offers, isLoading, fetchOffers, startPolling, stopPolling } = useOffers()
const filterType = ref<'all' | 'buy' | 'sell'>('all')

// Client-side filtering for realtime updates
const filteredOffers = computed(() => {
  if (filterType.value === 'all') return offers.value
  return offers.value.filter(o => o.offer_type === filterType.value)
})

onMounted(() => {
    const token = localStorage.getItem('auth_token')
    fetchOffers(token)
    startPolling(token)
})

onUnmounted(() => {
    stopPolling()
})
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
        @click="filterType = tab as any"
        class="flex-1 py-2 text-sm font-medium rounded-lg transition-all duration-200"
        :class="filterType === tab ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500 hover:text-gray-700'"
      >
        {{ tab === 'all' ? 'همه' : (tab === 'buy' ? 'خریدار' : 'فروشنده') }}
      </button>
    </div>

    <!-- List -->
    <OffersList :offers="filteredOffers" :loading="isLoading" />

  </div>
</template>
