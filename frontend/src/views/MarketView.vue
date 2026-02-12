<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { ArrowUp, ArrowDown, ArrowUpDown, X, Loader2 } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import OffersList from '../components/OffersList.vue'

interface Commodity {
  id: number
  name: string
}

const { offers, isLoading, fetchOffers, startPolling, stopPolling } = useOffers()
const filterType = ref<'all' | 'buy' | 'sell'>('all')

// Sort State
const sortCommodity = ref('')
const sortDirection = ref<'none' | 'asc' | 'desc'>('none')
const showSortPanel = ref(false)
const commodities = ref<Commodity[]>([])
const commoditiesLoading = ref(false)

// Fetch commodities
const fetchCommodities = async () => {
    commoditiesLoading.value = true
    try {
        const token = localStorage.getItem('auth_token')
        const headers: HeadersInit = {}
        if (token) headers.Authorization = `Bearer ${token}`
        
        const res = await fetch('/api/commodities/', { headers })
        if (res.ok) {
            commodities.value = await res.json()
        }
    } catch (e) {
        console.error('Failed to load commodities', e)
    } finally {
        commoditiesLoading.value = false
    }
}

// Client-side filtering & sorting
const filteredOffers = computed(() => {
  let result = [...offers.value] // Copy to sort safely
  
  // 1. Filter by type
  if (filterType.value !== 'all') {
    result = result.filter(o => o.offer_type === filterType.value)
  }
  
  // 2. Sort by price for selected commodity
  if (sortCommodity.value && sortDirection.value !== 'none') {
    const commodity = sortCommodity.value
    const dir = sortDirection.value
    
    result.sort((a, b) => {
      const aMatch = a.commodity_name === commodity
      const bMatch = b.commodity_name === commodity
      
      // Prioritize matching commodities to top
      if (aMatch && !bMatch) return -1
      if (!aMatch && bMatch) return 1
      if (!aMatch && !bMatch) return 0 // Keep original order (date desc)
      
      // Sort matching items by price
      return dir === 'asc' ? a.price - b.price : b.price - a.price
    })
  }
  return result
})

function toggleSort(name: string) {
  if (sortCommodity.value === name) {
    // Cycle: none -> asc -> desc -> none
    if (sortDirection.value === 'none') sortDirection.value = 'asc'
    else if (sortDirection.value === 'asc') sortDirection.value = 'desc'
    else {
        sortDirection.value = 'none'
        sortCommodity.value = ''
    }
  } else {
    sortCommodity.value = name
    sortDirection.value = 'asc'
  }
}

function clearSort() {
  sortCommodity.value = ''
  sortDirection.value = 'none'
  showSortPanel.value = false
}

onMounted(() => {
    const token = localStorage.getItem('auth_token')
    fetchOffers(token)
    startPolling(token)
    fetchCommodities()
})

onUnmounted(() => {
    stopPolling()
})
</script>

<template>
  <div class="flex flex-col h-screen bg-gray-50">
    
    <!-- Fixed Header: Filters & Sort -->
    <div class="sticky top-0 z-20 bg-gray-50 pt-4 px-4 pb-2 border-b border-gray-200 shadow-sm">
      
      <!-- Top Row: Filters + Sort Toggle -->
      <div class="flex gap-2 mb-2">
        <!-- Filter Tabs -->
        <div class="flex-1 flex p-1 bg-gray-200 rounded-xl">
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

        <!-- Sort Toggle Button -->
        <button 
          @click="showSortPanel = !showSortPanel"
          class="flex items-center justify-center gap-1.5 px-3 bg-white border border-gray-200 rounded-xl text-xs font-bold text-gray-600 shadow-sm transition-all active:scale-95 hover:bg-gray-50"
          :class="{ 'border-amber-500 text-amber-600 bg-amber-50': showSortPanel || sortDirection !== 'none' }"
        >
          <ArrowUpDown v-if="sortDirection === 'none'" :size="16" />
          <ArrowUp v-else-if="sortDirection === 'asc'" :size="16" />
          <ArrowDown v-else :size="16" />
          <span class="hidden sm:inline whitespace-nowrap">مرتب‌سازی</span>
        </button>
      </div>

      <!-- Sort Panel (Collapsible) -->
      <transition name="slide">
        <div v-if="showSortPanel" class="mb-2 bg-white border border-gray-200 rounded-xl p-3 shadow-inner">
          
          <div class="flex items-center justify-between mb-3">
             <span class="text-xs font-bold text-gray-700">انتخاب کالا برای مرتب‌سازی قیمت:</span>
             <button v-if="sortDirection !== 'none'" @click="clearSort" class="flex items-center gap-1 px-2 py-1 bg-red-50 text-red-600 rounded-lg text-[10px] font-bold hover:bg-red-100 transition-colors">
                <X :size="12" /> حذف فیلتر
             </button>
          </div>

          <div v-if="commoditiesLoading" class="flex justify-center py-2">
             <Loader2 class="animate-spin text-gray-400" :size="20" />
          </div>

          <div v-else class="flex flex-wrap gap-2">
            <button
              v-for="c in commodities"
              :key="c.id"
              @click="toggleSort(c.name)"
              class="flex items-center gap-1.5 px-3 py-1.5 border rounded-full text-xs font-medium transition-all duration-200 active:scale-95"
              :class="sortCommodity === c.name 
                 ? 'bg-amber-50 border-amber-500 text-amber-700 shadow-sm' 
                 : 'bg-white border-gray-200 text-gray-600 hover:border-gray-300'"
            >
              {{ c.name }}
              <span v-if="sortCommodity === c.name && sortDirection === 'asc'" class="font-extrabold text-[13px]">↑</span>
              <span v-if="sortCommodity === c.name && sortDirection === 'desc'" class="font-extrabold text-[13px]">↓</span>
            </button>
          </div>

          <div v-if="sortCommodity && sortDirection !== 'none'" class="mt-2 text-[10px] text-amber-600 font-medium text-center border-t border-gray-100 pt-2">
             {{ sortCommodity }} — {{ sortDirection === 'asc' ? 'ارزان‌ترین اول' : 'گران‌ترین اول' }}
             <span class="text-gray-400 font-normal">(دوباره بزنید برای تغییر جهت)</span>
          </div>

        </div>
      </transition>
    </div>

    <!-- Scrollable Offers List -->
    <div class="flex-1 overflow-y-auto px-4 py-4 pb-24">
      <OffersList :offers="filteredOffers" :loading="isLoading" />
    </div>

  </div>
</template>

<style scoped>
.slide-enter-active,
.slide-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  max-height: 300px;
  overflow: hidden;
  opacity: 1;
}

.slide-enter-from,
.slide-leave-to {
  max-height: 0;
  opacity: 0;
  margin-top: 0;
  padding-top: 0;
  padding-bottom: 0;
  transform: translateY(-8px);
}
</style>
