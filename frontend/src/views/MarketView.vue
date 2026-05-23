<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { ArrowUp, ArrowDown, ArrowUpDown, X, Loader2, Send, ChevronLeft } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import { useTradingSort } from '../composables/useTradingSort'
import { useWebSocket } from '../composables/useWebSocket'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import OffersList from '../components/OffersList.vue'
import OfferPreviewModal from '../components/OfferPreviewModal.vue'
import { apiFetch, apiFetchJson } from '../utils/auth'
import { createHttpErrorFromResponse, getUserFacingErrorMessage } from '../utils/httpErrorPolicy'

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

interface ParsedOfferPreview {
  trade_type: 'buy' | 'sell'
  commodity_id: number
  commodity_name: string
  quantity: number
  price: number
  is_wholesale: boolean
  lot_sizes: number[] | null
  notes: string | null
}

interface OfferPriceWarning {
  error_code: string
  title: string
  detail: string
  message: string
  warning_type: string
  reference_label: string
  reference_price: number
  proposed_price: number
  difference_percent: number
}

interface MarketRuntimeState {
  is_open: boolean
  active_web_notice_visible: boolean
  offers_since_last_open: number
  last_transition_at: string | null
  next_transition_at: string | null
}

type CustomerTierValue = 'tier1' | 'tier2' | null

const MARKET_CLOSED_DETAIL = 'بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید.'

const { offers, isLoading, fetchOffers, startPolling, stopPolling } = useOffers()
const { on: wsOn, off: wsOff } = useWebSocket()
const currentUserId = ref<number | undefined>(undefined)
const currentUserCustomerTier = ref<CustomerTierValue>(null)
const marketRuntime = ref<MarketRuntimeState>({
  is_open: true,
  active_web_notice_visible: false,
  offers_since_last_open: 0,
  last_transition_at: null,
  next_transition_at: null,
})

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

// Text Offer State
const offerText = ref('')
const parseError = ref('')
const isSubmitting = ref(false)
const successMessage = ref('')
const pendingOfferPreview = ref<ParsedOfferPreview | null>(null)
const previewError = ref('')
const previewWarning = ref<OfferPriceWarning | null>(null)
const isTier2Customer = computed(() => currentUserCustomerTier.value === 'tier2')
const isMarketOpen = computed(() => marketRuntime.value.is_open)
const showMarketNotice = computed(() => !marketRuntime.value.is_open || marketRuntime.value.active_web_notice_visible)
const marketNoticeText = computed(() => (marketRuntime.value.is_open ? 'شروع فعالیت بازار' : 'پایان فعالیت بازار'))
const marketInputPlaceholder = computed(() => (isMarketOpen.value ? randomPlaceholder.value : 'بازار بسته است'))

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

async function fetchMarketState() {
    try {
        const res = await apiFetch('/api/trading-settings/market-state')
        if (res.ok) {
            const data = await res.json()
            marketRuntime.value = {
              ...marketRuntime.value,
              ...data,
            }
        }
    } catch (e) {
        console.error('Failed to load market state', e)
    }
}

function applyMarketRuntimePatch(patch: Partial<MarketRuntimeState>) {
  marketRuntime.value = {
    ...marketRuntime.value,
    ...patch,
  }
}

function handleMarketOpened(data: Partial<MarketRuntimeState> | undefined) {
  applyMarketRuntimePatch({
    is_open: true,
    active_web_notice_visible: data?.active_web_notice_visible ?? true,
    offers_since_last_open: data?.offers_since_last_open ?? 0,
    last_transition_at: data?.last_transition_at ?? marketRuntime.value.last_transition_at,
  })
}

function handleMarketClosed(data: Partial<MarketRuntimeState> | undefined) {
  applyMarketRuntimePatch({
    is_open: false,
    active_web_notice_visible: data?.active_web_notice_visible ?? true,
    offers_since_last_open: data?.offers_since_last_open ?? marketRuntime.value.offers_since_last_open,
    last_transition_at: data?.last_transition_at ?? marketRuntime.value.last_transition_at,
  })
}

function handleMarketNoticeHidden(data: Partial<MarketRuntimeState> | undefined) {
  applyMarketRuntimePatch({
    active_web_notice_visible: false,
    offers_since_last_open: data?.offers_since_last_open ?? marketRuntime.value.offers_since_last_open,
    last_transition_at: data?.last_transition_at ?? marketRuntime.value.last_transition_at,
  })
}

function buildOfferCreatePayload(offer: ParsedOfferPreview) {
  return {
    offer_type: offer.trade_type,
    commodity_id: offer.commodity_id,
    quantity: offer.quantity,
    price: offer.price,
    is_wholesale: offer.is_wholesale,
    lot_sizes: offer.lot_sizes,
    notes: offer.notes,
  }
}

function cancelOfferPreview() {
  pendingOfferPreview.value = null
  previewError.value = ''
  previewWarning.value = null
}

async function confirmOfferPreview() {
  if (!pendingOfferPreview.value) return
  if (!isMarketOpen.value) {
    previewError.value = MARKET_CLOSED_DETAIL
    return
  }
  if (isTier2Customer.value) {
    previewError.value = 'مشتری سطح 2 مجاز به ثبت لفظ نیست و فقط می‌تواند روی لفظ‌های دیگر درخواست بزند.'
    return
  }
  isSubmitting.value = true
  previewError.value = ''

  try {
    const response = await apiFetch('/api/offers/', {
      method: 'POST',
      body: JSON.stringify({
        ...buildOfferCreatePayload(pendingOfferPreview.value),
        warning_acknowledged: !!previewWarning.value,
      }),
    })
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      if (response.status === 409 && payload?.error_code === 'OFFER_PRICE_WARNING' && payload?.warning) {
        previewWarning.value = payload.warning as OfferPriceWarning
        return
      }
      throw await createHttpErrorFromResponse(response, {
        surface: 'market',
        scope: 'form',
        operation: 'submit',
        userInitiated: true,
        fallbackMessage: 'ثبت لفظ انجام نشد.',
      }, payload)
    }

    successMessage.value = 'لفظ متنی ثبت شد'
    offerText.value = ''
    pendingOfferPreview.value = null
    previewWarning.value = null
    setTimeout(() => successMessage.value = '', 3000)
    fetchOffers()
  } catch (e: any) {
    previewError.value = getUserFacingErrorMessage(e, {
      surface: 'market',
      scope: 'form',
      operation: 'submit',
      userInitiated: true,
      fallbackMessage: 'خطا در ثبت لفظ',
    })
  } finally {
    isSubmitting.value = false
  }
}

function parseAndSubmitTextOffer() {
  if (isTier2Customer.value) {
    parseError.value = 'مشتری سطح 2 مجاز به ثبت لفظ نیست و فقط می‌تواند روی لفظ‌های دیگر درخواست بزند.'
    return
  }
  if (!isMarketOpen.value) {
    parseError.value = MARKET_CLOSED_DETAIL
    return
  }
  if (!offerText.value.trim()) return
  isSubmitting.value = true
  parseError.value = ''
  previewError.value = ''
  previewWarning.value = null
  
  apiFetchJson('/api/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
    }, {
      surface: 'market',
      scope: 'field',
      operation: 'submit',
      userInitiated: true,
      fallbackMessage: 'خطا در پردازش متن',
    })
  .then(res => {
      if (res.success && res.data) {
          pendingOfferPreview.value = res.data as ParsedOfferPreview
          return null
      } else {
          throw new Error(res.error || 'خطا در پردازش متن')
      }
  })
  .catch(e => parseError.value = getUserFacingErrorMessage(e, {
      surface: 'market',
      scope: 'field',
      operation: 'submit',
      userInitiated: true,
      fallbackMessage: 'خطا در پردازش متن',
  }))
  .finally(() => isSubmitting.value = false)
}

async function fetchCurrentUser() {
    try {
        const res = await apiFetch('/api/auth/me')
        if (res.ok) {
            const data = await res.json()
            currentUserId.value = data.id
      currentUserCustomerTier.value = data.customer_tier === 'tier1' || data.customer_tier === 'tier2'
        ? data.customer_tier
        : null
        }
    } catch (e) {
        console.error('Failed to load current user', e)
    }
}

watch(isTier2Customer, (blocked) => {
  if (!blocked) return
  offerText.value = ''
  pendingOfferPreview.value = null
  previewWarning.value = null
  previewError.value = ''
})

onMounted(() => {
    fetchOffers()
    startPolling()
    fetchCommodities()
    fetchTradingSettings()
  fetchMarketState()
    fetchCurrentUser()
  wsOn('market:opened', handleMarketOpened)
  wsOn('market:closed', handleMarketClosed)
  wsOn('market:notice_hidden', handleMarketNoticeHidden)
})

onUnmounted(() => {
    stopPolling()
    clearBackStack()
  wsOff('market:opened', handleMarketOpened)
  wsOff('market:closed', handleMarketClosed)
  wsOff('market:notice_hidden', handleMarketNoticeHidden)
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

    <transition name="fade">
      <div
        v-if="showMarketNotice"
        class="market-runtime-notice"
        :class="marketRuntime.is_open ? 'market-runtime-notice--open' : 'market-runtime-notice--closed'"
      >
        {{ marketNoticeText }}
      </div>
    </transition>

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

    <OfferPreviewModal
      v-if="pendingOfferPreview"
      :offer="pendingOfferPreview"
      :submitting="isSubmitting"
      :error="previewError"
      :warning="previewWarning"
      @confirm="confirmOfferPreview"
      @cancel="cancelOfferPreview"
    />

    <!-- Bottom Action Bar -->
    <div class="market-action-bar" :class="{ 'market-action-bar--notice': isTier2Customer }">
        <div class="action-bar-inner">
        <template v-if="isTier2Customer">
          <div class="tier2-offer-note">
            <div class="tier2-offer-note-title">ثبت لفظ برای مشتری سطح 2 غیرفعال است</div>
            <div class="tier2-offer-note-text">شما فقط می‌توانید روی لفظ‌های دیگر درخواست بزنید.</div>
          </div>
        </template>
        <template v-else>
          <!-- Text Input Row -->
          <div class="input-wrapper">
            <input 
              v-model="offerText"
              type="text" 
              :placeholder="marketInputPlaceholder"
              class="text-offer-input"
              :disabled="!isMarketOpen || isSubmitting"
              @keydown.enter="parseAndSubmitTextOffer"
            >
            <button 
              @click="parseAndSubmitTextOffer"
              :disabled="!isMarketOpen || !offerText.trim() || isSubmitting"
              class="send-btn"
            >
              <Loader2 v-if="isSubmitting" class="animate-spin" :size="20" />
              <Send v-else :size="20" />
            </button>
          </div>
          <div v-if="parseError" class="parse-error">{{ parseError }}</div>
        </template>
        </div>
    </div>
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

.market-runtime-notice {
  margin: 0.75rem 1rem 0;
  padding: 0.8rem 1rem;
  border-radius: var(--ds-radius-lg);
  font-size: 0.84rem;
  font-weight: 800;
  text-align: center;
  border: 1px solid transparent;
}

.market-runtime-notice--open {
  background: rgba(15, 118, 110, 0.09);
  color: #0f766e;
  border-color: rgba(15, 118, 110, 0.18);
}

.market-runtime-notice--closed {
  background: rgba(185, 28, 28, 0.08);
  color: #b91c1c;
  border-color: rgba(185, 28, 28, 0.16);
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

.market-action-bar--notice {
  background: rgba(255, 248, 235, 0.97);
  border-top-color: rgba(217, 119, 6, 0.18);
}

.tier2-offer-note {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  padding: 0.9rem 1rem;
  border-radius: var(--ds-radius-lg);
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.98), rgba(255, 237, 213, 0.96));
  border: 1px solid rgba(217, 119, 6, 0.2);
  color: var(--ds-warning-700, #9a3412);
  box-shadow: 0 10px 24px rgba(217, 119, 6, 0.08);
}

.tier2-offer-note-title {
  font-size: 0.88rem;
  font-weight: 800;
}

.tier2-offer-note-text {
  font-size: 0.78rem;
  line-height: 1.6;
  color: var(--ds-warning-600, #b45309);
}

.input-wrapper {
  position: relative;
}

.text-offer-input {
  width: 100%;
  padding: 0.75rem 3.5rem 0.75rem 1rem;
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

.text-offer-input:disabled {
  background: var(--ds-bg-disabled);
  color: var(--ds-text-disabled);
  cursor: not-allowed;
}

.send-btn {
  position: absolute;
  right: 0.5rem;
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
