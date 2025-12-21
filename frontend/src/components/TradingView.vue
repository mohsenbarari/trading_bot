<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted } from 'vue'

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
  lot_max_count: 5
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
  quantity: 0,
  price: 0,
  is_wholesale: true,
  lot_sizes: null as number[] | null,
  notes: ''
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
const quickQuantities = [10, 15, 20, 25, 30, 35, 40, 45, 50]

// Computed
const filteredOffers = computed(() => {
  if (filterType.value === 'all') return offers.value
  return offers.value.filter(o => o.offer_type === filterType.value)
})

// API Helper
async function apiFetch(endpoint: string, options: RequestInit = {}) {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
    ...(props.jwtToken ? { 'Authorization': `Bearer ${props.jwtToken}` } : {})
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
    myOffers.value = await apiFetch('/offers/my?status_filter=active')
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
    quantity: 0,
    price: 0,
    is_wholesale: true,
    lot_sizes: null,
    notes: ''
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
  if (qty < tradingSettings.value.offer_min_quantity || qty > tradingSettings.value.offer_max_quantity) {
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
    const qty = newOffer.value.quantity
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

function validateLotSizes(): boolean {
  const parts = lotSizesText.value.trim().split(/\s+/)
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
  if (newOffer.value.price <= 0) {
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
    quantity: 0,
    price: 0,
    is_wholesale: true,
    lot_sizes: null,
    notes: ''
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
      notes: newOffer.value.notes || null
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
    await apiFetch('/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
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

// Set initial lot sizes text when entering lotInput step
watch(createStep, (step) => {
  if (step === 'lotInput' && newOffer.value.lot_sizes) {
    lotSizesText.value = newOffer.value.lot_sizes.join(' ')
  }
})
</script>

<template>
  <div class="trading-view">
    <!-- Header with back button -->
    <div class="trade-header">
      <button class="back-btn" @click="goHome">
        <span>←</span>
      </button>
      <h1>معاملات</h1>
      <div class="header-spacer"></div>
    </div>
    
    <!-- Success/Error Messages -->
    <div v-if="successMessage" class="message success">{{ successMessage }}</div>
    <div v-if="error" class="message error">{{ error }}</div>
    
    <!-- Text Input for Offer -->
    <div class="text-offer-section">
      <textarea 
        v-model="offerText"
        class="text-offer-input"
        placeholder="لفظ متنی را اینجا بنویسید... مثال: خرید سکه 10 20 15 قیمت 1000000"
        rows="2"
      ></textarea>
      <div v-if="parseError" class="parse-error">{{ parseError }}</div>
      <button 
        v-if="offerText.trim()" 
        class="text-submit-btn"
        @click="parseAndSubmitTextOffer"
        :disabled="isLoading"
      >
        {{ isLoading ? '...' : '🚀 ارسال' }}
      </button>
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
      <div class="filter-bar">
        <button :class="{ active: filterType === 'all' }" @click="filterType = 'all'">همه</button>
        <button :class="{ active: filterType === 'buy' }" @click="filterType = 'buy'">🟢 خرید</button>
        <button :class="{ active: filterType === 'sell' }" @click="filterType = 'sell'">🔴 فروش</button>
      </div>
      
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
            <span class="offer-type">
              {{ offer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}
            </span>
            <span class="offer-time">{{ offer.created_at }}</span>
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
                <button class="trade-btn" @click="openTradeModal(offer)">
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
                  {{ amount }} عدد
                </button>
              </template>
            </div>
            <span v-else class="own-offer-badge">لفظ شما</span>
          </div>
        </div>
      </div>
    </div>
    
    <!-- Tab: My Offers -->
    <div v-if="activeTab === 'my_offers'" class="tab-content">
      <div v-if="myOffers.length === 0" class="empty-state">
        <p>شما هیچ لفظ فعالی ندارید.</p>
      </div>
      
      <div v-else class="offers-list">
        <div 
          v-for="offer in myOffers" 
          :key="offer.id" 
          class="offer-card my-offer"
          :class="offer.offer_type"
        >
          <div class="offer-header">
            <span class="offer-type">
              {{ offer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}
            </span>
            <span class="remaining">{{ offer.remaining_quantity }}/{{ offer.quantity }}</span>
          </div>
          
          <div class="offer-body">
            <div class="offer-main">
              <span class="commodity">{{ offer.commodity_name }}</span>
              <span class="quantity">{{ offer.remaining_quantity }}/{{ offer.quantity }} عدد</span>
              <span class="price">{{ offer.price.toLocaleString() }}</span>
            </div>
          </div>
          
          <div class="offer-footer">
            <span class="offer-time">{{ offer.created_at }}</span>
            <button class="expire-btn" @click="expireOffer(offer.id)">❌ منقضی کردن</button>
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
    
    <!-- Bottom Fixed Buttons (Buy / Sell) -->
    <div class="bottom-actions" v-if="!showCreateWizard && !showTradeModal">
      <button class="action-btn buy" @click="startCreateOffer('buy')">
        🟢 خرید
      </button>
      <button class="action-btn sell" @click="startCreateOffer('sell')">
        🔴 فروش
      </button>
    </div>
    
    <!-- Create Offer Wizard Modal -->
    <div v-if="showCreateWizard" class="wizard-overlay">
      <div class="wizard-modal">
        <div class="wizard-header">
          <button class="wizard-back" @click="goBack">←</button>
          <h2>
            {{ newOffer.offer_type === 'buy' ? '🟢 ثبت لفظ خرید' : '🔴 ثبت لفظ فروش' }}
          </h2>
          <button class="wizard-close" @click="closeWizard">✕</button>
        </div>
        
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
              placeholder="یا تعداد دلخواه..."
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
            placeholder="مثال: 10 15 25"
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
            <p><strong>قیمت:</strong> {{ newOffer.price.toLocaleString() }} تومان</p>
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
          <button class="close-btn" @click="showTradeModal = false">✕</button>
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
  padding: 12px;
  margin-bottom: 16px;
  border: 1px solid var(--border-color);
}

.text-offer-input {
  width: 100%;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 10px;
  font-size: 14px;
  resize: none;
  font-family: inherit;
}

.parse-error {
  color: #ef4444;
  font-size: 12px;
  margin-top: 8px;
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
  gap: 8px;
  margin-bottom: 16px;
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
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.trade-btn {
  padding: 8px 16px;
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
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

/* Empty State */
.empty-state {
  text-align: center;
  padding: 40px 20px;
  color: var(--text-secondary);
}

/* Bottom Fixed Buttons */
.bottom-actions {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  display: flex;
  gap: 12px;
  padding: 16px;
  background: var(--bg-color);
  border-top: 1px solid var(--border-color);
  z-index: 100;
}

.action-btn {
  flex: 1;
  padding: 16px;
  border: none;
  border-radius: 14px;
  font-size: 18px;
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
  background: #f0f0f0;
  border: none;
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 18px;
  cursor: pointer;
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
  background: var(--card-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  font-size: 13px;
  cursor: pointer;
}

.commodity-btn:hover {
  background: #007AFF;
  color: white;
  border-color: #007AFF;
}

.quantity-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 16px;
}

.qty-btn {
  padding: 16px;
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
  border: none;
  border-radius: 10px;
  font-size: 18px;
  font-weight: 600;
  cursor: pointer;
}

.custom-qty {
  display: flex;
  gap: 10px;
}

.qty-input, .price-input, .lot-input {
  flex: 1;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  font-size: 16px;
  text-align: center;
}

.confirm-btn {
  padding: 14px 24px;
  background: linear-gradient(135deg, #007AFF, #0056b3);
  color: white;
  border: none;
  border-radius: 10px;
  font-weight: 600;
  cursor: pointer;
}

.confirm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
</style>
