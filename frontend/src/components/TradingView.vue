<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted } from 'vue'
import LoadingSkeleton from './LoadingSkeleton.vue'
import { useWebSocket } from '../composables/useWebSocket'
import { apiFetchJson } from '../utils/auth'

const { connect: wsConnect, on: wsOn, off: wsOff } = useWebSocket()

// Props
const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  user: any
  initialTab?: 'offers' | 'my_offers' | 'my_trades'
}>()

// Emits
const emit = defineEmits<{
  (e: 'navigate', view: string, payload?: any): void
}>()

// Interfaces
interface Commodity {
  id: number
  name: string
}

interface Offer {
  id: number
  user_id: number
  user_account_name: string
  offer_type: 'buy' | 'sell'
  commodity_id: number
  commodity_name: string
  quantity: number
  remaining_quantity: number
  price: number
  is_wholesale: boolean
  lot_sizes: number[] | null
  notes: string | null
  status: string
  channel_message_id: number | null
  created_at: string
  expires_at_ts?: number
}

interface Trade {
  id: number
  trade_number: number
  trade_type: string
  commodity_name: string
  quantity: number
  price: number
  offer_user_id: number | null
  offer_user_name: string | null
  responder_user_id: number | null
  responder_user_name: string | null
  created_at: string
}

interface TradingSettings {
  offer_min_quantity: number
  offer_max_quantity: number
  lot_min_size: number
  lot_max_count: number
  offer_expiry_minutes: number
}

// State
const activeTab = ref<'offers' | 'my_offers' | 'my_trades'>(props.initialTab || 'offers')
const isLoading = ref(true)
const error = ref('')
const successMessage = ref('')

// Data Lists
const offers = ref<Offer[]>([])
const myOffers = ref<Offer[]>([])
const myTrades = ref<Trade[]>([])
const commodities = ref<Commodity[]>([])
const tradingSettings = ref<TradingSettings>({
  offer_min_quantity: 1,
  offer_max_quantity: 1000,
  lot_min_size: 5,
  lot_max_count: 5,
  offer_expiry_minutes: 60
})

// Filter & Sort
const filterType = ref<'all' | 'buy' | 'sell'>('all')
const sortCommodity = ref('')
const sortDirection = ref<'none' | 'asc' | 'desc'>('none')
const showSortPanel = ref(false)

// Create Wizard State
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
// Lot input
const lotSizesText = ref('')
const suggestedLotText = ref('')
const quickQuantities = [10, 20, 30, 40, 50, 100]

// Text Offer Mode
const offerText = ref('')
const parseError = ref('')

// Trade Modal State
const showTradeModal = ref(false)
const selectedOffer = ref<Offer | null>(null)
const tradeQuantity = ref(0)
const isTrading = ref(false)

// Polling
let pollingInterval: number | null = null
let timerTick: number | null = null

// Global timer tick (shared across all offer cards)
const now = ref(Math.floor(Date.now() / 1000))

// --- Timer Helpers (pure graphical, no numbers) ---
function getTimerPercent(offer: Offer): number {
  if (!offer.expires_at_ts) return 100
  const remaining = offer.expires_at_ts - now.value
  if (remaining <= 0) return 0
  const total = tradingSettings.value.offer_expiry_minutes * 60
  return Math.min(Math.max((remaining / total) * 100, 0), 100)
}

function getTimerColor(pct: number): [number, number, number] {
  // Returns [hue, saturation, lightness] for beautiful multi-stop spectrum
  // Emerald → Green → Gold → Orange → Red
  if (pct >= 75) {
    const t = (pct - 75) / 25
    return [Math.round(120 + t * 40), 78, 42]  // 120→160 emerald/teal
  } else if (pct >= 50) {
    const t = (pct - 50) / 25
    return [Math.round(50 + t * 70), 82, 46]   // 50→120 gold→green
  } else if (pct >= 25) {
    const t = (pct - 25) / 25
    return [Math.round(25 + t * 25), 88, 48]   // 25→50 orange→gold
  } else {
    const t = pct / 25
    return [Math.round(t * 25), 92, 48]         // 0→25 red→orange
  }
}

function hsl(c: [number, number, number]): string {
  return `hsl(${c[0]}, ${c[1]}%, ${c[2]}%)`
}

function hsla(c: [number, number, number], a: number): string {
  return `hsla(${c[0]}, ${c[1]}%, ${c[2]}%, ${a.toFixed(2)})`
}

function getCardTimerStyle(offer: Offer): Record<string, string> {
  if (!offer.expires_at_ts) return {}
  const pct = getTimerPercent(offer)
  const c = getTimerColor(pct)
  const glowOpacity = Math.max(0.15, (pct / 100) * 0.45)
  const glowSpread = Math.round(2 + (pct / 100) * 8)
  return {
    '--timer-pct': pct + '%',
    '--timer-color': hsl(c),
    '--timer-color-glow': hsla(c, glowOpacity),
    '--timer-color-glow-inner': hsla(c, glowOpacity * 0.5),
    '--timer-color-light': hsla(c, 0.7),
    '--timer-glow-spread': glowSpread + 'px',
    '--timer-glow-strong': hsla(c, Math.min(glowOpacity * 1.8, 0.6)),
    '--timer-glow-subtle': hsla(c, 0.15)
  }
}

// Computed
const filteredOffers = computed(() => {
  let result = offers.value
  
  // Filter by type
  if (filterType.value !== 'all') {
    result = result.filter(o => o.offer_type === filterType.value)
  }
  
  // Sort by price for selected commodity
  if (sortCommodity.value && sortDirection.value !== 'none') {
    const commodity = sortCommodity.value
    const dir = sortDirection.value
    result = [...result].sort((a, b) => {
      const aMatch = a.commodity_name === commodity
      const bMatch = b.commodity_name === commodity
      // Non-matching commodities go to end
      if (aMatch && !bMatch) return -1
      if (!aMatch && bMatch) return 1
      if (!aMatch && !bMatch) return 0
      // Both match: sort by price
      return dir === 'asc' ? a.price - b.price : b.price - a.price
    })
  }
  
  return result
})

function toggleSort(commodity: string) {
  if (sortCommodity.value === commodity) {
    // Cycle: none -> asc -> desc -> none
    if (sortDirection.value === 'none') sortDirection.value = 'asc'
    else if (sortDirection.value === 'asc') sortDirection.value = 'desc'
    else { sortDirection.value = 'none'; sortCommodity.value = '' }
  } else {
    sortCommodity.value = commodity
    sortDirection.value = 'asc'
  }
}

function clearSort() {
  sortCommodity.value = ''
  sortDirection.value = 'none'
  showSortPanel.value = false
}

const randomPlaceholder = computed(() => {
  if (!commodities.value || commodities.value.length === 0) {
    return 'مثال: خرید سکه 30 عدد 125000'
  }
  const comm = commodities.value[Math.floor(Math.random() * commodities.value.length)]
  return `خرید ${comm?.name || 'کالا'} 50 عدد 125000`
})

// API Helper — uses shared apiFetchJson from auth.ts
function api(endpoint: string, options: RequestInit = {}) {
  return apiFetchJson(`/api${endpoint}`, options)
}


// Load Functions
async function loadOffers(silent = false) {
  if (!silent) {
      isLoading.value = true
      error.value = ''
  }
  try {
    offers.value = await api('/offers/')
  } catch (e: any) {
    console.error(e)
    if (!silent) error.value = 'خطا در دریافت لیست لفظ‌ها'
  } finally {
    if (!silent) isLoading.value = false
  }
}

async function loadMyOffers(silent = false) {
  if (!silent) {
      isLoading.value = true
      error.value = ''
  }
  try {
    myOffers.value = await api('/offers/my?since_hours=2')
  } catch (e: any) {
    console.error(e)
    if (!silent) error.value = 'خطا در دریافت لفظ‌های من'
  } finally {
    if (!silent) isLoading.value = false
  }
}

async function loadMyTrades(silent = false) {
  if (!silent) {
      isLoading.value = true
      error.value = ''
  }
  try {
    myTrades.value = await api('/trades/my')
  } catch (e: any) {
    console.error(e)
    if (!silent) error.value = 'خطا در دریافت صورت معاملات'
  } finally {
    if (!silent) isLoading.value = false
  }
}

async function loadCommodities() {
  try {
    commodities.value = await api('/commodities/')
  } catch (e) {
      console.error('Failed to load commodities', e)
  }
}

async function loadTradingSettings() {
  try {
    tradingSettings.value = await api('/trading-settings/')
  } catch (e) {
      console.error('Failed to load settings', e)
  }
}

// Wizard Functions
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
  error.value = ''
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
  if (!newOffer.value.quantity) {
    error.value = 'تعداد الزامی است'
    return
  }
  error.value = ''
  createStep.value = 'lot'
}

function selectLotType(wholesale: boolean) {
  newOffer.value.is_wholesale = wholesale
  if (wholesale) {
    newOffer.value.lot_sizes = null
    createStep.value = 'price'
  } else {
    createStep.value = 'lotInput'
    // pre-fill suggested logic if needed
    const q = newOffer.value.quantity || 0
    if (q > 0) suggestedLotText.value = `${Math.floor(q/2)} ${q - Math.floor(q/2)}`
  }
}

function confirmLotSizes() {
  if (!lotSizesText.value && !suggestedLotText.value) {
    error.value = 'لطفا ترکیب را وارد کنید'
    return
  }
  const txt = lotSizesText.value || suggestedLotText.value
  // basic validation
  const parts = txt.trim().split(/\s+/).map(Number)
  if (parts.some(isNaN)) {
    error.value = 'اعداد نامعتبر'
    return
  }
  const sum = parts.reduce((a,b) => a+b, 0)
  if (sum !== newOffer.value.quantity) {
    error.value = `مجموع (${sum}) با تعداد (${newOffer.value.quantity}) برابر نیست`
    return
  }
  newOffer.value.lot_sizes = parts
  error.value = ''
  createStep.value = 'price'
}

function confirmPrice() {
  if (!newOffer.value.price) {
    error.value = 'قیمت الزامی است'
    return
  }
  error.value = ''
  createStep.value = 'notes'
}

function confirmNotes() {
  createStep.value = 'preview'
}

function closeWizard() {
  showCreateWizard.value = false
}

async function submitOffer() {
  isLoading.value = true
  try {
    await api('/offers/', {
      method: 'POST',
      body: JSON.stringify(newOffer.value)
    })
    successMessage.value = 'لفظ ثبت شد'
    setTimeout(() => successMessage.value = '', 3000)
    closeWizard()
    await loadOffers()
  } catch (e: any) {
    error.value = e.message
  } finally {
    isLoading.value = false
  }
}

async function parseAndSubmitTextOffer() {
  if (!offerText.value.trim()) return
  isLoading.value = true
  parseError.value = ''
  try {
    const res = await api('/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
    })
    if (res.success && res.data) {
       // Submit the parsed offer directly
       await api('/offers/', {
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
       successMessage.value = 'لفظ متنی ثبت شد'
       offerText.value = ''
       await loadOffers()
    } else {
      parseError.value = res.error || 'خطا در پردازش متن'
    }
  } catch (e: any) {
    parseError.value = e.message
  } finally {
    isLoading.value = false
  }
}

// Trade Logic
function openTradeModal(offer: Offer, qty?: number) {
  if (offer.user_id === props.user?.id) return
  selectedOffer.value = offer
  tradeQuantity.value = qty || offer.remaining_quantity
  showTradeModal.value = true
}

async function executeTrade() {
  if (!selectedOffer.value) return
  isTrading.value = true
  try {
    await api('/trades/', {
      method: 'POST',
      body: JSON.stringify({
        offer_id: selectedOffer.value.id,
        quantity: tradeQuantity.value
      })
    })
    successMessage.value = 'معامله انجام شد'
    showTradeModal.value = false
    await loadOffers()
  } catch (e: any) {
    error.value = e.message
  } finally {
    isTrading.value = false
  }
}

async function expireOffer(id: number) {
  if (!confirm('آیا مطمئن هستید؟')) return
  try {
    await api(`/offers/${id}`, { method: 'DELETE' })
    await loadMyOffers()
    if (activeTab.value === 'offers') await loadOffers()
  } catch (e: any) { error.value = e.message }
}

function repeatOffer(offer: Offer) {
  newOffer.value = { ...offer, republished_from_id: offer.id } as any
  createStep.value = 'preview'
  showCreateWizard.value = true
}

function getStatusLabel(s: string) {
  const map: Record<string, string> = { active: 'فعال', completed: 'تکمیل', expired: 'منقضی', cancelled: 'لغو' }
  return map[s] || s
}

function getUserTradeType(t: Trade) {
  if (t.responder_user_id === props.user?.id) return t.trade_type
  return t.trade_type === 'active' ? 'active' : (t.trade_type === 'buy' ? 'sell' : 'buy')
}

onMounted(() => {
  // Parallelize loading: Fire non-criticals in background
  loadCommodities()
  loadTradingSettings()
  
  // Load initial data based on active tab immediately
  // We do NOT await here to let Vue mount the component and show the skeleton immediately
  if (activeTab.value === 'offers') loadOffers()
  else if (activeTab.value === 'my_offers') loadMyOffers()
  else if (activeTab.value === 'my_trades') loadMyTrades()
  
  startPolling()
  setupWebSocket()

  // Start global timer tick for all offer cards
  timerTick = setInterval(() => {
    now.value = Math.floor(Date.now() / 1000)
  }, 1000) as any
})

onUnmounted(() => {
  stopPolling()
  cleanupWebSocket()
  if (timerTick) clearInterval(timerTick)
})

function startPolling() {
  if (pollingInterval) return
  pollingInterval = setInterval(() => {
    if (activeTab.value === 'offers') loadOffers(true)
    else if (activeTab.value === 'my_offers') loadMyOffers(true)
    else if (activeTab.value === 'my_trades') loadMyTrades(true)
  }, 3000) as any
}

function stopPolling() {
  if (pollingInterval) clearInterval(pollingInterval)
}

// --- WebSocket Realtime Handlers ---
function removeOfferById(offerId: number) {
  offers.value = offers.value.filter(o => o.id !== offerId)
  myOffers.value = myOffers.value.filter(o => o.id !== offerId)
}

function handleOfferExpiredWS(data: any) {
  if (data?.id) {
    removeOfferById(data.id)
  }
}

function handleOfferCreatedWS(_data: any) {
  // Reload offers to get the new one with full data
  if (activeTab.value === 'offers') loadOffers(true)
}

function handleOfferUpdatedWS(_data: any) {
  if (activeTab.value === 'offers') loadOffers(true)
  else if (activeTab.value === 'my_offers') loadMyOffers(true)
}

function handleTradeCreatedWS(_data: any) {
  if (activeTab.value === 'offers') loadOffers(true)
  else if (activeTab.value === 'my_trades') loadMyTrades(true)
}

function setupWebSocket() {
  wsConnect()
  wsOn('offer:expired', handleOfferExpiredWS)
  wsOn('offer:cancelled', handleOfferExpiredWS)
  wsOn('offer:completed', handleOfferExpiredWS)
  wsOn('offer:created', handleOfferCreatedWS)
  wsOn('offer:updated', handleOfferUpdatedWS)
  wsOn('trade:created', handleTradeCreatedWS)
}

function cleanupWebSocket() {
  wsOff('offer:expired', handleOfferExpiredWS)
  wsOff('offer:cancelled', handleOfferExpiredWS)
  wsOff('offer:completed', handleOfferExpiredWS)
  wsOff('offer:created', handleOfferCreatedWS)
  wsOff('offer:updated', handleOfferUpdatedWS)
  wsOff('trade:created', handleTradeCreatedWS)
}

// Watch for client-side timer expiration (offers whose timer hit 0)
watch(now, () => {
  const expired = offers.value.filter(o => o.expires_at_ts && o.expires_at_ts <= now.value)
  for (const o of expired) {
    removeOfferById(o.id)
  }
})

watch(activeTab, (val) => {
   if (val === 'offers') loadOffers()
   else if (val === 'my_offers') loadMyOffers()
   else if (val === 'my_trades') loadMyTrades()
})
</script>

<template>
  <div class="trading-view">
    <!-- Success/Error Messages -->
    <div v-if="successMessage" class="message success">{{ successMessage }}</div>
    <div v-if="error" class="message error">{{ error }}</div>
    
    <!-- Filter Bar at Top -->
    <div class="filter-sort-row">
      <div class="filter-bar">
        <button :class="{ active: filterType === 'all' }" @click="filterType = 'all'">همه</button>
        <button :class="{ active: filterType === 'buy' }" @click="filterType = 'buy'">🟢 خرید</button>
        <button :class="{ active: filterType === 'sell' }" @click="filterType = 'sell'">🔴 فروش</button>
      </div>
      <button class="sort-toggle-btn" :class="{ active: showSortPanel || sortDirection !== 'none' }" @click="showSortPanel = !showSortPanel">
        <span v-if="sortDirection === 'asc'">↑</span>
        <span v-else-if="sortDirection === 'desc'">↓</span>
        <span v-else>⇅</span>
        مرتب‌سازی
      </button>
    </div>

    <!-- Sort Panel -->
    <div v-if="showSortPanel" class="sort-panel">
      <div class="sort-panel-header">
        <span class="sort-panel-title">مرتب‌سازی بر اساس قیمت:</span>
        <button v-if="sortDirection !== 'none'" class="sort-clear-btn" @click="clearSort">✕ حذف</button>
      </div>
      <div class="sort-chips">
        <button
          v-for="c in commodities"
          :key="c.id"
          class="sort-chip"
          :class="{ active: sortCommodity === c.name }"
          @click="toggleSort(c.name)"
        >
          {{ c.name }}
          <span v-if="sortCommodity === c.name && sortDirection === 'asc'" class="sort-arrow">↑</span>
          <span v-if="sortCommodity === c.name && sortDirection === 'desc'" class="sort-arrow">↓</span>
        </button>
      </div>
      <div v-if="sortCommodity && sortDirection !== 'none'" class="sort-hint">
        {{ sortCommodity }} — {{ sortDirection === 'asc' ? 'ارزان‌ترین اول' : 'گران‌ترین اول' }}
        <span class="sort-hint-tip">(دوباره بزنید برای تغییر جهت)</span>
      </div>
    </div>
    
    <!-- Tabs -->
    <div class="tabs">
      <button 
        :class="{ active: activeTab === 'offers' }"
        @click="activeTab = 'offers'"
      >📊 لفظ‌ها</button>
      <button 
        :class="{ active: activeTab === 'my_offers' }"
        @click="activeTab = 'my_offers'"
      >📝 لفظ‌های من</button>
      <button 
        :class="{ active: activeTab === 'my_trades' }"
        @click="activeTab = 'my_trades'"
      >📜 معاملات</button>
    </div>
    
    <!-- Tab: Active Offers -->
    <div v-if="activeTab === 'offers'" class="tab-content">
      <div v-if="isLoading && offers.length === 0">
         <LoadingSkeleton :count="5" :height="100" />
      </div>
      <div v-else-if="filteredOffers.length === 0" class="empty-state">
        <p>هیچ لفظ فعالی وجود ندارد.</p>
      </div>
      
      <div v-else class="offers-list">
        <div 
          v-for="offer in filteredOffers" 
          :key="offer.id" 
          class="offer-card"
          :class="[offer.offer_type, { 'timer-critical': offer.expires_at_ts && getTimerPercent(offer) < 15, 'has-timer': !!offer.expires_at_ts }]"
          :style="getCardTimerStyle(offer)"
        >
          <!-- Timer Glow Bar -->
          <div class="timer-bar-track" v-if="offer.expires_at_ts">
            <div class="timer-bar-fill"></div>
          </div>

          <div class="offer-header">
            <div class="offer-role">
              <span 
                class="role-badge" 
                :class="offer.offer_type === 'buy' ? 'buy' : 'sell'"
              >
                {{ offer.offer_type === 'buy' ? 'خرید' : 'فروش' }}
              </span>
            </div>
            <div class="offer-time">{{ offer.created_at }}</div>
          </div>
          
          <div class="offer-body">
            <div class="offer-main">
              <span class="commodity">{{ offer.commodity_name }}</span>
              <span class="quantity">{{ offer.remaining_quantity }} عدد</span>
              <span class="price">{{ offer.price.toLocaleString() }}</span>
            </div>
            <div v-if="offer.notes" class="offer-notes">
              توضیحات: {{ offer.notes }}
            </div>
          </div>
          
          <div class="offer-footer">
            <div class="trade-buttons" v-if="offer.user_id !== user?.id">
              <template v-if="offer.is_wholesale || !offer.lot_sizes">
                <button class="trade-btn full-width" @click="openTradeModal(offer)">
                  {{ offer.remaining_quantity }} عدد
                </button>
              </template>
              <template v-else>
                <button 
                  v-for="amount in [...new Set([offer.remaining_quantity, ...(offer.lot_sizes || [])])]
                    .filter(a => a > 0 && a <= offer.remaining_quantity)
                    .sort((a, b) => a - b)"
                  :key="offer.id + '-' + amount"
                  class="trade-btn"
                  @click="openTradeModal(offer, amount)"
                >
                  {{ amount }}
                </button>
              </template>
            </div>
            <div v-else class="owner-actions">
              <span class="own-offer-badge">لفظ شما</span>
              <button class="expire-btn-small" @click="expireOffer(offer.id)">❌ منقضی</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Tab: My Offers -->
    <div v-if="activeTab === 'my_offers'" class="tab-content">
      <div v-if="isLoading && myOffers.length === 0">
         <LoadingSkeleton :count="3" :height="100" />
      </div>
      <div v-else-if="myOffers.length === 0" class="empty-state">
        <p>شما هیچ لفظی در ۲ ساعت اخیر نداشته‌اید.</p>
      </div>
      
      <div v-else class="offers-list">
        <div 
          v-for="offer in myOffers" 
          :key="offer.id" 
          class="offer-card my-offer"
          :class="[offer.offer_type, { 'expired-offer': offer.status !== 'active' }]"
        >
          <div class="offer-header">
            <span class="offer-type">
              {{ offer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}
              <span v-if="offer.status !== 'active'" class="status-badge">{{ getStatusLabel(offer.status) }}</span>
            </span>
            <span class="remaining" v-if="offer.status === 'active'">{{ offer.remaining_quantity }}/{{ offer.quantity }}</span>
            <span class="remaining" v-else>{{ offer.quantity }} عدد</span>
          </div>
          
          <div class="offer-body">
            <div class="offer-main">
              <span class="commodity">{{ offer.commodity_name }}</span>
              <span class="quantity">{{ offer.remaining_quantity }} عدد</span>
              <span class="price">{{ offer.price.toLocaleString() }}</span>
            </div>
            <div v-if="offer.notes" class="offer-notes">
              توضیحات: {{ offer.notes }}
            </div>
          </div>
          
          <div class="offer-footer">
            <span class="offer-time">{{ offer.created_at }}</span>
            <div class="owner-actions">
              <button 
                v-if="offer.status === 'active'" 
                class="expire-btn" 
                @click="expireOffer(offer.id)"
              >❌ منقضی کردن</button>
              
              <button 
                v-else 
                class="repeat-btn" 
                @click="repeatOffer(offer)"
              >🔄 تکرار</button>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Tab: My Trades -->
    <div v-if="activeTab === 'my_trades'" class="tab-content">
      <div v-if="isLoading && myTrades.length === 0">
         <LoadingSkeleton :count="4" :height="80" />
      </div>
      <div v-else-if="myTrades.length === 0" class="empty-state">
        <p>هنوز هیچ معامله‌ای انجام نداده‌اید.</p>
      </div>
      
      <div v-else class="trades-list">
        <div 
          v-for="trade in myTrades" 
          :key="trade.id" 
          class="trade-card"
          :class="getUserTradeType(trade)"
        >
          <div class="trade-header">
            <span class="trade-type">
              {{ getUserTradeType(trade) === 'buy' ? '🟢 خرید' : '🔴 فروش' }}
            </span>
            <!-- شماره معامله به بدنه منتقل شد -->
          </div>
          
          <div class="trade-body">
            <div class="trade-info-row">
              <span class="info-label">💰 فی:</span>
              <span class="info-value price">{{ trade.price.toLocaleString() }}</span>
            </div>
            <div class="trade-info-row">
              <span class="info-label">📦 تعداد:</span>
              <span class="info-value">{{ trade.quantity }}</span>
            </div>
            <div class="trade-info-row">
              <span class="info-label">🏷️ کالا:</span>
              <span class="info-value">{{ trade.commodity_name }}</span>
            </div>
            <div class="trade-info-row">
              <span class="info-label">👤 طرف معامله:</span>
              <span 
                class="info-value profile-link" 
                @click.stop="$emit('navigate', 'public_profile', { id: trade.responder_user_id === user?.id ? trade.offer_user_id : trade.responder_user_id, account_name: trade.responder_user_id === user?.id ? trade.offer_user_name : trade.responder_user_name })"
              >
                {{ trade.responder_user_id === user?.id ? trade.offer_user_name : trade.responder_user_name }}
              </span>
            </div>
            <div class="trade-info-row">
              <span class="info-label">🔢 شماره معامله:</span>
              <span class="info-value">{{ trade.trade_number }}</span>
            </div>
            <div class="trade-info-row">
               <span class="info-label">🕐 زمان معامله:</span>
               <span class="info-value dir-ltr">{{ trade.created_at }}</span>
            </div>
          </div>
          
          <div class="trade-footer">
             <!-- زمان به بدنه منتقل شد -->
          </div>
        </div>
      </div>
    </div>
    
    <!-- Bottom Fixed Section (Text Input + Buy/Sell Buttons) -->
    <div class="bottom-fixed" v-if="!showCreateWizard && !showTradeModal">
      <!-- Text Input for Offer with Send Button -->
      <div class="text-offer-section">
        <div class="text-offer-container">
          <button 
            class="send-btn"
            @click="parseAndSubmitTextOffer"
            :disabled="isLoading || !offerText.trim()"
            :class="{ 'active': offerText.trim() }"
          >
            <svg viewBox="0 0 24 24" class="send-icon">
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
            </svg>
          </button>
          <textarea 
            v-model="offerText"
            class="text-offer-input"
            :placeholder="randomPlaceholder"
            rows="1"
            @keydown.enter.prevent="parseAndSubmitTextOffer"
          ></textarea>
        </div>
      </div>
      <div v-if="parseError" class="parse-error">{{ parseError }}</div>
      
      <!-- Buy/Sell Buttons -->
      <div class="bottom-actions">
        <button class="action-btn buy" @click="startCreateOffer('buy')">
          🟢 خرید
        </button>
        <button class="action-btn sell" @click="startCreateOffer('sell')">
          🔴 فروش
        </button>
      </div>
    </div>
    <!-- Create Offer Wizard Modal -->
    <div v-if="showCreateWizard" class="wizard-overlay" @click.self="closeWizard">
      <div class="wizard-modal">
        
        <div v-if="error" class="wizard-error">{{ error }}</div>
        
        <!-- Step: Commodity -->
        <div v-if="createStep === 'commodity'" class="wizard-step">
          <h3>کالا را انتخاب کنید:</h3>
          <div class="commodity-grid">
            <button 
              v-for="commodity in commodities" 
              :key="commodity.id"
              class="commodity-btn"
              @click="selectCommodity(commodity)"
            >
              {{ commodity.name }}
            </button>
          </div>
        </div>
        
        <!-- Step: Quantity -->
        <div v-if="createStep === 'quantity'" class="wizard-step">
          <h3>تعداد را انتخاب کنید:</h3>
          <div class="quantity-grid">
            <button 
              v-for="qty in quickQuantities.filter(q => q >= tradingSettings.offer_min_quantity && q <= tradingSettings.offer_max_quantity)" 
              :key="qty"
              class="qty-btn"
              @click="selectQuantity(qty)"
            >
              {{ qty }}
            </button>
          </div>
          <div class="custom-qty">
            <input 
              type="number" 
              v-model.number="newOffer.quantity"
              :min="tradingSettings.offer_min_quantity"
              :max="tradingSettings.offer_max_quantity"
              placeholder="تعداد دلخواه"
              class="qty-input"
            >
            <button class="confirm-btn" @click="confirmQuantity" :disabled="!newOffer.quantity">
              تأیید
            </button>
          </div>
        </div>
        
        <!-- Step: Lot Type -->
        <div v-if="createStep === 'lot'" class="wizard-step">
          <h3>نوع فروش:</h3>
          <div class="lot-type-buttons">
            <button class="lot-btn wholesale" @click="selectLotType(true)">
              📦 یکجا
            </button>
            <button class="lot-btn retail" @click="selectLotType(false)">
              🔢 خُرد
            </button>
          </div>
        </div>
        
        <!-- Step: Lot Sizes Input -->
        <div v-if="createStep === 'lotInput'" class="wizard-step">
          <h3>ترکیب را وارد کنید:</h3>
          <p class="hint">مجموع باید {{ newOffer.quantity }} باشد (با فاصله جدا کنید)</p>
          <input 
            type="text"
            v-model="lotSizesText"
            :placeholder="suggestedLotText || 'مثال: 10 15 25'"
            class="lot-input"
          >
          <button class="confirm-btn" @click="confirmLotSizes">
            تأیید ترکیب
          </button>
        </div>
        
        <!-- Step: Price -->
        <div v-if="createStep === 'price'" class="wizard-step">
          <h3>قیمت را وارد کنید:</h3>
          <input 
            type="number"
            v-model.number="newOffer.price"
            placeholder="قیمت (تومان)"
            class="price-input"
          >
          <button class="confirm-btn" @click="confirmPrice" :disabled="!newOffer.price">
            تأیید
          </button>
        </div>
        
        <!-- Step: Notes -->
        <div v-if="createStep === 'notes'" class="wizard-step">
          <h3>توضیحات (اختیاری):</h3>
          <textarea 
            v-model="newOffer.notes"
            placeholder="توضیحات اضافی..."
            class="notes-input"
            rows="3"
          ></textarea>
          <button class="confirm-btn" @click="confirmNotes">
            بعدی
          </button>
        </div>
        
        <!-- Step: Preview -->
        <div v-if="createStep === 'preview'" class="wizard-step">
          <h3>پیش‌نمایش لفظ:</h3>
          <div class="preview-card">
            <p><strong>نوع:</strong> {{ newOffer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}</p>
            <p><strong>کالا:</strong> {{ newOffer.commodity_name }}</p>
            <p><strong>تعداد:</strong> {{ newOffer.quantity }}</p>
            <p><strong>قیمت:</strong> {{ (newOffer.price || 0).toLocaleString() }} تومان</p>
            <p><strong>نوع فروش:</strong> {{ newOffer.is_wholesale ? 'یکجا' : 'خُرد' }}</p>
            <p v-if="!newOffer.is_wholesale && newOffer.lot_sizes">
              <strong>ترکیب:</strong> {{ newOffer.lot_sizes.join(' + ') }}
            </p>
            <p v-if="newOffer.notes"><strong>توضیحات:</strong> {{ newOffer.notes }}</p>
          </div>
          <button class="submit-btn" @click="submitOffer" :disabled="isLoading">
            {{ isLoading ? 'در حال ارسال...' : '✅ تأیید و ارسال' }}
          </button>
        </div>
      </div>
    </div>
    
    <!-- Trade Modal -->
    <div v-if="showTradeModal && selectedOffer" class="modal-overlay" @click.self="showTradeModal = false">
      <div class="modal">
        <div class="modal-header">
          <h2>{{ selectedOffer.offer_type === 'buy' ? '🔴 فروش' : '🟢 خرید' }}</h2>
        </div>
        
        <div class="modal-body">
          <p><strong>کالا:</strong> {{ selectedOffer.commodity_name }}</p>
          <p><strong>قیمت:</strong> {{ selectedOffer.price.toLocaleString() }}</p>
          <p><strong>تعداد:</strong> {{ tradeQuantity }}</p>
          <p><strong>مجموع:</strong> {{ (selectedOffer.price * tradeQuantity).toLocaleString() }} تومان</p>
        </div>
        
        <div class="modal-footer">
          <button class="cancel-btn" @click="showTradeModal = false">انصراف</button>
          <button 
            class="confirm-trade-btn"
            @click="executeTrade"
            :disabled="isTrading"
          >
            {{ isTrading ? 'در حال پردازش...' : '✅ تأیید معامله' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trading-view {
  padding: 12px;
  padding-bottom: 100px;
  direction: rtl;
  font-family: 'Vazirmatn', sans-serif;
  min-height: 100vh;
  background: var(--bg-color);
}

/* Header */
.trade-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  padding: 8px 0;
}

.back-btn {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 8px 14px;
  font-size: 18px;
  cursor: pointer;
}

.trade-header h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}

.header-spacer {
  width: 44px;
}

/* Messages */
.message {
  padding: 14px;
  border-radius: 10px;
  margin-bottom: 16px;
  text-align: center;
  font-weight: 500;
}

.message.success {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
}

.message.error {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  color: white;
}

/* Text Offer Section */
.text-offer-section {
  background: var(--card-bg);
  border-radius: 12px;
  padding: 8px;
  margin-bottom: 12px;
  border: 1px solid var(--border-color);
}

.text-offer-container {
  display: flex;
  align-items: center;
  gap: 8px;
  direction: rtl;
  width: 100%;
}

.text-offer-input {
  flex: 1;
  border: 1px solid var(--border-color);
  border-radius: 20px;
  padding: 10px 16px;
  font-size: 14px;
  resize: none;
  font-family: inherit;
  min-height: 40px;
  max-height: 80px;
  line-height: 1.4;
  box-sizing: border-box;
}

.text-offer-input:focus {
  outline: none;
  border-color: #007AFF;
}

.send-btn {
  width: 40px;
  height: 40px;
  min-width: 40px;
  border-radius: 50%;
  border: none;
  background: #e5e5e5;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s ease;
}

.send-btn.active {
  background: linear-gradient(135deg, #007AFF, #0056b3);
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.send-icon {
  width: 20px;
  height: 20px;
  fill: #999;
  transform: rotate(180deg); /* For RTL direction */
}

.send-btn.active .send-icon {
  fill: white;
}

.parse-error {
  color: #ef4444;
  font-size: 12px;
  margin-top: 8px;
  padding: 0 8px;
}

.text-submit-btn {
  margin-top: 8px;
  width: 100%;
  padding: 10px;
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
  border: none;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
}

/* Tabs */
.tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 16px;
  overflow-x: auto;
}

.tabs button {
  flex: 1;
  padding: 10px 8px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 10px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
}

.tabs button.active {
  background: linear-gradient(135deg, #007AFF, #0056b3);
  color: white;
  border-color: #007AFF;
}

/* Filter & Sort Row */
.filter-sort-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: -25px;
  margin-bottom: 8px;
}

.filter-bar {
  display: flex;
  gap: 2px;
}

.filter-bar button {
  padding: 8px 16px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 20px;
  font-size: 12px;
  cursor: pointer;
}

.filter-bar button.active {
  background: #007AFF;
  color: white;
  border-color: #007AFF;
}

.sort-toggle-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 7px 12px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-secondary);
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}
.sort-toggle-btn.active {
  background: #f59e0b;
  color: white;
  border-color: #f59e0b;
}

/* Sort Panel */
.sort-panel {
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 10px 12px;
  margin-bottom: 8px;
  animation: slideDown 0.2s ease-out;
}
@keyframes slideDown {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}

.sort-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.sort-panel-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-color);
}
.sort-clear-btn {
  font-size: 11px;
  padding: 3px 8px;
  border: none;
  background: #fee2e2;
  color: #dc2626;
  border-radius: 12px;
  cursor: pointer;
  font-weight: 600;
}

.sort-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.sort-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 16px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}
.sort-chip:active {
  transform: scale(0.95);
}
.sort-chip.active {
  background: #fffbeb;
  border-color: #f59e0b;
  color: #b45309;
  font-weight: 700;
}
.sort-arrow {
  font-weight: 800;
  font-size: 13px;
}

.sort-hint {
  margin-top: 8px;
  font-size: 11px;
  color: #d97706;
  font-weight: 600;
}
.sort-hint-tip {
  color: var(--text-secondary);
  font-weight: 400;
}

/* Offers List */
.offers-list, .trades-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.offer-card, .trade-card {
  background: var(--card-bg);
  border-radius: 12px;
  padding: 14px;
  border: 1px solid var(--border-color);
  position: relative;
  overflow: hidden;
  transition: box-shadow 1s linear, border-color 1s linear;
}

/* Animated glowing border for offers with timer */
.offer-card.has-timer {
  border-color: var(--timer-color, var(--border-color));
  box-shadow:
    0 0 var(--timer-glow-spread, 0px) var(--timer-color-glow, transparent),
    inset 0 0 calc(var(--timer-glow-spread, 0px) * 0.5) var(--timer-color-glow-inner, transparent);
}

/* --- Timer Glow Bar (top of card) --- */
.timer-bar-track {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3.5px;
  background: rgba(128, 128, 128, 0.1);
  border-radius: 12px 12px 0 0;
  overflow: hidden;
  z-index: 2;
}

.timer-bar-fill {
  height: 100%;
  width: var(--timer-pct, 100%);
  background: linear-gradient(90deg, var(--timer-color, #10b981), var(--timer-color-light, #10b981));
  box-shadow: 0 0 8px var(--timer-color, #10b981), 0 0 3px var(--timer-color, #10b981);
  border-radius: 0 3px 3px 0;
  transition: width 1s linear, background 1.5s ease, box-shadow 1.5s ease;
  transform-origin: right;
}

/* Critical state — pulsing glow */
.offer-card.timer-critical .timer-bar-fill {
  animation: timer-pulse 0.8s ease-in-out infinite;
}

.offer-card.timer-critical {
  animation: card-pulse 1.2s ease-in-out infinite;
}

@keyframes timer-pulse {
  0%, 100% {
    opacity: 0.6;
    box-shadow: 0 0 6px var(--timer-color);
  }
  50% {
    opacity: 1;
    box-shadow: 0 0 18px var(--timer-color), 0 0 6px var(--timer-color);
  }
}

@keyframes card-pulse {
  0%, 100% {
    box-shadow: 0 0 var(--timer-glow-spread, 4px) var(--timer-color-glow, rgba(239,68,68,0.3));
  }
  50% {
    box-shadow:
      0 0 calc(var(--timer-glow-spread, 4px) * 2.5) var(--timer-glow-strong, rgba(239,68,68,0.5)),
      inset 0 0 8px var(--timer-glow-subtle, rgba(239,68,68,0.15));
  }
}

.offer-card.buy {
  border-right: 4px solid #10b981;
}

.offer-card.sell {
  border-right: 4px solid #ef4444;
}

.offer-card.buy.has-timer {
  border-right-color: var(--timer-color, #10b981);
}

.offer-card.sell.has-timer {
  border-right-color: var(--timer-color, #ef4444);
}

.offer-header, .trade-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
}

.offer-type, .trade-type {
  font-weight: 600;
}

.offer-time, .trade-time {
  color: var(--text-secondary);
  font-size: 11px;
}

.offer-body, .trade-body {
  margin-bottom: 10px;
}

.offer-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.commodity {
  font-weight: 600;
}

.quantity {
  background: #f0f0f0;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 13px;
}

.price {
  font-weight: 700;
  color: var(--primary-color);
}

.offer-notes {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.offer-footer, .trade-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.trade-buttons {
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: nowrap !important; /* Force single row */
  overflow-x: auto; /* Allow scrolling if too many buttons */
  scrollbar-width: none; /* Hide scrollbar for cleaner look */
  gap: 6px;
  width: 100%;
}
.trade-buttons::-webkit-scrollbar {
  display: none;
}

.trade-btn {
  padding: 8px 12px;
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  flex: 1 1 auto; /* Grow and shrink */
  min-width: 45px; /* Smaller min-width to fit more */
  max-width: 100px; /* Prevent them from becoming too wide individually unless full-width */
  text-align: center;
}

.trade-btn.full-width {
  width: 100%;
  max-width: none;
}

/* Force Cancel Button Style with High Specificity */
.modal-footer .cancel-btn {
  background: #dc2626 !important;
  color: white !important;
  border: none !important;
  padding: 12px 24px !important;
  border-radius: 12px !important;
  font-size: 15px !important;
  font-weight: 700 !important;
  cursor: pointer !important;
  transition: all 0.2s ease !important;
  box-shadow: 0 4px 12px rgba(220, 38, 38, 0.3) !important;
}

.modal-footer .cancel-btn:hover {
  background: #b91c1c !important;
  transform: translateY(-2px) !important;
  box-shadow: 0 6px 15px rgba(220, 38, 38, 0.4) !important;
}

.own-offer-badge {
  background: #f0f0f0;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  color: var(--text-secondary);
}

.expire-btn {
  background: #fee2e2;
  color: #dc2626;
  border: none;
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}

.owner-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.expire-btn-small {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  color: white;
  border: none;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.expire-btn-small:hover {
  background: linear-gradient(135deg, #dc2626, #b91c1c);
  transform: scale(1.05);
}

/* Empty State */
.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: var(--text-secondary);
}

/* Bottom Fixed Section */
.bottom-fixed {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--bg-color);
  border-top: 1px solid var(--border-color);
  padding: 12px 16px;
  z-index: 100;
}

.bottom-fixed .text-offer-section {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  padding: 0;
  background: transparent;
  border: none;
}

.bottom-fixed .text-offer-input {
  flex: 1;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  font-size: 13px;
  resize: none;
  min-height: 40px;
}

.bottom-fixed .text-submit-btn {
  padding: 10px 16px;
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
  border: none;
  border-radius: 10px;
  font-size: 16px;
  cursor: pointer;
}

.bottom-fixed .parse-error {
  margin-bottom: 8px;
}

.bottom-actions {
  display: flex;
  gap: 12px;
}

.action-btn {
  flex: 1;
  padding: 10px;
  border: none;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.action-btn.buy {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
}

.action-btn.sell {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  color: white;
}

/* Wizard Modal */
.wizard-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: flex-end;
  z-index: 200;
}

.wizard-modal {
  background: var(--card-bg);
  width: 100%;
  max-height: 85vh;
  border-radius: 20px 20px 0 0;
  padding: 20px;
  overflow-y: auto;
}

.wizard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.wizard-back, .wizard-close {
  background: linear-gradient(135deg, #4b5563, #374151);
  border: none;
  padding: 10px 16px;
  border-radius: 10px;
  font-size: 18px;
  color: white;
  cursor: pointer;
  transition: all 0.2s ease;
}

.wizard-back:hover, .wizard-close:hover {
  background: linear-gradient(135deg, #374151, #1f2937);
  transform: scale(1.05);
}

.wizard-header h2 {
  margin: 0;
  font-size: 18px;
}

.wizard-error {
  background: #fee2e2;
  color: #dc2626;
  padding: 10px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 13px;
}

.wizard-step h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
}

.commodity-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.commodity-btn {
  padding: 14px 8px;
  background: linear-gradient(135deg, #667eea, #764ba2);
  border: none;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 600;
  color: white;
  cursor: pointer;
  box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
  transition: all 0.2s ease;
}

.commodity-btn:hover {
  background: linear-gradient(135deg, #764ba2, #667eea);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
}

.quantity-grid {
  display: flex;
  flex-wrap: nowrap;
  gap: 8px;
  margin-bottom: 20px;
  justify-content: center;
}

.qty-btn {
  flex: 1;
  min-width: 55px;
  padding: 14px 8px;
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
  border: none;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
}

.qty-btn:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.4);
}

.custom-qty {
  display: flex;
  gap: 12px;
  margin-top: 16px;
  align-items: stretch;
}

.custom-qty .qty-input {
  flex: 3;
  min-width: 150px;
}

.custom-qty .confirm-btn {
  flex: 1;
  min-width: 80px;
}

.qty-input, .price-input, .lot-input {
  flex: 1;
  width: 100%;
  padding: 20px 24px;
  border: 2px solid #d1d5db;
  border-radius: 14px;
  font-size: 20px;
  font-weight: 600;
  text-align: center;
  min-height: 65px;
  background: white;
  transition: all 0.2s ease;
  box-sizing: border-box;
}

.qty-input:focus, .price-input:focus, .lot-input:focus {
  border-color: #667eea;
  background: white;
  outline: none;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.15);
}

.qty-input::placeholder, .price-input::placeholder, .lot-input::placeholder {
  color: #9ca3af;
  font-weight: 400;
}

.confirm-btn {
  padding: 14px 24px;
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  border: none;
  border-radius: 12px;
  font-weight: 600;
  font-size: 15px;
  cursor: pointer;
  box-shadow: 0 4px 15px rgba(16, 185, 129, 0.3);
  transition: all 0.2s ease;
}

.confirm-btn:hover {
  background: linear-gradient(135deg, #059669, #047857);
  transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(16, 185, 129, 0.4);
}

.confirm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
  box-shadow: none;
}

.lot-type-buttons {
  display: flex;
  gap: 12px;
}

.lot-btn {
  flex: 1;
  padding: 24px;
  border: none;
  border-radius: 14px;
  font-size: 18px;
  font-weight: 600;
  cursor: pointer;
  color: white;
}

.lot-btn.wholesale {
  background: linear-gradient(135deg, #6366f1, #4f46e5);
}

.lot-btn.retail {
  background: linear-gradient(135deg, #f59e0b, #d97706);
}

.hint {
  color: var(--text-secondary);
  font-size: 12px;
  margin-bottom: 12px;
}

.notes-input {
  width: 100%;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  font-size: 14px;
  resize: none;
  font-family: inherit;
  margin-bottom: 12px;
}

.preview-card {
  background: #f9fafb;
  padding: 16px;
  border-radius: 12px;
  margin-bottom: 16px;
}

.preview-card p {
  margin: 6px 0;
}

.submit-btn {
  width: 100%;
  padding: 16px;
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  border: none;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 700;
  cursor: pointer;
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* Trade Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0,0,0,0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.modal {
  background: var(--card-bg);
  border-radius: 16px;
  width: 90%;
  max-width: 400px;
  padding: 20px;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.modal-header h2 {
  margin: 0;
}

.close-btn {
  background: #f0f0f0;
  border: none;
  padding: 8px 12px;
  border-radius: 8px;
  cursor: pointer;
}

.modal-body {
  margin-bottom: 20px;
}

.modal-body p {
  margin: 8px 0;
}

.modal-footer {
  display: flex;
  gap: 12px;
}

.cancel-btn {
  flex: 1;
  padding: 14px;
  background: #f0f0f0;
  border: none;
  border-radius: 10px;
  font-weight: 500;
  cursor: pointer;
}

.confirm-trade-btn {
  flex: 1;
  padding: 14px;
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  border: none;
  border-radius: 10px;
  font-weight: 600;
  cursor: pointer;
}

.confirm-trade-btn:disabled {
  opacity: 0.6;
}

/* Trade card styles */
.trade-card.buy {
  border-right: 4px solid #10b981;
}

.trade-card.sell {
  border-right: 4px solid #ef4444;
}

.trade-number {
  color: var(--text-secondary);
  font-size: 12px;
}

/* Expired Offer Styles */
.expired-offer {
  opacity: 0.8;
  background: #f5f5f5; /* Light gray for expired */
  border-color: #ddd;
}

[data-theme='dark'] .expired-offer {
  background: #2a2a2a;
  border-color: #444;
}

.status-badge {
  font-size: 12px;
  background: #eee;
  padding: 2px 6px;
  border-radius: 4px;
  margin-right: 8px;
  color: #666;
}

[data-theme='dark'] .status-badge {
  background: #444;
  color: #aaa;
}

/* Repeat Button */
.repeat-btn {
  background: var(--primary-color);
  color: white;
  border: none;
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
}

.repeat-btn-small {
  background: var(--primary-color);
  color: white;
  border: none;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}
.trade-info-row { 
  display: flex; 
  justify-content: space-between; 
  margin-bottom: 6px; 
  font-size: 13px; 
} 

.info-label { 
  color: var(--text-secondary); 
} 

.info-value.price { 
  color: var(--text-color); 
  font-weight: 700; 
} 

/* Profile Link Style - High Specificity */
.info-value.profile-link { 
  color: #3b82f6 !important; /* Blue-500 */
  cursor: pointer; 
  text-decoration: underline; 
  font-weight: 600; 
} 

[data-theme='dark'] .info-value.profile-link {
  color: #60a5fa !important; /* Blue-400 for dark mode */
}

.dir-ltr { 
  direction: ltr; 
  display: inline-block; 
}

</style>

