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
  <div class="flex flex-col h-screen">
    
    <!-- Fixed Filter Tabs at Top -->
    <div class="sticky top-0 z-10 bg-gray-50 pt-4 px-4 pb-3 border-b border-gray-200">
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
    </div>

    <!-- Scrollable Offers List -->
    <div class="flex-1 overflow-y-auto px-4 py-4 pb-20">
      <OffersList :offers="filteredOffers" :loading="isLoading" />
    </div>

  </div>
</template>
