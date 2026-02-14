<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { ArrowUp, ArrowDown, ArrowUpDown, X, Loader2, Send } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import OffersList from '../components/OffersList.vue'

interface Commodity {
  id: number
  name: string
}

interface TradingSettings {
  offer_min_quantity: number
  offer_max_quantity: number
  lot_min_size: number
  lot_max_count: number
  offer_expiry_minutes: number
}

const { offers, isLoading, fetchOffers, startPolling, stopPolling } = useOffers()
const filterType = ref<'all' | 'buy' | 'sell'>('all')

// Sort State
const sortCommodity = ref('')
const sortDirection = ref<'none' | 'asc' | 'desc'>('none')
const showSortPanel = ref(false)
const commodities = ref<Commodity[]>([])
const commoditiesLoading = ref(false)
const tradingSettings = ref<TradingSettings>({
  offer_min_quantity: 1,
  offer_max_quantity: 1000,
  lot_min_size: 5,
  lot_max_count: 5,
  offer_expiry_minutes: 60
})

// Offer Creation State
const showCreateWizard = ref(false)
const createStep = ref<'commodity' | 'quantity' | 'lot' | 'lotInput' | 'price' | 'notes' | 'preview'>('commodity')
const newOffer = ref({
  offer_type: '' as 'buy' | 'sell' | '',
  commodity_id: 0,
  commodity_name: '',
  quantity: null as number | null,
  price: null as number | null,
  is_wholesale: true,
  lot_sizes: null as number[] | null,
  notes: '',
  republished_from_id: null as number | null
})
const lotSizesText = ref('')
const suggestedLotText = ref('')
const quickQuantities = [10, 20, 30, 40, 50, 100]

// Text Offer State
const offerText = ref('')
const parseError = ref('')
const isSubmitting = ref(false)
const successMessage = ref('')

// Computed
const randomPlaceholder = computed(() => {
  if (!commodities.value || commodities.value.length === 0) {
    return 'مثال: خرید سکه 30 عدد 125000'
  }
  const comm = commodities.value[Math.floor(Math.random() * commodities.value.length)]
  return `خرید ${comm?.name || 'کالا'} 50 عدد 125000`
})

const filteredOffers = computed(() => {
  let result = [...offers.value]
  if (filterType.value !== 'all') {
    result = result.filter(o => o.offer_type === filterType.value)
  }
  if (sortCommodity.value && sortDirection.value !== 'none') {
    const commodity = sortCommodity.value
    const dir = sortDirection.value
    result.sort((a, b) => {
      const aMatch = a.commodity_name === commodity
      const bMatch = b.commodity_name === commodity
      if (aMatch && !bMatch) return -1
      if (!aMatch && bMatch) return 1
      if (!aMatch && !bMatch) return 0
      return dir === 'asc' ? a.price - b.price : b.price - a.price
    })
  }
  return result
})

import { apiFetch, apiFetchJson } from '../utils/auth'

// API Helpers
async function fetchCommodities() {
    commoditiesLoading.value = true
    try {
        const res = await apiFetch('/api/commodities/')
        if (res.ok) commodities.value = await res.json()
    } catch (e) {
        console.error('Failed to load commodities', e)
    } finally {
        commoditiesLoading.value = false
    }
}

async function fetchTradingSettings() {
    try {
        const res = await apiFetch('/api/trading-settings/')
        if (res.ok) tradingSettings.value = await res.json()
    } catch (e) {
        console.error('Failed to load settings', e)
    }
}

// Wizard Logic
function startCreateOffer(type: 'buy' | 'sell') {
  newOffer.value = {
    offer_type: type,
    commodity_id: 0,
    commodity_name: '',
    quantity: null,
    price: null,
    is_wholesale: true,
    lot_sizes: null,
    notes: '',
    republished_from_id: null
  }
  createStep.value = 'commodity'
  showCreateWizard.value = true
}

function selectCommodity(c: Commodity) {
  newOffer.value.commodity_id = c.id
  newOffer.value.commodity_name = c.name
  createStep.value = 'quantity'
}

function selectQuantity(q: number) {
  newOffer.value.quantity = q
  createStep.value = 'lot'
}

function confirmQuantity() {
  if (!newOffer.value.quantity) return
  createStep.value = 'lot'
}

function selectLotType(wholesale: boolean) {
  newOffer.value.is_wholesale = wholesale
  if (wholesale) {
    newOffer.value.lot_sizes = null
    createStep.value = 'price'
  } else {
    createStep.value = 'lotInput'
    const q = newOffer.value.quantity || 0
    if (q > 0) suggestedLotText.value = `${Math.floor(q/2)} ${q - Math.floor(q/2)}`
    else suggestedLotText.value = ''
    lotSizesText.value = ''
  }
}

function confirmLotSizes() {
  const txt = lotSizesText.value || suggestedLotText.value
  if (!txt) return
  const parts = txt.trim().split(/\s+/).map(Number)
  if (parts.some(isNaN)) return // Add error handling if needed
  const sum = parts.reduce((a,b) => a+b, 0)
  if (sum !== newOffer.value.quantity) return // Add validation error
  newOffer.value.lot_sizes = parts
  createStep.value = 'price'
}

function submitOffer() {
  if (!newOffer.value.price) return
  isSubmitting.value = true
  apiFetchJson('/offers/', {
      method: 'POST',
      body: JSON.stringify(newOffer.value)
  })
  .then(() => {
     successMessage.value = 'لفظ ثبت شد'
     setTimeout(() => successMessage.value = '', 3000)
     showCreateWizard.value = false
     fetchOffers()
  })
  .catch(e => console.error(e))
  .finally(() => isSubmitting.value = false)
}

function parseAndSubmitTextOffer() {
  if (!offerText.value.trim()) return
  isSubmitting.value = true
  parseError.value = ''
  
  apiFetchJson('/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
  })
  .then(res => {
      if (res.success && res.data) {
          return apiFetchJson('/offers/', {
             method: 'POST',
             body: JSON.stringify({
                offer_type: res.data.trade_type,
                commodity_id: res.data.commodity_id,
                quantity: res.data.quantity,
                price: res.data.price,
                is_wholesale: res.data.is_wholesale,
                lot_sizes: res.data.lot_sizes,
                notes: res.data.notes
             })
          })
      } else {
          throw new Error(res.error || 'خطا در پردازش متن')
      }
  })
  .then(() => {
      successMessage.value = 'لفظ متنی ثبت شد'
      offerText.value = ''
      setTimeout(() => successMessage.value = '', 3000)
      fetchOffers()
  })
  .catch(e => parseError.value = e.message)
  .finally(() => isSubmitting.value = false)
}

function toggleSort(name: string) {
  if (sortCommodity.value === name) {
    if (sortDirection.value === 'none') sortDirection.value = 'asc'
    else if (sortDirection.value === 'asc') sortDirection.value = 'desc'
    else { sortDirection.value = 'none'; sortCommodity.value = '' }
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
    fetchOffers()
    startPolling()
    fetchCommodities()
    fetchTradingSettings()
})

onUnmounted(() => {
    stopPolling()
})
</script>

<template>
  <div class="flex flex-col h-screen bg-gray-50">
    
    <!-- Success/Error Toasts -->
    <transition name="fade">
        <div v-if="successMessage" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-green-500 text-white px-4 py-2 rounded-full text-sm font-medium shadow-lg">
            {{ successMessage }}
        </div>
    </transition>

    <!-- Fixed Header: Filters & Sort -->
    <div class="sticky top-0 z-20 bg-gray-50 pt-4 px-4 pb-2 border-b border-gray-200 shadow-sm">
      <div class="flex gap-2 mb-2">
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
        </div>
      </transition>
    </div>

    <!-- Scrollable Offers List -->
    <div class="flex-1 overflow-y-auto px-4 py-4 pb-32">
      <OffersList :offers="filteredOffers" :loading="isLoading" />
    </div>

    <!-- Bottom Action Bar -->
    <div class="fixed bottom-0 left-0 right-0 z-30 bg-white border-t border-gray-200 px-4 py-3 pb-8 md:pb-4 shadow-lg-up">
        <div class="max-w-md mx-auto w-full flex flex-col gap-3">
            
            <!-- Text Input Row -->
            <div class="relative">
                <input 
                    v-model="offerText"
                    type="text" 
                    :placeholder="randomPlaceholder"
                    class="w-full bg-gray-100 border-none rounded-2xl py-3 px-4 pl-12 text-sm focus:ring-2 focus:ring-blue-500 transition-all"
                    @keydown.enter="parseAndSubmitTextOffer"
                >
                <button 
                    @click="parseAndSubmitTextOffer"
                    :disabled="!offerText.trim() || isSubmitting"
                    class="absolute left-2 top-1/2 -translate-y-1/2 p-2 bg-blue-600 rounded-xl text-white disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
                >
                    <Loader2 v-if="isSubmitting" class="animate-spin" :size="18" />
                    <Send v-else :size="18" />
                </button>
            </div>
            
            <!-- Parse Error -->
            <div v-if="parseError" class="text-red-500 text-xs px-2">{{ parseError }}</div>

            <!-- Action Buttons -->
            <div class="flex gap-3">
                <button @click="startCreateOffer('buy')" class="flex-1 bg-green-50 text-green-700 border border-green-200 py-3 rounded-2xl font-bold flex items-center justify-center gap-2 hover:bg-green-100 active:scale-95 transition-all">
                    <span>🟢</span> ثبت خرید
                </button>
                <button @click="startCreateOffer('sell')" class="flex-1 bg-red-50 text-red-700 border border-red-200 py-3 rounded-2xl font-bold flex items-center justify-center gap-2 hover:bg-red-100 active:scale-95 transition-all">
                    <span>🔴</span> ثبت فروش
                </button>
            </div>
        </div>
    </div>

    <!-- Wizard Modal -->
    <div v-if="showCreateWizard" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" @click.self="showCreateWizard = false">
        <div class="bg-white w-full max-w-sm rounded-3xl overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-200">
            <!-- Wizard Header -->
            <div class="bg-gray-50 px-6 py-4 flex justify-between items-center border-b border-gray-100">
                <h3 class="font-bold text-gray-800">
                    {{ newOffer.offer_type === 'buy' ? '🟢 ثبت سفارش خرید' : '🔴 ثبت سفارش فروش' }}
                </h3>
                <button @click="showCreateWizard = false" class="p-1 rounded-full hover:bg-gray-200 transition-colors">
                    <X :size="20" class="text-gray-500" />
                </button>
            </div>

            <!-- Steps Content -->
            <div class="p-6">
                <!-- Step 1: Commodity -->
                <div v-if="createStep === 'commodity'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">کالای مورد نظر را انتخاب کنید</p>
                     <div class="grid grid-cols-2 gap-3">
                        <button v-for="c in commodities" :key="c.id" @click="selectCommodity(c)" class="p-3 bg-gray-50 border border-gray-200 rounded-xl font-bold text-gray-700 hover:bg-blue-50 hover:border-blue-200 active:scale-95 transition-all">
                            {{ c.name }}
                        </button>
                     </div>
                </div>

                <!-- Step 2: Quantity -->
                <div v-if="createStep === 'quantity'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">تعداد را وارد کنید</p>
                     <div class="grid grid-cols-3 gap-2">
                        <button v-for="q in quickQuantities" :key="q" @click="selectQuantity(q)" class="py-2 bg-gray-50 border border-gray-200 rounded-lg font-medium hover:bg-blue-50 hover:border-blue-200 active:scale-95 transition-all">
                            {{ q }}
                        </button>
                     </div>
                     <div class="flex gap-2">
                         <input v-model.number="newOffer.quantity" type="number" class="flex-1 bg-gray-100 rounded-xl px-4 py-3 text-center font-bold text-lg focus:ring-2 focus:ring-blue-500 outline-none" placeholder="تعداد دلخواه">
                         <button @click="confirmQuantity" :disabled="!newOffer.quantity" class="px-6 bg-blue-600 text-white rounded-xl font-bold disabled:opacity-50 disabled:cursor-not-allowed">تایید</button>
                     </div>
                </div>

                <!-- Step 3: Lot Type -->
                <div v-if="createStep === 'lot'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">نحوه فروش را مشخص کنید</p>
                     <div class="flex flex-col gap-3">
                        <button @click="selectLotType(true)" class="p-4 bg-purple-50 border-2 border-purple-100 rounded-2xl font-bold text-purple-700 hover:bg-purple-100 active:scale-95 transition-all text-right">
                            📦 فروش یکجا ({{ newOffer.quantity }} عدد)
                            <span class="block text-xs font-normal text-purple-500 mt-1">خریدار باید کل تعداد را بخرد</span>
                        </button>
                        <button @click="selectLotType(false)" class="p-4 bg-orange-50 border-2 border-orange-100 rounded-2xl font-bold text-orange-700 hover:bg-orange-100 active:scale-95 transition-all text-right">
                            🔢 فروش خُرد (قابل تقسیم)
                            <span class="block text-xs font-normal text-orange-500 mt-1">خریدار می‌تواند بخشی از تعداد را بخرد</span>
                        </button>
                     </div>
                </div>

                <!-- Step 4: Lot Input -->
                <div v-if="createStep === 'lotInput'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">ترکیب فروش را مشخص کنید</p>
                     <div class="bg-yellow-50 text-yellow-800 text-xs p-3 rounded-lg text-center">
                        مجموع باید دقیقاً {{ newOffer.quantity }} عدد باشد
                     </div>
                     <input v-model="lotSizesText" type="text" :placeholder="suggestedLotText" class="w-full bg-gray-100 rounded-xl px-4 py-3 text-center font-bold text-lg focus:ring-2 focus:ring-blue-500 outline-none dir-ltr">
                     <button @click="confirmLotSizes" class="w-full py-3 bg-blue-600 text-white rounded-xl font-bold hover:bg-blue-700 transition-colors">تایید ترکیب</button>
                </div>

                <!-- Step 5: Price -->
                <div v-if="createStep === 'price'" class="space-y-6">
                     <p class="text-center text-gray-600 font-medium">قیمت واحد را وارد کنید (تومان)</p>
                     <div class="relative">
                         <input v-model.number="newOffer.price" type="number" class="w-full bg-gray-100 rounded-2xl px-4 py-4 text-center font-black text-2xl tracking-widest focus:ring-2 focus:ring-blue-500 outline-none" placeholder="0">
                         <div class="text-center mt-2 text-sm text-gray-400 font-medium" v-if="newOffer.price">
                            {{ newOffer.price.toLocaleString() }} تومان
                         </div>
                     </div>
                     <button @click="submitOffer" :disabled="!newOffer.price || isSubmitting" class="w-full py-4 bg-green-600 text-white rounded-2xl font-bold text-lg shadow-lg shadow-green-200 hover:bg-green-700 active:scale-95 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2">
                        <Loader2 v-if="isSubmitting" class="animate-spin" />
                        <span> ثبت نهایی لفظ {{ newOffer.offer_type === 'buy' ? 'خرید' : 'فروش' }}</span>
                     </button>
                </div>

            </div>
        </div>
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

.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.shadow-lg-up {
    box-shadow: 0 -4px 6px -1px rgba(0, 0, 0, 0.05), 0 -2px 4px -1px rgba(0, 0, 0, 0.03);
}
</style>
