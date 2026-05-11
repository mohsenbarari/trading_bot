<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { ArrowUp, ArrowDown, ArrowUpDown, X, Loader2, Send, ChevronLeft } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import { useTradingSort } from '../composables/useTradingSort'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import OffersList from '../components/OffersList.vue'
import { apiFetch, apiFetchJson } from '../utils/auth'

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

const {
  filterType,
  sortCommodity,
  sortDirection,
  showSortPanel,
  filteredOffers,
  toggleSort,
  clearSort,
} = useTradingSort(offers)

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
    popBackState()
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
  if (parts.some(isNaN)) return
  const sum = parts.reduce((a,b) => a+b, 0)
  if (sum !== newOffer.value.quantity) return
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
    clearBackStack()
})
</script>

<template>
  <div class="market-page ds-page">
    
    <!-- Success Toast -->
    <transition name="fade">
        <div v-if="successMessage" class="ds-toast success">
            {{ successMessage }}
        </div>
    </transition>

    <!-- Header: Filters & Sort -->
    <div class="market-header">
      <div class="header-controls">
        <div class="tabs-container">
          <button 
            v-for="tab in ['all', 'buy', 'sell']" 
            :key="tab"
            @click="filterType = tab as any"
            class="tab-btn"
            :class="{ active: filterType === tab }"
          >
            {{ tab === 'all' ? 'همه' : (tab === 'buy' ? 'خریدار' : 'فروشنده') }}
          </button>
        </div>

        <button 
          @click="showSortPanel = !showSortPanel"
          class="sort-toggle-btn"
          :class="{ active: showSortPanel || sortDirection !== 'none' }"
        >
          <ArrowUpDown v-if="sortDirection === 'none'" :size="18" />
          <ArrowUp v-else-if="sortDirection === 'asc'" :size="18" />
          <ArrowDown v-else :size="18" />
          <span class="btn-label">مرتب‌سازی</span>
        </button>
      </div>

      <transition name="slide">
        <div v-if="showSortPanel" class="sort-panel ds-card">
          <div class="sort-panel-header">
             <span class="panel-title">انتخاب کالا برای مرتب‌سازی قیمت:</span>
             <button v-if="sortDirection !== 'none'" @click="clearSort" class="clear-sort-btn">
                <X :size="14" /> حذف فیلتر
             </button>
          </div>
          
          <div v-if="commoditiesLoading" class="panel-loading">
             <Loader2 class="animate-spin" :size="24" />
          </div>
          
          <div v-else class="commodity-grid">
            <button
              v-for="c in commodities"
              :key="c.id"
              @click="toggleSort(c.name)"
              class="commodity-btn"
              :class="{ active: sortCommodity === c.name }"
            >
              {{ c.name }}
              <span v-if="sortCommodity === c.name && sortDirection === 'asc'" class="dir-arrow">↑</span>
              <span v-if="sortCommodity === c.name && sortDirection === 'desc'" class="dir-arrow">↓</span>
            </button>
          </div>
        </div>
      </transition>
    </div>

    <!-- Offers List -->
    <div class="market-content">
      <div class="content-inner">
        <OffersList 
          :offers="filteredOffers" 
          :loading="isLoading" 
          :expiry-minutes="tradingSettings.offer_expiry_minutes" 
          :current-user-id="currentUserId" 
          @trade-completed="fetchOffers()" 
        />
      </div>
    </div>

    <!-- Bottom Action Bar -->
    <div class="market-action-bar">
        <div class="action-bar-inner">
            <!-- Text Input Row -->
            <div class="input-wrapper">
                <input 
                    v-model="offerText"
                    type="text" 
                    :placeholder="randomPlaceholder"
                    class="text-offer-input"
                    @keydown.enter="parseAndSubmitTextOffer"
                >
                <button 
                    @click="parseAndSubmitTextOffer"
                    :disabled="!offerText.trim() || isSubmitting"
                    class="send-btn"
                >
                    <Loader2 v-if="isSubmitting" class="animate-spin" :size="20" />
                    <Send v-else :size="20" />
                </button>
            </div>
            
            <div v-if="parseError" class="parse-error">{{ parseError }}</div>

            <!-- Action Buttons -->
            <div class="action-buttons">
                <button @click="startCreateOffer('buy')" class="create-btn buy">
                    <span class="btn-icon">🟢</span> ثبت خرید
                </button>
                <button @click="startCreateOffer('sell')" class="create-btn sell">
                    <span class="btn-icon">🔴</span> ثبت فروش
                </button>
            </div>
        </div>
    </div>

    <!-- Wizard Modal -->
    <transition name="fade">
      <div v-if="showCreateWizard" class="wizard-overlay" @click.self="closeWizard()">
          <div class="wizard-modal">
              <div class="wizard-header">
                  <h3 class="wizard-title">
                      {{ newOffer.offer_type === 'buy' ? '🟢 ثبت سفارش خرید' : '🔴 ثبت سفارش فروش' }}
                  </h3>
                  <button @click="closeWizard()" class="close-btn">
                      <X :size="20" />
                  </button>
              </div>

              <div class="wizard-body">
                  <!-- Step 1: Commodity -->
                  <div v-if="createStep === 'commodity'" class="step-content">
                       <p class="step-label">کالای مورد نظر را انتخاب کنید</p>
                       <div class="commodity-selection">
                          <button v-for="c in commodities" :key="c.id" @click="selectCommodity(c)" class="wizard-btn-outline">
                              {{ c.name }}
                          </button>
                       </div>
                  </div>

                  <!-- Step 2: Quantity -->
                  <div v-if="createStep === 'quantity'" class="step-content">
                       <p class="step-label">تعداد را وارد کنید</p>
                       <div class="quick-quantities">
                          <button v-for="q in quickQuantities" :key="q" @click="selectQuantity(q)" class="wizard-btn-quick">
                              {{ q }}
                          </button>
                       </div>
                       <div class="input-group">
                           <input v-model.number="newOffer.quantity" type="number" class="wizard-input" placeholder="تعداد دلخواه">
                           <button @click="confirmQuantity" :disabled="!newOffer.quantity" class="wizard-confirm-btn">تایید</button>
                       </div>
                  </div>

                  <!-- Step 3: Lot Type -->
                  <div v-if="createStep === 'lot'" class="step-content">
                       <p class="step-label">نحوه فروش را مشخص کنید</p>
                       <div class="lot-types">
                          <button @click="selectLotType(true)" class="lot-type-btn wholesale">
                              <span class="type-title">📦 فروش یکجا ({{ newOffer.quantity }} عدد)</span>
                              <span class="type-desc">خریدار باید کل تعداد را بخرد</span>
                          </button>
                          <button @click="selectLotType(false)" class="lot-type-btn retail">
                              <span class="type-title">🔢 فروش خُرد (قابل تقسیم)</span>
                              <span class="type-desc">خریدار می‌تواند بخشی از تعداد را بخرد</span>
                          </button>
                       </div>
                  </div>

                  <!-- Step 4: Lot Input -->
                  <div v-if="createStep === 'lotInput'" class="step-content">
                       <p class="step-label">ترکیب فروش را مشخص کنید</p>
                       <div class="info-alert">
                          مجموع باید دقیقاً {{ newOffer.quantity }} عدد باشد
                       </div>
                       <input v-model="lotSizesText" type="text" :placeholder="suggestedLotText" class="wizard-input ltr">
                       <button @click="confirmLotSizes" class="wizard-primary-btn">تایید ترکیب</button>
                  </div>

                  <!-- Step 5: Price -->
                  <div v-if="createStep === 'price'" class="step-content">
                       <p class="step-label">قیمت واحد را وارد کنید (تومان)</p>
                       <div class="price-input-wrapper">
                           <input v-model.number="newOffer.price" type="number" class="wizard-input big" placeholder="0">
                           <div class="price-preview" v-if="newOffer.price">
                              {{ newOffer.price.toLocaleString() }} تومان
                           </div>
                       </div>
                       <button @click="submitOffer" :disabled="!newOffer.price || isSubmitting" class="wizard-submit-btn">
                          <Loader2 v-if="isSubmitting" class="animate-spin" />
                          <span> ثبت نهایی لفظ {{ newOffer.offer_type === 'buy' ? 'خرید' : 'فروش' }}</span>
                       </button>
                  </div>
              </div>
          </div>
      </div>
    </transition>

  </div>
</template>

<style scoped>
.market-page {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  background: var(--ds-bg-page);
}

/* Header & Filters */
.market-header {
  position: sticky;
  top: 0;
  z-index: 20;
  padding: 1rem 1rem 0.5rem;
  background: var(--ds-bg-card);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--ds-border-light);
}

.header-controls {
  display: flex;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.tabs-container {
  flex: 1;
  display: flex;
  padding: 3px;
  background: var(--ds-bg-inset);
  border-radius: var(--ds-radius-lg);
  border: 1px solid var(--ds-border-light);
}

.tab-btn {
  flex: 1;
  padding: 0.5rem;
  font-size: 0.85rem;
  font-weight: 700;
  border-radius: var(--ds-radius-md);
  color: var(--ds-text-secondary);
  transition: all 0.2s;
}

.tab-btn.active {
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  box-shadow: var(--ds-shadow-sm);
}

.sort-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0 1rem;
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--ds-text-secondary);
  box-shadow: var(--ds-shadow-sm);
  transition: all 0.2s;
}

.sort-toggle-btn.active {
  background: var(--ds-primary-50);
  border-color: var(--ds-primary-400);
  color: var(--ds-primary-600);
}

.sort-toggle-btn .btn-label {
  display: none;
}
@media (min-width: 400px) {
  .sort-toggle-btn .btn-label { display: inline; }
}

/* Sort Panel */
.sort-panel {
  margin-top: 0.5rem;
  padding: 1rem;
  margin-bottom: 0.5rem;
}

.sort-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.panel-title {
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--ds-text-secondary);
}

.clear-sort-btn {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  padding: 0.25rem 0.5rem;
  background: var(--ds-danger-50);
  color: var(--ds-danger-600);
  border-radius: var(--ds-radius-sm);
  font-size: 0.65rem;
  font-weight: 700;
}

.panel-loading {
  display: flex;
  justify-content: center;
  padding: 1rem;
  color: var(--ds-primary-500);
}

.commodity-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.commodity-btn {
  padding: 0.4rem 1rem;
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-full);
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--ds-text-secondary);
  transition: all 0.2s;
}

.commodity-btn.active {
  background: var(--ds-primary-50);
  border-color: var(--ds-primary-400);
  color: var(--ds-primary-700);
  box-shadow: var(--ds-shadow-sm);
}

/* Main Content */
.market-content {
  flex: 1;
  overflow-y: auto;
  padding: 1rem 0 7rem;
}

.content-inner {
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  padding: 0 1rem;
  width: 100%;
}

/* Bottom Action Bar */
.market-action-bar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 30;
  padding: 0.75rem 1rem 2rem;
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-top: 1px solid var(--ds-border-light);
  box-shadow: 0 -4px 20px rgba(0, 0, 0, 0.06);
}

.action-bar-inner {
  max-width: var(--ds-page-max-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.input-wrapper {
  position: relative;
}

.text-offer-input {
  width: 100%;
  padding: 0.75rem 1rem 0.75rem 3.5rem;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  font-size: 0.9rem;
  outline: none;
  transition: all 0.2s;
}

.text-offer-input:focus {
  border-color: var(--ds-primary-400);
  background: var(--ds-bg-card);
  box-shadow: 0 0 0 4px var(--ds-primary-50);
}

.send-btn {
  position: absolute;
  left: 0.5rem;
  top: 50%;
  transform: translateY(-50%);
  padding: 0.5rem;
  background: var(--ds-gradient-primary);
  color: white;
  border-radius: var(--ds-radius-md);
  box-shadow: var(--ds-shadow-sm);
  transition: all 0.2s;
}

.send-btn:disabled {
  background: var(--ds-bg-disabled);
  color: var(--ds-text-disabled);
  box-shadow: none;
}

.parse-error {
  font-size: 0.7rem;
  color: var(--ds-danger-600);
  font-weight: 600;
  padding: 0 0.5rem;
}

.action-buttons {
  display: flex;
  gap: 0.75rem;
}

.create-btn {
  flex: 1;
  padding: 0.85rem;
  border-radius: var(--ds-radius-lg);
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  transition: all 0.2s;
  border: 1px solid transparent;
}

.create-btn.buy {
  background: var(--ds-success-50);
  color: var(--ds-success-700);
  border-color: var(--ds-success-200);
}

.create-btn.sell {
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
  border-color: var(--ds-danger-200);
}

.create-btn:active {
  transform: scale(0.97);
}

/* Wizard Modal */
.wizard-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1rem;
}

.wizard-modal {
  width: 100%;
  max-width: 400px;
  background: var(--ds-bg-card);
  border-radius: var(--ds-radius-xl);
  overflow: hidden;
  box-shadow: var(--ds-shadow-xl);
  animation: modalIn 0.3s cubic-bezier(0.16, 1, 0.3, 1);
}

.wizard-header {
  padding: 1rem 1.5rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--ds-bg-inset);
  border-bottom: 1px solid var(--ds-border-light);
}

.wizard-title {
  font-size: 1rem;
  font-weight: 800;
  color: var(--ds-text-primary);
}

.close-btn {
  padding: 0.5rem;
  color: var(--ds-text-muted);
  border-radius: var(--ds-radius-md);
  transition: all 0.2s;
}

.close-btn:hover {
  background: var(--ds-bg-hover);
  color: var(--ds-text-primary);
}

.wizard-body {
  padding: 1.5rem;
}

.step-content {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.step-label {
  text-align: center;
  font-weight: 700;
  color: var(--ds-text-secondary);
  font-size: 0.9rem;
}

.commodity-selection {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.wizard-btn-outline {
  padding: 1rem;
  background: var(--ds-bg-card);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  font-weight: 700;
  color: var(--ds-text-primary);
  transition: all 0.2s;
}

.wizard-btn-outline:hover {
  border-color: var(--ds-primary-400);
  background: var(--ds-primary-50);
}

.quick-quantities {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.5rem;
}

.wizard-btn-quick {
  padding: 0.6rem;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
  font-weight: 600;
  color: var(--ds-text-secondary);
}

.input-group {
  display: flex;
  gap: 0.5rem;
}

.wizard-input {
  flex: 1;
  padding: 0.75rem;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  text-align: center;
  font-weight: 800;
  font-size: 1.1rem;
  outline: none;
}

.wizard-input.big {
  font-size: 1.75rem;
  padding: 1rem;
}

.wizard-confirm-btn {
  padding: 0 1.5rem;
  background: var(--ds-gradient-primary);
  color: white;
  border-radius: var(--ds-radius-lg);
  font-weight: 800;
  box-shadow: var(--ds-shadow-sm);
}

.lot-types {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.lot-type-btn {
  padding: 1.25rem;
  border: 2px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-xl);
  text-align: right;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  transition: all 0.2s;
}

.lot-type-btn .type-title {
  font-weight: 800;
  font-size: 1rem;
}

.lot-type-btn .type-desc {
  font-size: 0.75rem;
  font-weight: 500;
}

.lot-type-btn.wholesale {
  background: var(--ds-primary-50);
  color: var(--ds-primary-800);
  border-color: var(--ds-primary-100);
}

.lot-type-btn.retail {
  background: #fff7ed;
  color: #9a3412;
  border-color: #ffedd5;
}

.info-alert {
  padding: 0.75rem;
  background: var(--ds-primary-50);
  color: var(--ds-primary-800);
  border: 1px solid var(--ds-primary-100);
  border-radius: var(--ds-radius-md);
  font-size: 0.75rem;
  text-align: center;
  font-weight: 600;
}

.wizard-primary-btn {
  padding: 1rem;
  background: var(--ds-gradient-primary);
  color: white;
  border-radius: var(--ds-radius-lg);
  font-weight: 800;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
}

.price-input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.price-preview {
  text-align: center;
  font-size: 0.9rem;
  font-weight: 700;
  color: var(--ds-primary-600);
}

.wizard-submit-btn {
  width: 100%;
  padding: 1.25rem;
  background: var(--ds-success-500);
  color: white;
  border-radius: var(--ds-radius-xl);
  font-weight: 800;
  font-size: 1.1rem;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  box-shadow: 0 8px 20px rgba(16, 185, 129, 0.2);
}

.wizard-submit-btn:disabled {
  background: var(--ds-bg-disabled);
  box-shadow: none;
}

/* Animations */
@keyframes modalIn {
  from { opacity: 0; transform: scale(0.95) translateY(10px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}

.slide-enter-active, .slide-leave-active {
  transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
  max-height: 400px;
}
.slide-enter-from, .slide-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-10px);
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.ltr { direction: ltr; }
</style>
