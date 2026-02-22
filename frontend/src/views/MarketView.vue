<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { ArrowUp, ArrowDown, ArrowUpDown, X, Loader2, Send } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
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
const currentUserId = ref<number | undefined>(undefined)
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
  pushBackState(() => { showCreateWizard.value = false })
}

function closeWizard() {
  if (showCreateWizard.value) {
    showCreateWizard.value = false
    popBackState() // کاربر از UI بسته — history entry اضافی حذف شود
  }
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
  apiFetchJson('/api/offers/', {
      method: 'POST',
      body: JSON.stringify(newOffer.value)
  })
  .then(() => {
     successMessage.value = 'لفظ ثبت شد'
     setTimeout(() => successMessage.value = '', 3000)
     closeWizard()
     fetchOffers()
  })
  .catch(e => console.error(e))
  .finally(() => isSubmitting.value = false)
}

function parseAndSubmitTextOffer() {
  if (!offerText.value.trim()) return
  isSubmitting.value = true
  parseError.value = ''
  
  apiFetchJson('/api/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
  })
  .then(res => {
      if (res.success && res.data) {
          return apiFetchJson('/api/offers/', {
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

async function fetchCurrentUser() {
    try {
        const res = await apiFetch('/api/auth/me')
        if (res.ok) {
            const data = await res.json()
            currentUserId.value = data.id
        }
    } catch (e) {
        console.error('Failed to load current user', e)
    }
}

onMounted(() => {
    fetchOffers()
    startPolling()
    fetchCommodities()
    fetchTradingSettings()
    fetchCurrentUser()
})

onUnmounted(() => {
    stopPolling()
})
</script>

<template>
  <div class="market-page flex flex-col h-screen">
    
    <!-- Success/Error Toasts -->
    <transition name="fade">
        <div v-if="successMessage" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-gradient-to-r from-amber-500 to-amber-600 text-white px-5 py-2.5 rounded-2xl text-sm font-bold shadow-lg shadow-amber-500/25">
            {{ successMessage }}
        </div>
    </transition>

    <!-- Fixed Header: Filters & Sort -->
    <div class="sticky top-0 z-20 pt-4 px-4 pb-2 market-header">
      <div class="flex gap-2 mb-2">
        <div class="flex-1 flex p-1 bg-white/60 backdrop-blur-sm rounded-xl border border-amber-100/50">
          <button 
            v-for="tab in ['all', 'buy', 'sell']" 
            :key="tab"
            @click="filterType = tab as any"
            class="flex-1 py-2 text-sm font-bold rounded-lg transition-all duration-200"
            :class="filterType === tab ? 'bg-white shadow-sm text-gray-900' : 'text-gray-500 hover:text-gray-700'"
          >
            {{ tab === 'all' ? 'همه' : (tab === 'buy' ? 'خریدار' : 'فروشنده') }}
          </button>
        </div>

        <button 
          @click="showSortPanel = !showSortPanel"
          class="flex items-center justify-center gap-1.5 px-3 bg-white/80 backdrop-blur-sm border border-amber-100/50 rounded-xl text-xs font-bold text-gray-600 shadow-sm transition-all active:scale-95 hover:bg-white"
          :class="{ 'border-amber-400 text-amber-600 bg-amber-50': showSortPanel || sortDirection !== 'none' }"
        >
          <ArrowUpDown v-if="sortDirection === 'none'" :size="16" />
          <ArrowUp v-else-if="sortDirection === 'asc'" :size="16" />
          <ArrowDown v-else :size="16" />
          <span class="hidden sm:inline whitespace-nowrap">مرتب‌سازی</span>
        </button>
      </div>

      <transition name="slide">
        <div v-if="showSortPanel" class="mb-2 bg-white/90 backdrop-blur-sm border border-amber-100/50 rounded-xl p-3 shadow-sm">
          <div class="flex items-center justify-between mb-3">
             <span class="text-xs font-bold text-gray-700">انتخاب کالا برای مرتب‌سازی قیمت:</span>
             <button v-if="sortDirection !== 'none'" @click="clearSort" class="flex items-center gap-1 px-2 py-1 bg-red-50 text-red-600 rounded-lg text-[10px] font-bold hover:bg-red-100 transition-colors">
                <X :size="12" /> حذف فیلتر
             </button>
          </div>
          <div v-if="commoditiesLoading" class="flex justify-center py-2">
             <Loader2 class="animate-spin text-amber-500" :size="20" />
          </div>
          <div v-else class="flex flex-wrap gap-2">
            <button
              v-for="c in commodities"
              :key="c.id"
              @click="toggleSort(c.name)"
              class="flex items-center gap-1.5 px-3 py-1.5 border rounded-full text-xs font-medium transition-all duration-200 active:scale-95"
              :class="sortCommodity === c.name 
                 ? 'bg-amber-50 border-amber-400 text-amber-700 shadow-sm' 
                 : 'bg-white border-gray-200 text-gray-600 hover:border-amber-200'"
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
    <div class="flex-1 overflow-y-auto px-4 py-4 pb-32 max-w-[480px] mx-auto w-full">
      <OffersList :offers="filteredOffers" :loading="isLoading" :expiry-minutes="tradingSettings.offer_expiry_minutes" :current-user-id="currentUserId" @trade-completed="fetchOffers()" />
    </div>

    <!-- Bottom Action Bar -->
    <div class="fixed bottom-0 left-0 right-0 z-30 market-action-bar px-4 py-3 pb-8 md:pb-4">
        <div class="max-w-[480px] mx-auto w-full flex flex-col gap-3">
            
            <!-- Text Input Row -->
            <div class="relative">
                <input 
                    v-model="offerText"
                    type="text" 
                    :placeholder="randomPlaceholder"
                    class="w-full bg-white/80 border border-amber-100 rounded-2xl py-3 px-4 pl-12 text-sm focus:ring-2 focus:ring-amber-400 focus:border-amber-300 transition-all outline-none"
                    @keydown.enter="parseAndSubmitTextOffer"
                >
                <button 
                    @click="parseAndSubmitTextOffer"
                    :disabled="!offerText.trim() || isSubmitting"
                    class="absolute left-2 top-1/2 -translate-y-1/2 p-2 bg-gradient-to-r from-amber-500 to-amber-600 rounded-xl text-white disabled:bg-gray-300 disabled:from-gray-300 disabled:to-gray-300 disabled:cursor-not-allowed transition-all shadow-sm shadow-amber-500/20"
                >
                    <Loader2 v-if="isSubmitting" class="animate-spin" :size="18" />
                    <Send v-else :size="18" />
                </button>
            </div>
            
            <!-- Parse Error -->
            <div v-if="parseError" class="text-red-500 text-xs px-2 font-medium">{{ parseError }}</div>

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
    <div v-if="showCreateWizard" class="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm" @click.self="closeWizard()">
        <div class="bg-white w-full max-w-sm rounded-3xl overflow-hidden shadow-2xl animate-in fade-in zoom-in-95 duration-200">
            <!-- Wizard Header -->
            <div class="wizard-header px-6 py-4 flex justify-between items-center">
                <h3 class="font-bold text-gray-800">
                    {{ newOffer.offer_type === 'buy' ? '🟢 ثبت سفارش خرید' : '🔴 ثبت سفارش فروش' }}
                </h3>
                <button @click="closeWizard()" class="p-1.5 rounded-xl hover:bg-gray-100 transition-colors">
                    <X :size="20" class="text-gray-400" />
                </button>
            </div>

            <!-- Steps Content -->
            <div class="p-6">
                <!-- Step 1: Commodity -->
                <div v-if="createStep === 'commodity'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">کالای مورد نظر را انتخاب کنید</p>
                     <div class="grid grid-cols-2 gap-3">
                        <button v-for="c in commodities" :key="c.id" @click="selectCommodity(c)" class="p-3 bg-amber-50/50 border border-amber-100 rounded-xl font-bold text-gray-700 hover:bg-amber-50 hover:border-amber-300 active:scale-95 transition-all">
                            {{ c.name }}
                        </button>
                     </div>
                </div>

                <!-- Step 2: Quantity -->
                <div v-if="createStep === 'quantity'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">تعداد را وارد کنید</p>
                     <div class="grid grid-cols-3 gap-2">
                        <button v-for="q in quickQuantities" :key="q" @click="selectQuantity(q)" class="py-2 bg-amber-50/50 border border-amber-100 rounded-lg font-medium hover:bg-amber-50 hover:border-amber-300 active:scale-95 transition-all">
                            {{ q }}
                        </button>
                     </div>
                     <div class="flex gap-2">
                         <input v-model.number="newOffer.quantity" type="number" class="flex-1 bg-gray-50 rounded-xl px-4 py-3 text-center font-bold text-lg focus:ring-2 focus:ring-amber-400 outline-none border border-gray-100" placeholder="تعداد دلخواه">
                         <button @click="confirmQuantity" :disabled="!newOffer.quantity" class="px-6 bg-gradient-to-r from-amber-500 to-amber-600 text-white rounded-xl font-bold disabled:opacity-50 disabled:cursor-not-allowed shadow-sm">تایید</button>
                     </div>
                </div>

                <!-- Step 3: Lot Type -->
                <div v-if="createStep === 'lot'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">نحوه فروش را مشخص کنید</p>
                     <div class="flex flex-col gap-3">
                        <button @click="selectLotType(true)" class="p-4 bg-amber-50/50 border-2 border-amber-100 rounded-2xl font-bold text-amber-800 hover:bg-amber-50 active:scale-95 transition-all text-right">
                            📦 فروش یکجا ({{ newOffer.quantity }} عدد)
                            <span class="block text-xs font-normal text-amber-600 mt-1">خریدار باید کل تعداد را بخرد</span>
                        </button>
                        <button @click="selectLotType(false)" class="p-4 bg-orange-50/50 border-2 border-orange-100 rounded-2xl font-bold text-orange-700 hover:bg-orange-50 active:scale-95 transition-all text-right">
                            🔢 فروش خُرد (قابل تقسیم)
                            <span class="block text-xs font-normal text-orange-500 mt-1">خریدار می‌تواند بخشی از تعداد را بخرد</span>
                        </button>
                     </div>
                </div>

                <!-- Step 4: Lot Input -->
                <div v-if="createStep === 'lotInput'" class="space-y-4">
                     <p class="text-center text-gray-600 font-medium">ترکیب فروش را مشخص کنید</p>
                     <div class="bg-amber-50 text-amber-800 text-xs p-3 rounded-lg text-center border border-amber-100">
                        مجموع باید دقیقاً {{ newOffer.quantity }} عدد باشد
                     </div>
                     <input v-model="lotSizesText" type="text" :placeholder="suggestedLotText" class="w-full bg-gray-50 rounded-xl px-4 py-3 text-center font-bold text-lg focus:ring-2 focus:ring-amber-400 outline-none border border-gray-100 dir-ltr">
                     <button @click="confirmLotSizes" class="w-full py-3 bg-gradient-to-r from-amber-500 to-amber-600 text-white rounded-xl font-bold hover:from-amber-600 hover:to-amber-700 transition-all shadow-sm shadow-amber-500/20">تایید ترکیب</button>
                </div>

                <!-- Step 5: Price -->
                <div v-if="createStep === 'price'" class="space-y-6">
                     <p class="text-center text-gray-600 font-medium">قیمت واحد را وارد کنید (تومان)</p>
                     <div class="relative">
                         <input v-model.number="newOffer.price" type="number" class="w-full bg-gray-50 rounded-2xl px-4 py-4 text-center font-black text-2xl tracking-widest focus:ring-2 focus:ring-amber-400 outline-none border border-gray-100" placeholder="0">
                         <div class="text-center mt-2 text-sm text-gray-400 font-medium" v-if="newOffer.price">
                            {{ newOffer.price.toLocaleString() }} تومان
                         </div>
                     </div>
                     <button @click="submitOffer" :disabled="!newOffer.price || isSubmitting" class="w-full py-4 bg-gradient-to-r from-green-500 to-green-600 text-white rounded-2xl font-bold text-lg shadow-lg shadow-green-500/20 hover:from-green-600 hover:to-green-700 active:scale-95 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2">
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
.market-page {
  min-height: 100dvh;
}

.market-header {
  background: rgba(255, 251, 235, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(245, 158, 11, 0.1);
}

.market-action-bar {
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-top: 1px solid rgba(245, 158, 11, 0.1);
  box-shadow: 0 -4px 16px rgba(0, 0, 0, 0.04);
}

.wizard-header {
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  border-bottom: 1px solid rgba(245, 158, 11, 0.15);
}

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
</style>
