<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { ArrowUp, ArrowDown, ArrowUpDown, X, Loader2, Send, ChevronLeft, ChevronDown } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import { useWebSocket } from '../composables/useWebSocket'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import OffersList from '../components/OffersList.vue'
import OfferPreviewModal from '../components/OfferPreviewModal.vue'
import { apiFetch, apiFetchJson } from '../utils/auth'
import { createHttpErrorFromResponse, getUserFacingErrorMessage } from '../utils/httpErrorPolicy'
import { buildOfferDraftText } from '../utils/offerDraftText'

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

interface RecentOfferSummary {
  id: number
  offer_type: 'buy' | 'sell'
  commodity_id: number
  commodity_name: string
  quantity: number
  remaining_quantity: number
  raw_price: number
  price: number
  is_wholesale: boolean
  lot_sizes: number[] | null
  original_lot_sizes: number[] | null
  notes: string | null
  status: 'active' | 'completed' | 'cancelled' | 'expired'
  created_at: string
}

interface MarketRuntimeState {
  is_open: boolean
  active_web_notice_visible: boolean
  offers_since_last_open: number
  last_transition_at: string | null
  next_transition_at: string | null
}

interface AdminMarketMessage {
  id: number
  content: string
  is_active: boolean
  published_at: string
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

const filterType = ref<'all' | 'buy' | 'sell' | 'my'>('all')

const filteredOffers = computed(() => {
  let list = offers.value || []
  if (filterType.value !== 'all') {
    if (filterType.value === 'my') {
      list = list.filter(o => o.is_own_offer)
    } else {
      list = list.filter(o => o.offer_type === filterType.value)
    }
  }
  return list
})

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
const pendingOfferPreview = ref<ParsedOfferPreview | null>(null)
const previewError = ref('')
const previewWarning = ref<OfferPriceWarning | null>(null)
const republishedFromOfferId = ref<number | null>(null)
const recentOffers = ref<RecentOfferSummary[]>([])
const recentOffersOpen = ref(false)
const recentOffersLoading = ref(false)
const recentOffersError = ref('')
const recentOffersRef = ref<HTMLElement | null>(null)
const offerInputRef = ref<HTMLTextAreaElement | null>(null)
const adminMarketMessage = ref<AdminMarketMessage | null>(null)
const adminMarketMessageExpanded = ref(false)
const isTier2Customer = computed(() => currentUserCustomerTier.value === 'tier2')
const isMarketOpen = computed(() => marketRuntime.value.is_open)
const showMarketNotice = computed(() => !marketRuntime.value.is_open || marketRuntime.value.active_web_notice_visible)
const marketNoticeText = computed(() => (marketRuntime.value.is_open ? 'شروع فعالیت بازار' : 'پایان فعالیت بازار'))
const marketInputPlaceholder = computed(() => (isMarketOpen.value ? randomPlaceholder.value : 'بازار بسته است'))
const shouldCollapseAdminMarketMessage = computed(() => (
  !!adminMarketMessage.value
  && isMarketOpen.value
  && filteredOffers.value.length > 0
  && !adminMarketMessageExpanded.value
))

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

  async function fetchAdminMarketMessage() {
    try {
      const res = await apiFetch('/api/admin-messages/market/current')
      if (res.ok) {
        const data = await res.json()
        adminMarketMessage.value = data && typeof data.content === 'string' ? data : null
        adminMarketMessageExpanded.value = false
      }
    } catch (e) {
      console.error('Failed to load admin market message', e)
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

function handleAdminMarketMessagePublished(data: AdminMarketMessage | undefined) {
  adminMarketMessage.value = data && typeof data.content === 'string' ? data : null
  adminMarketMessageExpanded.value = false
}

function toggleAdminMarketMessage() {
  if (!adminMarketMessage.value || !shouldCollapseAdminMarketMessage.value) return
  adminMarketMessageExpanded.value = true
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

function getRecentOfferRepublishQuantity(offer: RecentOfferSummary) {
  if (offer.status === 'active' && offer.remaining_quantity > 0) {
    return offer.remaining_quantity
  }
  return offer.quantity
}

function getRecentOfferRepublishLots(offer: RecentOfferSummary) {
  if (offer.is_wholesale) {
    return null
  }
  if (offer.status === 'active' && offer.lot_sizes?.length) {
    return [...offer.lot_sizes]
  }
  if (offer.original_lot_sizes?.length) {
    return [...offer.original_lot_sizes]
  }
  return offer.lot_sizes ? [...offer.lot_sizes] : null
}

function formatRecentOfferPrice(offer: RecentOfferSummary) {
  return (offer.raw_price || offer.price).toLocaleString('fa-IR')
}

function formatRecentOfferQuantity(offer: RecentOfferSummary) {
  return getRecentOfferRepublishQuantity(offer).toLocaleString('fa-IR')
}

function formatRecentOfferDetails(offer: RecentOfferSummary) {
  const parts: string[] = []

  if (!offer.is_wholesale) {
    const lots = getRecentOfferRepublishLots(offer)
    if (lots?.length) {
      parts.push(`خرد · پله‌ها: ${lots.map((lot) => lot.toLocaleString('fa-IR')).join(' + ')}`)
    } else {
      parts.push('خرد')
    }
  }

  const notes = offer.notes?.trim()
  if (notes) {
    parts.push(`توضیح: ${notes}`)
  }

  return parts.join(' • ')
}

function syncOfferInputHeight() {
  void nextTick(() => {
    const input = offerInputRef.value
    if (!input) return
    input.style.height = '0px'
    const nextHeight = Math.min(input.scrollHeight, 160)
    input.style.height = `${Math.max(nextHeight, 52)}px`
    input.style.overflowY = input.scrollHeight > 160 ? 'auto' : 'hidden'
  })
}

async function fetchRecentOffers(silent = false) {
  if (isTier2Customer.value) {
    recentOffers.value = []
    recentOffersError.value = ''
    return
  }

  if (!silent) {
    recentOffersLoading.value = true
  }
  recentOffersError.value = ''

  try {
    const response = await apiFetch('/api/offers/my?since_hours=1&limit=3&status_filter=expired')
    if (!response.ok) {
      throw await createHttpErrorFromResponse(response, {
        surface: 'market',
        scope: 'field',
        operation: 'load-list',
        preserveExistingData: true,
        fallbackMessage: 'بارگذاری لفظ‌های اخیر ممکن نشد.',
      })
    }
    const payload = await response.json()
    recentOffers.value = Array.isArray(payload) ? payload as RecentOfferSummary[] : []
  } catch (e) {
    recentOffersError.value = getUserFacingErrorMessage(e, {
      surface: 'market',
      scope: 'field',
      operation: 'load-list',
      preserveExistingData: true,
      fallbackMessage: 'بارگذاری لفظ‌های اخیر ممکن نشد.',
    })
  } finally {
    recentOffersLoading.value = false
  }
}

function closeRecentOffersMenu() {
  recentOffersOpen.value = false
}

function handleRecentOffersPointerDown(event: PointerEvent) {
  if (!recentOffersOpen.value) {
    return
  }
  const target = event.target as Node | null
  if (!target || !recentOffersRef.value || recentOffersRef.value.contains(target)) {
    return
  }
  closeRecentOffersMenu()
}

function toggleRecentOffersMenu() {
  if (recentOffersOpen.value) {
    closeRecentOffersMenu()
    return
  }
  recentOffersOpen.value = true
  void fetchRecentOffers()
}

function openRecentOfferPreview(offer: RecentOfferSummary) {
  republishedFromOfferId.value = offer.id
  pendingOfferPreview.value = {
    trade_type: offer.offer_type,
    commodity_id: offer.commodity_id,
    commodity_name: offer.commodity_name,
    quantity: getRecentOfferRepublishQuantity(offer),
    price: offer.raw_price || offer.price,
    is_wholesale: offer.is_wholesale,
    lot_sizes: getRecentOfferRepublishLots(offer),
    notes: offer.notes,
  }
  previewError.value = ''
  previewWarning.value = null
  parseError.value = ''
  closeRecentOffersMenu()
}

function cancelOfferPreview() {
  pendingOfferPreview.value = null
  previewError.value = ''
  previewWarning.value = null
  republishedFromOfferId.value = null
}

function focusOfferInput() {
  void nextTick(() => {
    const input = offerInputRef.value
    if (!input) return
    input.focus()
    const end = input.value.length
    input.setSelectionRange(end, end)
  })
}

function editPendingOfferPreview() {
  if (!pendingOfferPreview.value) return
  offerText.value = buildOfferDraftText(pendingOfferPreview.value)
  parseError.value = ''
  cancelOfferPreview()
  focusOfferInput()
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
        republished_from_id: republishedFromOfferId.value,
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

    offerText.value = ''
    pendingOfferPreview.value = null
    previewWarning.value = null
    republishedFromOfferId.value = null
    fetchOffers()
    void fetchRecentOffers(true)
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
  if (offerText.value.trim() === 'نشد') {
    cancelAllOffers()
    return
  }
  republishedFromOfferId.value = null
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

async function cancelAllOffers() {
  isSubmitting.value = true
  parseError.value = ''
  try {
    const response = await apiFetch('/api/offers/cancel-all', { method: 'POST' })
    if (!response.ok) {
      throw await createHttpErrorFromResponse(response, {
        surface: 'market',
        scope: 'field',
        operation: 'submit',
        userInitiated: true,
        fallbackMessage: 'خطا در لغو لفظ‌ها',
      })
    }
    offerText.value = ''
    fetchOffers()
    void fetchRecentOffers(true)
  } catch (e: any) {
    parseError.value = getUserFacingErrorMessage(e, {
      surface: 'market',
      scope: 'field',
      operation: 'submit',
      userInitiated: true,
      fallbackMessage: 'خطا در لغو لفظ‌ها',
    })
  } finally {
    isSubmitting.value = false
  }
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
  republishedFromOfferId.value = null
  closeRecentOffersMenu()
  recentOffers.value = []
})

watch(offerText, () => {
  syncOfferInputHeight()
})

onMounted(() => {
    fetchOffers()
    startPolling()
    fetchCommodities()
    fetchTradingSettings()
  fetchMarketState()
    fetchAdminMarketMessage()
    fetchCurrentUser()
  document.addEventListener('pointerdown', handleRecentOffersPointerDown)
  wsOn('market:opened', handleMarketOpened)
  wsOn('market:closed', handleMarketClosed)
  wsOn('market:notice_hidden', handleMarketNoticeHidden)
    wsOn('market:admin_message_published', handleAdminMarketMessagePublished)
  syncOfferInputHeight()
})

onUnmounted(() => {
    stopPolling()
    clearBackStack()
  document.removeEventListener('pointerdown', handleRecentOffersPointerDown)
  wsOff('market:opened', handleMarketOpened)
  wsOff('market:closed', handleMarketClosed)
  wsOff('market:notice_hidden', handleMarketNoticeHidden)
  wsOff('market:admin_message_published', handleAdminMarketMessagePublished)
})
</script>

<template>
  <div class="market-page ds-page">

    <!-- Header: Filters -->
    <div class="market-header">
      <div class="header-controls">
        <div class="tabs-container">
          <button 
            v-for="tab in ['all', 'buy', 'sell', 'my']" 
            :key="tab"
            @click="filterType = tab as any"
            class="tab-btn"
            :class="{ active: filterType === tab }"
          >
            {{ tab === 'all' ? 'همه' : (tab === 'buy' ? 'خریدار' : (tab === 'sell' ? 'فروشنده' : 'لفظ های شما')) }}
          </button>
        </div>
      </div>
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

    <section
      v-if="adminMarketMessage"
      class="admin-market-message"
      :class="{ 'admin-market-message--collapsed': shouldCollapseAdminMarketMessage }"
      role="status"
      @click="toggleAdminMarketMessage"
    >
      <div class="admin-market-message-title">پیام مدیریت</div>
      <div class="admin-market-message-body">{{ adminMarketMessage.content }}</div>
      <button
        v-if="shouldCollapseAdminMarketMessage"
        type="button"
        class="admin-market-message-expand"
        @click.stop="adminMarketMessageExpanded = true"
      >
        مشاهده کامل
      </button>
    </section>

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
      @edit="editPendingOfferPreview"
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
          <div ref="recentOffersRef" class="input-wrapper">
            <button
              type="button"
              class="recent-offers-toggle"
              :class="{ 'recent-offers-toggle--open': recentOffersOpen }"
              :disabled="isSubmitting"
              aria-label="نمایش لفظ‌های اخیر"
              @click="toggleRecentOffersMenu"
            >
              <Loader2 v-if="recentOffersLoading" class="animate-spin" :size="17" />
              <ChevronDown v-else :size="17" />
            </button>
            <transition name="recent-offers-dropdown">
              <div v-if="recentOffersOpen" class="recent-offers-dropdown">
                <div v-if="recentOffersLoading" class="recent-offers-state">
                  در حال بارگذاری...
                </div>
                <div v-else-if="recentOffersError" class="recent-offers-state recent-offers-state--error">
                  {{ recentOffersError }}
                </div>
                <div v-else-if="!recentOffers.length" class="recent-offers-state">
                  در یک ساعت گذشته لفظی نداشتید.
                </div>
                <button
                  v-for="offer in recentOffers"
                  :key="offer.id"
                  type="button"
                  class="recent-offer-item"
                  @click="openRecentOfferPreview(offer)"
                >
                  <div class="recent-offer-item-main">
                    <span class="recent-offer-item-badge" :class="offer.offer_type === 'buy' ? 'recent-offer-item-badge--buy' : 'recent-offer-item-badge--sell'">
                      {{ offer.offer_type === 'buy' ? 'خ' : 'ف' }}
                    </span>
                    <span class="recent-offer-item-copy">
                      <span class="recent-offer-item-summary">
                        {{ offer.commodity_name }} · {{ formatRecentOfferQuantity(offer) }} · {{ formatRecentOfferPrice(offer) }}
                      </span>
                      <span v-if="formatRecentOfferDetails(offer)" class="recent-offer-item-details">
                        {{ formatRecentOfferDetails(offer) }}
                      </span>
                    </span>
                  </div>
                </button>
              </div>
            </transition>
            <textarea
              ref="offerInputRef"
              v-model="offerText"
              :placeholder="marketInputPlaceholder"
              class="text-offer-input"
              rows="1"
              :disabled="!isMarketOpen || isSubmitting"
              @input="syncOfferInputHeight"
              @keydown.enter.prevent="parseAndSubmitTextOffer"
            ></textarea>
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

.admin-market-message {
  margin: 0.75rem 1rem 0;
  padding: 0.9rem 1rem;
  border-radius: var(--ds-radius-lg);
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.98), rgba(236, 253, 245, 0.95));
  border: 1px solid rgba(217, 119, 6, 0.22);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
  color: var(--ds-text-primary);
}

.admin-market-message-title {
  font-size: 0.82rem;
  font-weight: 950;
  color: #92400e;
  margin-bottom: 0.45rem;
}

.admin-market-message-body {
  white-space: pre-wrap;
  line-height: 1.85;
  font-size: 0.88rem;
  font-weight: 700;
}

.admin-market-message--collapsed {
  cursor: pointer;
}

.admin-market-message--collapsed .admin-market-message-body {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.admin-market-message-expand {
  margin-top: 0.6rem;
  color: #0f766e;
  font-size: 0.78rem;
  font-weight: 900;
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

.recent-offers-toggle {
  position: absolute;
  left: 0.94rem;
  bottom: 0.58rem;
  width: 2.1rem;
  height: 2.1rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  color: var(--ds-accent, #b45309);
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.14);
  box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
  transition: transform 0.2s ease, background 0.2s ease, color 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}

.recent-offers-toggle:hover:not(:disabled),
.recent-offers-toggle--open {
  background: rgba(245, 158, 11, 0.14);
  color: var(--ds-text-primary);
  border-color: rgba(245, 158, 11, 0.22);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.1);
}

.recent-offers-toggle--open {
  transform: rotate(180deg);
}

.recent-offers-toggle:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.recent-offers-dropdown {
  position: absolute;
  left: 0;
  bottom: calc(100% + 0.65rem);
  width: min(19rem, calc(100vw - 2rem));
  padding: 0.45rem;
  display: grid;
  gap: 0.3rem;
  border-radius: 1rem;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--ds-border-light);
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.14);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
}

.recent-offers-state {
  padding: 0.8rem 0.7rem;
  font-size: 0.76rem;
  line-height: 1.7;
  color: var(--ds-text-secondary);
}

.recent-offers-state--error {
  color: var(--ds-danger-600);
}

.recent-offer-item {
  display: grid;
  gap: 0.25rem;
  padding: 0.7rem 0.75rem;
  text-align: right;
  border-radius: 0.85rem;
  background: transparent;
  transition: background 0.18s ease, transform 0.18s ease;
}

.recent-offer-item:hover {
  background: var(--ds-bg-page);
}

.recent-offer-item:active {
  transform: scale(0.985);
}

.recent-offer-item-main {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  min-width: 0;
}

.recent-offer-item-copy {
  min-width: 0;
  display: grid;
  gap: 0.16rem;
}

.recent-offer-item-badge {
  flex: 0 0 auto;
  width: 1.45rem;
  height: 1.45rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 900;
}

.recent-offer-item-badge--buy {
  background: var(--ds-success-50);
  color: var(--ds-success-700);
}

.recent-offer-item-badge--sell {
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
}

.recent-offer-item-summary {
  min-width: 0;
  font-size: 0.8rem;
  font-weight: 700;
  line-height: 1.55;
  color: var(--ds-text-primary);
}

.recent-offer-item-details {
  min-width: 0;
  font-size: 0.7rem;
  line-height: 1.6;
  color: var(--ds-text-secondary);
  word-break: break-word;
}

.recent-offer-item-time {
  font-size: 0.68rem;
  color: var(--ds-text-tertiary, #64748b);
}

.text-offer-input {
  width: 100%;
  min-height: 3.25rem;
  max-height: 10rem;
  padding: 0.82rem 3.7rem 0.82rem 3.9rem;
  background: var(--ds-bg-inset);
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-lg);
  font-size: 0.9rem;
  line-height: 1.75;
  outline: none;
  resize: none;
  white-space: pre-wrap;
  word-break: break-word;
  transition: all 0.2s;
}

.text-offer-input::selection {
  background: rgba(245, 158, 11, 0.28);
  color: var(--ds-text-primary, #0f172a);
}

.text-offer-input::-moz-selection {
  background: rgba(245, 158, 11, 0.28);
  color: var(--ds-text-primary, #0f172a);
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
  bottom: 0.5rem;
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

.recent-offers-dropdown-enter-active,
.recent-offers-dropdown-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.recent-offers-dropdown-enter-from,
.recent-offers-dropdown-leave-to {
  opacity: 0;
  transform: translateY(0.35rem);
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
