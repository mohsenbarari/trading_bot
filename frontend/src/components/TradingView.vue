<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted } from 'vue'
import CircleTimer from './CircleTimer.vue'

// Props
const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  user: any
}>()

// Emits
const emit = defineEmits<{
  (e: 'navigate', view: string, payload?: any): void
}>()

// Types
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
const activeTab = ref<'offers' | 'my_offers' | 'my_trades'>('offers')
const isLoading = ref(false)
const error = ref('')
const successMessage = ref('')

// Offers list
const offers = ref<Offer[]>([])
const myOffers = ref<Offer[]>([])
const myTrades = ref<Trade[]>([])
const commodities = ref<Commodity[]>([])
const tradingSettings = ref<TradingSettings>({
  offer_min_quantity: 1,
  offer_max_quantity: 50,
  lot_min_size: 5,
  lot_max_count: 5,
  offer_expiry_minutes: 10
})

// Filter
const filterType = ref<'all' | 'buy' | 'sell'>('all')

// Create offer wizard
const showCreateWizard = ref(false)
const createStep = ref<'commodity' | 'quantity' | 'lot' | 'lotInput' | 'price' | 'notes' | 'preview'>('commodity')

// Offer data
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

// Text mode
const offerText = ref('')
const parseError = ref('')

// Trade modal
const showTradeModal = ref(false)
const selectedOffer = ref<Offer | null>(null)
const tradeQuantity = ref(0)
const isTrading = ref(false)

// Polling
let pollingInterval: number | null = null

// Quick quantity buttons
const quickQuantities = [10, 20, 30, 40, 50]

// Computed
const filteredOffers = computed(() => {
  if (filterType.value === 'all') return offers.value
  return offers.value.filter(o => o.offer_type === filterType.value)
})

// Dynamic placeholder generator based on actual commodities
const randomPlaceholder = computed(() => {
  // Default if no commodities loaded yet
  if (!commodities.value || commodities.value.length === 0) {
    return 'لفظ متنی... مثال: خ سکه 30تا 125000'
  }
  
  // Pick a random commodity (with fallback)
  const commodityIndex = Math.floor(Math.random() * commodities.value.length)
  const commodity = commodities.value[commodityIndex]
  if (!commodity) {
    return 'لفظ متنی... مثال: خ سکه 30تا 125000'
  }
  const commodityName = commodity.name
  
  // Random parameters
  const tradeTypes = ['خ', 'ف', 'خرید', 'فروش']
  const tradeType = tradeTypes[Math.floor(Math.random() * tradeTypes.length)] || 'خ'
  
  const quantities = [20, 25, 30, 35, 40, 45, 50]
  const quantity = quantities[Math.floor(Math.random() * quantities.length)] || 30
  
  const prices = [123000, 124000, 125000, 126000, 127000, 128000]
  const price = prices[Math.floor(Math.random() * prices.length)] || 125000
  
  const quantitySuffix = Math.random() > 0.5 ? 'تا' : ' عدد'
  
  // 50% chance for retail (lot sizes)
  const isRetail = Math.random() > 0.5
  
  if (isRetail && quantity >= 20) {
    // Generate lot sizes that sum to quantity
    const lot1 = Math.floor(quantity / 3)
    const lot2 = Math.floor(quantity / 3)
    const lot3 = quantity - lot1 - lot2
    return `${tradeType} ${commodityName} ${quantity}${quantitySuffix} ${lot1} ${lot2} ${lot3} ${price}`
  } else {
    return `${tradeType} ${commodityName} ${quantity}${quantitySuffix} ${price}`
  }
})

// API Helper
async function apiFetch(endpoint: string, options: RequestInit = {}) {
  // Get the freshest token (localStorage may have been refreshed by parent)
  const token = localStorage.getItem('auth_token') || props.jwtToken;
  
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(token ? { 'Authorization': `Bearer ${token}` } : {})
  }
  
  const response = await fetch(`${props.apiBaseUrl}/api${endpoint}`, {
    ...options,
    headers: { ...headers, ...(options.headers || {}) }
  })
  
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || `خطا: ${response.status}`)
  }
  
  if (response.status === 204) return null
  return response.json()
}

// Load Data
async function loadOffers() {
  try {
    offers.value = await apiFetch('/offers/')
  } catch (e: any) {
    console.error('Error loading offers:', e)
  }
}

async function loadMyOffers() {
  try {
    // دریافت لفظ‌های ۲ ساعت اخیر (همه وضعیت‌ها)
    myOffers.value = await apiFetch('/offers/my?since_hours=2')
  } catch (e: any) {
    console.error('Error loading my offers:', e)
  }
}

async function loadMyTrades() {
  try {
    myTrades.value = await apiFetch('/trades/my')
  } catch (e: any) {
    console.error('Error loading my trades:', e)
  }
}

async function loadCommodities() {
  try {
    const data = await apiFetch('/commodities/')
    commodities.value = data
  } catch (e: any) {
    console.error('Error loading commodities:', e)
  }
}

async function loadTradingSettings() {
  try {
    const data = await apiFetch('/trading-settings/')
    tradingSettings.value = data
  } catch (e: any) {
    console.error('Error loading trading settings:', e)
  }
}

// ===== CREATE OFFER WIZARD =====

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
  error.value = ''
  createStep.value = 'commodity'
  showCreateWizard.value = true
}

function selectCommodity(commodity: Commodity) {
  newOffer.value.commodity_id = commodity.id
  newOffer.value.commodity_name = commodity.name
  createStep.value = 'quantity'
}

function selectQuantity(qty: number) {
  if (qty < tradingSettings.value.offer_min_quantity || qty > tradingSettings.value.offer_max_quantity) {
    error.value = `تعداد باید بین ${tradingSettings.value.offer_min_quantity} تا ${tradingSettings.value.offer_max_quantity} باشد.`
    return
  }
  newOffer.value.quantity = qty
  error.value = ''
  createStep.value = 'lot'
}

function confirmQuantity() {
  const qty = newOffer.value.quantity
  if (!qty || qty < tradingSettings.value.offer_min_quantity || qty > tradingSettings.value.offer_max_quantity) {
    error.value = `تعداد باید بین ${tradingSettings.value.offer_min_quantity} تا ${tradingSettings.value.offer_max_quantity} باشد.`
    return
  }
  error.value = ''
  createStep.value = 'lot'
}

function selectLotType(isWholesale: boolean) {
  newOffer.value.is_wholesale = isWholesale
  if (isWholesale) {
    newOffer.value.lot_sizes = null
    createStep.value = 'price'
  } else {
    // پیشنهاد ترکیب اولیه
    const qty = newOffer.value.quantity || 0
    if (qty >= 30) {
      newOffer.value.lot_sizes = [Math.floor(qty / 3), Math.floor(qty / 3), qty - 2 * Math.floor(qty / 3)]
    } else if (qty >= 10) {
      newOffer.value.lot_sizes = [Math.floor(qty / 2), qty - Math.floor(qty / 2)]
    } else {
      newOffer.value.lot_sizes = [qty]
    }
    createStep.value = 'lotInput'
  }
}

// Lot sizes input (as text like "10 15 25")
const lotSizesText = ref('')
const suggestedLotText = ref('')  // Placeholder for suggested combination

function validateLotSizes(): boolean {
  // If user didn't enter anything, use the suggested combination
  const textToValidate = lotSizesText.value.trim() || suggestedLotText.value.trim()
  const parts = textToValidate.split(/\s+/)
  if (parts.length === 0 || (parts.length === 1 && parts[0] === '')) {
    error.value = 'لطفاً ترکیب را وارد کنید.'
    return false
  }
  
  const lots: number[] = []
  for (const p of parts) {
    const n = parseInt(p)
    if (isNaN(n) || n <= 0) {
      error.value = `"${p}" یک عدد معتبر نیست.`
      return false
    }
    if (n < tradingSettings.value.lot_min_size) {
      error.value = `هر بخش باید حداقل ${tradingSettings.value.lot_min_size} عدد باشد.`
      return false
    }
    lots.push(n)
  }
  
  if (lots.length > tradingSettings.value.lot_max_count) {
    error.value = `حداکثر ${tradingSettings.value.lot_max_count} بخش مجاز است.`
    return false
  }
  
  const sum = lots.reduce((a, b) => a + b, 0)
  if (sum !== newOffer.value.quantity) {
    error.value = `جمع ترکیب (${sum}) با کل (${newOffer.value.quantity}) برابر نیست.`
    return false
  }
  
  newOffer.value.lot_sizes = lots.sort((a, b) => b - a)
  error.value = ''
  return true
}

function confirmLotSizes() {
  if (validateLotSizes()) {
    createStep.value = 'price'
  }
}

function confirmPrice() {
  if (!newOffer.value.price || newOffer.value.price <= 0) {
    error.value = 'قیمت باید بزرگ‌تر از صفر باشد.'
    return
  }
  error.value = ''
  createStep.value = 'notes'
}

function confirmNotes() {
  createStep.value = 'preview'
}

function goBack() {
  switch (createStep.value) {
    case 'commodity': closeWizard(); break
    case 'quantity': createStep.value = 'commodity'; break
    case 'lot': createStep.value = 'quantity'; break
    case 'lotInput': createStep.value = 'lot'; break
    case 'price': 
      createStep.value = newOffer.value.is_wholesale ? 'lot' : 'lotInput'
      break
    case 'notes': createStep.value = 'price'; break
    case 'preview': createStep.value = 'notes'; break
  }
}

function closeWizard() {
  showCreateWizard.value = false
  newOffer.value = {
    offer_type: '',
    commodity_id: 0,
    commodity_name: '',
    quantity: null,
    price: null,
    is_wholesale: true,
    lot_sizes: null,
    notes: '',
    republished_from_id: null
  }
  error.value = ''
}

async function submitOffer() {
  isLoading.value = true
  error.value = ''
  
  try {
    const payload = {
      offer_type: newOffer.value.offer_type,
      commodity_id: newOffer.value.commodity_id,
      quantity: newOffer.value.quantity,
      price: newOffer.value.price,
      is_wholesale: newOffer.value.is_wholesale,
      lot_sizes: newOffer.value.lot_sizes,
      notes: newOffer.value.notes || null,
      republished_from_id: newOffer.value.republished_from_id || null
    }
    
    await apiFetch('/offers/', {
      method: 'POST',
      body: JSON.stringify(payload)
    })
    
    successMessage.value = '✅ لفظ شما با موفقیت در کانال ارسال شد!'
    closeWizard()
    await loadOffers()
    
    setTimeout(() => successMessage.value = '', 3000)
  } catch (e: any) {
    error.value = e.message
  } finally {
    isLoading.value = false
  }
}

// ===== TEXT MODE OFFER =====

async function parseAndSubmitTextOffer() {
  if (!offerText.value.trim()) {
    parseError.value = 'لطفاً متن لفظ را وارد کنید.'
    return
  }
  
  isLoading.value = true
  parseError.value = ''
  
  try {
    // Step 1: Parse the text
    const parseResult = await apiFetch('/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
    })
    
    if (!parseResult.success || !parseResult.data) {
      parseError.value = parseResult.error || 'خطا در پارس متن'
      return
    }
    
    // Step 2: Create the offer using parsed data
    const offerData = parseResult.data
    await apiFetch('/offers/', {
      method: 'POST',
      body: JSON.stringify({
        offer_type: offerData.trade_type,
        commodity_id: offerData.commodity_id,
        quantity: offerData.quantity,
        price: offerData.price,
        is_wholesale: offerData.is_wholesale,
        lot_sizes: offerData.lot_sizes,
        notes: offerData.notes
      })
    })
    
    successMessage.value = '✅ لفظ شما با موفقیت در کانال ارسال شد!'
    offerText.value = ''
    await loadOffers()
    
    setTimeout(() => successMessage.value = '', 3000)
  } catch (e: any) {
    parseError.value = e.message
  } finally {
    isLoading.value = false
  }
}

// ===== TRADE MODAL =====

function openTradeModal(offer: Offer, quantity?: number) {
  if (offer.user_id === props.user?.id) {
    error.value = 'نمی‌توانید روی لفظ خودتان معامله کنید.'
    setTimeout(() => error.value = '', 3000)
    return
  }
  selectedOffer.value = offer
  tradeQuantity.value = quantity ?? offer.remaining_quantity
  showTradeModal.value = true
}

async function executeTrade() {
  if (!selectedOffer.value) return
  
  isTrading.value = true
  error.value = ''
  
  try {
    await apiFetch('/trades/', {
      method: 'POST',
      body: JSON.stringify({
        offer_id: selectedOffer.value.id,
        quantity: tradeQuantity.value
      })
    })
    
    successMessage.value = '✅ معامله با موفقیت انجام شد!'
    showTradeModal.value = false
    selectedOffer.value = null
    
    await new Promise(resolve => setTimeout(resolve, 300))
    await loadOffers()
    setTimeout(() => loadOffers(), 500)
    
    setTimeout(() => successMessage.value = '', 3000)
  } catch (e: any) {
    error.value = e.message
  } finally {
    isTrading.value = false
  }
}

// ===== EXPIRE OFFER =====

async function expireOffer(offerId: number) {
  if (!confirm('آیا از منقضی کردن این لفظ مطمئن هستید؟')) return
  
  try {
    await apiFetch(`/offers/${offerId}`, { method: 'DELETE' })
    successMessage.value = '✅ لفظ منقضی شد.'
    await loadMyOffers()
    await loadOffers()
    setTimeout(() => successMessage.value = '', 3000)
  } catch (e: any) {
    error.value = e.message
  }
}

function repeatOffer(offer: any) {
  // تنظیم مقادیر فرم از روی لفظ قبلی
  newOffer.value = {
    offer_type: offer.offer_type,
    commodity_id: offer.commodity_id,
    commodity_name: offer.commodity_name,
    quantity: offer.quantity,
    price: offer.price,
    is_wholesale: offer.is_wholesale,
    lot_sizes: offer.original_lot_sizes || offer.lot_sizes,
    notes: offer.notes,
    republished_from_id: offer.id
  }
  
  // باز کردن ویزارد
  showCreateWizard.value = true
  createStep.value = 'preview'
}

function getStatusLabel(status: string) {
  switch (status) {
    case 'active': return 'فعال';
    case 'completed': return 'تکمیل شده';
    case 'expired': return 'منقضی شده';
    case 'cancelled': return 'لغو شده';
    default: return status;
  }
}

// ===== POLLING =====

function startPolling() {
  if (pollingInterval) return
  
  pollingInterval = setInterval(async () => {
    if (activeTab.value === 'offers') {
      await loadOffers()
    } else if (activeTab.value === 'my_offers') {
      await loadMyOffers()
    } else if (activeTab.value === 'my_trades') {
      await loadMyTrades()
    }
  }, 1500) as unknown as number
}

function stopPolling() {
  if (pollingInterval) {
    clearInterval(pollingInterval)
    pollingInterval = null
  }
}

// ===== NAVIGATION =====

function goHome() {
  emit('navigate', 'profile')
}

// ===== LIFECYCLE =====

onMounted(async () => {
  await loadCommodities()
  await loadTradingSettings()
  await loadOffers()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})

watch(activeTab, async (tab) => {
  if (tab === 'my_offers') await loadMyOffers()
  if (tab === 'my_trades') await loadMyTrades()
  if (tab === 'offers') await loadOffers()
})

// Set suggested lot sizes as placeholder when entering lotInput step
watch(createStep, (step) => {
  if (step === 'lotInput' && newOffer.value.lot_sizes) {
    suggestedLotText.value = newOffer.value.lot_sizes.join(' ')
    lotSizesText.value = ''  // Keep input empty so placeholder shows
  }
})
</script>

<template>
  <div class="trading-view">
    <!-- Success/Error Messages -->
    <div v-if="successMessage" class="message success">{{ successMessage }}</div>
    <div v-if="error" class="message error">{{ error }}</div>
    
    <!-- Filter Bar at Top -->
    <div class="filter-bar">
      <button :class="{ active: filterType === 'all' }" @click="filterType = 'all'">همه</button>
      <button :class="{ active: filterType === 'buy' }" @click="filterType = 'buy'">🟢 خرید</button>
      <button :class="{ active: filterType === 'sell' }" @click="filterType = 'sell'">🔴 فروش</button>
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
      <div v-if="filteredOffers.length === 0" class="empty-state">
        <p>هیچ لفظ فعالی وجود ندارد.</p>
      </div>
      
      <div v-else class="offers-list">
        <div 
          v-for="offer in filteredOffers" 
          :key="offer.id" 
          class="offer-card"
          :class="offer.offer_type"
        >
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

          <!-- Expiration Timer (Absolute Positioned) -->
          <div class="offer-timer-badge" v-if="offer.expires_at_ts">
            <CircleTimer 
              :expires-at="offer.expires_at_ts"
              :total-duration="tradingSettings.offer_expiry_minutes * 60"
              :size="24"
            />
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
      <div v-if="myOffers.length === 0" class="empty-state">
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
      <div v-if="myTrades.length === 0" class="empty-state">
        <p>هنوز هیچ معامله‌ای انجام نداده‌اید.</p>
      </div>
      
      <div v-else class="trades-list">
        <div 
          v-for="trade in myTrades" 
          :key="trade.id" 
          class="trade-card"
          :class="trade.trade_type"
        >
          <div class="trade-header">
            <span class="trade-type">
              {{ trade.trade_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}
            </span>
            <span class="trade-number">#{{ trade.trade_number }}</span>
          </div>
          
          <div class="trade-body">
            <p><strong>{{ trade.commodity_name }}</strong></p>
            <p>💰 فی: {{ trade.price.toLocaleString() }} | 📦 تعداد: {{ trade.quantity }}</p>
            <p>👤 طرف معامله: {{ trade.responder_user_id === user?.id ? trade.offer_user_name : trade.responder_user_name }}</p>
          </div>
          
          <div class="trade-footer">
            <span class="trade-time">{{ trade.created_at }}</span>
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

/* Filter Bar */
.filter-bar {
  display: flex;
  gap: 2px;
  margin-top: -25px;
  margin-bottom: 8px;
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
  position: relative; /* Context for absolute timer */
}

.offer-timer-badge {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 5;
  background: var(--card-bg);
  border-radius: 50%;
  padding: 1px;
}

.offer-card.buy {
  border-right: 4px solid #10b981;
}

.offer-card.sell {
  border-right: 4px solid #ef4444;
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
  padding-left: 30px; /* Space for absolute timer */
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
</style>
