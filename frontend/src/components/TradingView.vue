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

// State
const activeTab = ref<'offers' | 'create' | 'my_offers' | 'my_trades'>('offers')
const isLoading = ref(false)
const error = ref('')
const successMessage = ref('')

// Offers list
const offers = ref<Offer[]>([])
const myOffers = ref<Offer[]>([])
const myTrades = ref<Trade[]>([])
const commodities = ref<Commodity[]>([])

// Filter
const filterType = ref<'all' | 'buy' | 'sell'>('all')

// Create offer form
const createMode = ref<'button' | 'text'>('button')
const createStep = ref<'type' | 'commodity' | 'quantity' | 'lot' | 'price' | 'preview'>('type')

// Button mode data
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
const parsedOffer = ref<any>(null)
const parseError = ref('')

// Trade modal
const showTradeModal = ref(false)
const selectedOffer = ref<Offer | null>(null)
const tradeQuantity = ref(0)
const isTrading = ref(false)

// Computed
const filteredOffers = computed(() => {
  if (filterType.value === 'all') return offers.value
  return offers.value.filter(o => o.offer_type === filterType.value)
})

// Available trade quantities for selected offer
const availableTradeQuantities = computed(() => {
  if (!selectedOffer.value) return []
  const offer = selectedOffer.value
  if (offer.is_wholesale || !offer.lot_sizes) {
    return [offer.remaining_quantity]
  }
  const amounts = [offer.remaining_quantity, ...offer.lot_sizes]
  const unique = [...new Set(amounts)].filter(a => a <= offer.remaining_quantity)
  return unique.sort((a, b) => b - a)
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

// Create Offer - Button Mode
function selectOfferType(type: 'buy' | 'sell') {
  newOffer.value.offer_type = type
  createStep.value = 'commodity'
}

function selectCommodity(commodity: Commodity) {
  newOffer.value.commodity_id = commodity.id
  newOffer.value.commodity_name = commodity.name
  createStep.value = 'quantity'
}

function confirmQuantity() {
  if (newOffer.value.quantity < 1 || newOffer.value.quantity > 1000) {
    error.value = 'تعداد باید بین ۱ تا ۱۰۰۰ باشد.'
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
    // برای خُرد، پیشنهاد می‌دهیم
    const qty = newOffer.value.quantity
    if (qty >= 30) {
      newOffer.value.lot_sizes = [Math.floor(qty / 3), Math.floor(qty / 3), qty - 2 * Math.floor(qty / 3)]
    } else if (qty >= 10) {
      newOffer.value.lot_sizes = [Math.floor(qty / 2), qty - Math.floor(qty / 2)]
    } else {
      newOffer.value.lot_sizes = [qty]
    }
    createStep.value = 'price'
  }
}

function confirmPrice() {
  if (newOffer.value.price < 10000 || newOffer.value.price > 9999999) {
    error.value = 'قیمت باید بین ۱۰,۰۰۰ تا ۹,۹۹۹,۹۹۹ باشد.'
    return
  }
  error.value = ''
  createStep.value = 'preview'
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
    resetCreateForm()
    activeTab.value = 'offers'
    await loadOffers()
    
    setTimeout(() => successMessage.value = '', 3000)
  } catch (e: any) {
    error.value = e.message
  } finally {
    isLoading.value = false
  }
}

// Create Offer - Text Mode
async function parseOfferText() {
  if (!offerText.value.trim()) {
    parseError.value = 'متن لفظ را وارد کنید.'
    return
  }
  
  isLoading.value = true
  parseError.value = ''
  
  try {
    const result = await apiFetch('/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
    })
    
    if (result.success) {
      parsedOffer.value = result.data
      // پر کردن فرم از داده پارس شده
      newOffer.value = {
        offer_type: result.data.trade_type,
        commodity_id: result.data.commodity_id,
        commodity_name: result.data.commodity_name,
        quantity: result.data.quantity,
        price: result.data.price,
        is_wholesale: result.data.is_wholesale,
        lot_sizes: result.data.lot_sizes,
        notes: result.data.notes || ''
      }
      createStep.value = 'preview'
    } else {
      parseError.value = result.error || 'خطا در پارس متن'
    }
  } catch (e: any) {
    parseError.value = e.message
  } finally {
    isLoading.value = false
  }
}

function resetCreateForm() {
  createStep.value = 'type'
  createMode.value = 'button'
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
  offerText.value = ''
  parsedOffer.value = null
  parseError.value = ''
  error.value = ''
}

// Trade
function openTradeModal(offer: Offer, quantity?: number) {
  if (offer.user_id === props.user?.id) {
    error.value = 'نمی‌توانید روی لفظ خودتان معامله کنید.'
    setTimeout(() => error.value = '', 3000)
    return
  }
  selectedOffer.value = offer
  // اگر quantity پاس داده شده باشد، از آن استفاده کن، در غیر اینصورت کل موجودی
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
    
    // صبر کوتاه برای اطمینان از commit دیتابیس
    await new Promise(resolve => setTimeout(resolve, 300))
    
    // بارگذاری مجدد لفظ‌ها
    await loadOffers()
    
    // بارگذاری دوم برای اطمینان
    setTimeout(() => loadOffers(), 500)
    
    setTimeout(() => successMessage.value = '', 3000)
  } catch (e: any) {
    error.value = e.message
  } finally {
    isTrading.value = false
  }
}

// Expire offer
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

// Real-time updates با polling سریع (هر ۱.۵ ثانیه)
let pollingInterval: number | null = null

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

// Lifecycle
onMounted(async () => {
  await loadCommodities()
  await loadOffers()
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})

// Watch tab changes
watch(activeTab, async (tab) => {
  if (tab === 'my_offers') await loadMyOffers()
  if (tab === 'my_trades') await loadMyTrades()
  if (tab === 'offers') await loadOffers()
  if (tab === 'create') resetCreateForm()
})
</script>

<template>
  <div class="trading-view">
    <!-- Success/Error Messages -->
    <div v-if="successMessage" class="message success">{{ successMessage }}</div>
    <div v-if="error" class="message error">{{ error }}</div>
    
    <!-- Tabs -->
    <div class="tabs">
      <button 
        :class="{ active: activeTab === 'offers' }"
        @click="activeTab = 'offers'"
      >📊 لفظ‌ها</button>
      <button 
        :class="{ active: activeTab === 'create' }"
        @click="activeTab = 'create'"
      >➕ ثبت لفظ</button>
      <button 
        :class="{ active: activeTab === 'my_offers' }"
        @click="activeTab = 'my_offers'"
      >📝 لفظ‌های من</button>
      <button 
        :class="{ active: activeTab === 'my_trades' }"
        @click="activeTab = 'my_trades'"
      >📜 معاملات</button>
    </div>
    
    <!-- Tab: Active Offers (like Telegram channel) -->
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
            
            <!-- Trade buttons (like Telegram channel) -->
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
    
    <!-- Tab: Create Offer -->
    <div v-if="activeTab === 'create'" class="tab-content create-offer">
      
      <!-- Mode Toggle -->
      <div class="mode-toggle">
        <button :class="{ active: createMode === 'button' }" @click="createMode = 'button'; resetCreateForm()">
          🔘 با دکمه
        </button>
        <button :class="{ active: createMode === 'text' }" @click="createMode = 'text'; resetCreateForm()">
          ✏️ با متن
        </button>
      </div>
      
      <!-- Button Mode -->
      <div v-if="createMode === 'button'" class="button-mode">
        
        <!-- Step 1: Type -->
        <div v-if="createStep === 'type'" class="step">
          <h3>📈 ثبت لفظ جدید</h3>
          <p>نوع معامله را انتخاب کنید:</p>
          <div class="type-buttons">
            <button class="type-btn buy" @click="selectOfferType('buy')">🟢 خرید</button>
            <button class="type-btn sell" @click="selectOfferType('sell')">🔴 فروش</button>
          </div>
        </div>
        
        <!-- Step 2: Commodity -->
        <div v-if="createStep === 'commodity'" class="step">
          <h3>{{ newOffer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}</h3>
          <p>کالا را انتخاب کنید:</p>
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
          <button class="back-btn" @click="createStep = 'type'">⬅️ بازگشت</button>
        </div>
        
        <!-- Step 3: Quantity -->
        <div v-if="createStep === 'quantity'" class="step">
          <h3>{{ newOffer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }} {{ newOffer.commodity_name }}</h3>
          <p>تعداد را وارد کنید (۱ تا ۱۰۰۰):</p>
          <input 
            type="number" 
            v-model.number="newOffer.quantity" 
            min="1" 
            max="1000"
            placeholder="تعداد"
            class="input-field"
          />
          <button class="next-btn" @click="confirmQuantity" :disabled="!newOffer.quantity">ادامه ➡️</button>
          <button class="back-btn" @click="createStep = 'commodity'">⬅️ بازگشت</button>
        </div>
        
        <!-- Step 4: Lot Type -->
        <div v-if="createStep === 'lot'" class="step">
          <h3>{{ newOffer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }} {{ newOffer.commodity_name }} - {{ newOffer.quantity }} عدد</h3>
          <p>نوع فروش را انتخاب کنید:</p>
          <div class="lot-buttons">
            <button class="lot-btn" @click="selectLotType(true)">📦 یکجا</button>
            <button class="lot-btn" @click="selectLotType(false)">📦📦 خُرد</button>
          </div>
          <button class="back-btn" @click="createStep = 'quantity'">⬅️ بازگشت</button>
        </div>
        
        <!-- Step 5: Price -->
        <div v-if="createStep === 'price'" class="step">
          <h3>
            {{ newOffer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }} 
            {{ newOffer.commodity_name }} - {{ newOffer.quantity }} عدد
            ({{ newOffer.is_wholesale ? 'یکجا' : 'خُرد' }})
          </h3>
          <p>💰 قیمت را وارد کنید (5 یا 6 رقم):</p>
          <input 
            type="number" 
            v-model.number="newOffer.price" 
            min="10000" 
            max="9999999"
            placeholder="قیمت"
            class="input-field"
          />
          <button class="next-btn" @click="confirmPrice" :disabled="!newOffer.price">ادامه ➡️</button>
          <button class="back-btn" @click="createStep = 'lot'">⬅️ بازگشت</button>
        </div>
        
        <!-- Step 6: Preview -->
        <div v-if="createStep === 'preview'" class="step preview">
          <h3>پیش‌نمایش لفظ</h3>
          <div class="preview-card" :class="newOffer.offer_type">
            <div class="preview-header">
              {{ newOffer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}
              {{ newOffer.commodity_name }}
              {{ newOffer.quantity }} عدد
              {{ newOffer.price.toLocaleString() }}
            </div>
            <div class="preview-details">
              <p>📦 نوع: {{ newOffer.is_wholesale ? 'یکجا' : `خُرد ${newOffer.lot_sizes?.join(', ')}` }}</p>
              <p v-if="newOffer.notes">توضیحات: {{ newOffer.notes }}</p>
            </div>
          </div>
          
          <div class="notes-input">
            <label>توضیحات (اختیاری):</label>
            <input type="text" v-model="newOffer.notes" maxlength="200" placeholder="توضیحات اضافی..." class="input-field" />
          </div>
          
          <div class="preview-actions">
            <button class="submit-btn" @click="submitOffer" :disabled="isLoading">
              {{ isLoading ? 'در حال ارسال...' : '✅ تایید و ارسال' }}
            </button>
            <button class="cancel-btn" @click="resetCreateForm">❌ انصراف</button>
          </div>
        </div>
      </div>
      
      <!-- Text Mode -->
      <div v-if="createMode === 'text'" class="text-mode">
        <div v-if="createStep !== 'preview'" class="step">
          <h3>✏️ ثبت لفظ با متن</h3>
          <p>متن لفظ را مانند بات وارد کنید:</p>
          <p class="hint">مثال: خ 30تا 75800 یا ف 20 تا 802000</p>
          <textarea 
            v-model="offerText" 
            placeholder="خ 30تا 75800"
            class="text-input"
            rows="3"
          ></textarea>
          <div v-if="parseError" class="parse-error">{{ parseError }}</div>
          <button class="parse-btn" @click="parseOfferText" :disabled="isLoading">
            {{ isLoading ? 'در حال پردازش...' : '🔍 بررسی متن' }}
          </button>
        </div>
        
        <!-- Preview for text mode -->
        <div v-if="createStep === 'preview' && parsedOffer" class="step preview">
          <h3>پیش‌نمایش لفظ</h3>
          <div class="preview-card" :class="newOffer.offer_type">
            <div class="preview-header">
              {{ newOffer.offer_type === 'buy' ? '🟢 خرید' : '🔴 فروش' }}
              {{ newOffer.commodity_name }}
              {{ newOffer.quantity }} عدد
              {{ newOffer.price.toLocaleString() }}
            </div>
            <div class="preview-details">
              <p>📦 نوع: {{ newOffer.is_wholesale ? 'یکجا' : `خُرد ${newOffer.lot_sizes?.join(', ')}` }}</p>
            </div>
          </div>
          
          <div class="preview-actions">
            <button class="submit-btn" @click="submitOffer" :disabled="isLoading">
              {{ isLoading ? 'در حال ارسال...' : '✅ تایید و ارسال' }}
            </button>
            <button class="cancel-btn" @click="resetCreateForm">❌ انصراف</button>
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
            <span class="offer-status">{{ offer.status === 'active' ? '✅ فعال' : offer.status }}</span>
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
    
    <!-- Trade Modal -->
    <div v-if="showTradeModal && selectedOffer" class="modal-overlay" @click.self="showTradeModal = false">
      <div class="modal">
        <h3>تایید معامله</h3>
        
        <div class="modal-content">
          <p>
            <strong>{{ selectedOffer.offer_type === 'buy' ? '🔴 فروش' : '🟢 خرید' }}</strong>
          </p>
          <p>🏷️ کالا: {{ selectedOffer.commodity_name }}</p>
          <p>💰 فی: {{ selectedOffer.price.toLocaleString() }}</p>
          
          <div class="quantity-selector">
            <label>📦 تعداد:</label>
            <div class="quantity-buttons">
              <button 
                v-for="amount in availableTradeQuantities"
                :key="amount"
                :class="{ selected: tradeQuantity === amount }"
                @click="tradeQuantity = amount"
              >
                {{ amount }} عدد
              </button>
            </div>
          </div>
        </div>
        
        <div class="modal-actions">
          <button class="confirm-btn" @click="executeTrade" :disabled="isTrading">
            {{ isTrading ? 'در حال انجام...' : '✅ تایید معامله' }}
          </button>
          <button class="cancel-btn" @click="showTradeModal = false">❌ انصراف</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trading-view {
  padding: 12px;
  direction: rtl;
  font-family: 'Vazirmatn', sans-serif;
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

/* Tabs */
.tabs {
  display: flex;
  gap: 6px;
  margin-bottom: 16px;
  overflow-x: auto;
  padding-bottom: 6px;
}

.tabs button {
  flex: 1;
  padding: 12px 14px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 10px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.3s;
  box-shadow: 0 2px 6px rgba(0,0,0,0.04);
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
  padding: 10px 18px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 20px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
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
  padding: 16px;
  border-right: 4px solid;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

.offer-card.buy, .trade-card.buy {
  border-color: #10b981;
}

.offer-card.sell, .trade-card.sell {
  border-color: #ef4444;
}

.offer-header, .trade-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.offer-type, .trade-type {
  font-weight: 700;
  font-size: 15px;
  color: var(--text-color);
}

.offer-time, .trade-time, .trade-number {
  color: var(--text-secondary);
  font-size: 12px;
}

.offer-body, .trade-body {
  margin-bottom: 12px;
}

.trade-body p {
  margin: 6px 0;
  color: var(--text-color);
}

.offer-main {
  display: flex;
  gap: 16px;
  align-items: center;
  flex-wrap: wrap;
}

.commodity {
  font-weight: 700;
  font-size: 17px;
  color: var(--text-color);
}

.quantity {
  color: #007AFF;
  font-size: 15px;
  font-weight: 500;
}

.price {
  color: #f59e0b;
  font-size: 15px;
  font-weight: 700;
}

.offer-notes {
  color: var(--text-secondary);
  font-size: 13px;
  margin-top: 8px;
}

.offer-footer, .trade-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.offer-owner {
  color: var(--text-secondary);
  font-size: 13px;
}

.trade-buttons {
  display: flex;
  gap: 8px;
}

.trade-btn {
  padding: 10px 18px;
  border: none;
  background: linear-gradient(135deg, #007AFF, #0056b3);
  color: white;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: transform 0.2s;
  box-shadow: 0 3px 10px rgba(0, 122, 255, 0.3);
}

.trade-btn:active {
  transform: scale(0.95);
}

.own-offer-badge {
  background: rgba(245, 158, 11, 0.15);
  color: #d97706;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 500;
}

.empty-state {
  text-align: center;
  padding: 50px 20px;
  color: var(--text-secondary);
  font-size: 15px;
}

/* Create Offer Styles */
.mode-toggle {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
}

.mode-toggle button {
  flex: 1;
  padding: 14px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 10px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
}

.mode-toggle button.active {
  background: linear-gradient(135deg, #8b5cf6, #7c3aed);
  color: white;
  border-color: #8b5cf6;
}

.step {
  background: var(--card-bg);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

.step h3 {
  margin: 0 0 16px 0;
  color: var(--text-color);
  font-size: 18px;
}

.step p {
  color: var(--text-secondary);
  margin-bottom: 16px;
  font-size: 14px;
}

.type-buttons, .lot-buttons {
  display: flex;
  gap: 15px;
}

.type-btn, .lot-btn {
  flex: 1;
  padding: 22px;
  border: none;
  border-radius: 12px;
  font-size: 18px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.2s;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
}

.type-btn.buy {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
}

.type-btn.sell {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  color: white;
}

.lot-btn {
  background: linear-gradient(135deg, #6366f1, #4f46e5);
  color: white;
}

.type-btn:active, .lot-btn:active {
  transform: scale(0.95);
}

.commodity-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  margin-bottom: 16px;
}

.commodity-btn {
  padding: 16px 12px;
  border: 1px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 10px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
}

.commodity-btn:hover, .commodity-btn:active {
  background: #007AFF;
  color: white;
  border-color: #007AFF;
}

.input-field {
  width: 100%;
  padding: 16px;
  border: 2px solid var(--border-color);
  border-radius: 10px;
  background: var(--card-bg);
  color: var(--text-color);
  font-size: 18px;
  text-align: center;
  margin-bottom: 16px;
}

.input-field:focus {
  outline: none;
  border-color: #007AFF;
}

.next-btn, .back-btn, .parse-btn {
  padding: 14px 26px;
  border: none;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  margin-left: 10px;
}

.next-btn {
  background: linear-gradient(135deg, #007AFF, #0056b3);
  color: white;
}

.back-btn {
  background: #f3f4f6;
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
}

.parse-btn {
  background: linear-gradient(135deg, #8b5cf6, #7c3aed);
  color: white;
  width: 100%;
  margin: 0;
}

/* Preview */
.preview-card {
  background: var(--card-bg);
  border-radius: 12px;
  padding: 20px;
  margin-bottom: 20px;
  border-right: 4px solid;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

.preview-card.buy {
  border-color: #10b981;
}

.preview-card.sell {
  border-color: #ef4444;
}

.preview-header {
  font-size: 18px;
  font-weight: 700;
  margin-bottom: 12px;
  color: var(--text-color);
}

.preview-details {
  color: var(--text-secondary);
}

.preview-details p {
  margin: 6px 0;
}

.notes-input {
  margin-bottom: 16px;
}

.notes-input label {
  display: block;
  margin-bottom: 8px;
  color: var(--text-secondary);
  font-size: 14px;
}

.preview-actions {
  display: flex;
  gap: 10px;
}

.submit-btn, .confirm-btn {
  flex: 1;
  padding: 16px;
  border: none;
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  border-radius: 10px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
}

.cancel-btn, .expire-btn {
  padding: 16px 20px;
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #dc2626;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
}

/* Text Mode */
.text-input {
  width: 100%;
  padding: 16px;
  border: 2px solid var(--border-color);
  border-radius: 10px;
  background: var(--card-bg);
  color: var(--text-color);
  font-size: 16px;
  resize: none;
  margin-bottom: 12px;
  font-family: inherit;
}

.text-input:focus {
  outline: none;
  border-color: #007AFF;
}

.hint {
  font-size: 13px;
  color: #007AFF;
  margin-bottom: 12px;
}

.parse-error {
  color: #dc2626;
  margin-bottom: 12px;
  font-size: 14px;
}

/* Modal */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 20px;
}

.modal {
  background: var(--card-bg);
  border-radius: 16px;
  padding: 24px;
  width: 100%;
  max-width: 400px;
  box-shadow: 0 10px 40px rgba(0,0,0,0.2);
}

.modal h3 {
  margin: 0 0 20px 0;
  text-align: center;
  color: var(--text-color);
  font-size: 20px;
}

.modal-content {
  margin-bottom: 20px;
}

.modal-content p {
  margin: 10px 0;
  color: var(--text-color);
  font-size: 15px;
}

.quantity-selector {
  margin-top: 16px;
}

.quantity-selector label {
  display: block;
  margin-bottom: 12px;
  color: var(--text-secondary);
  font-size: 14px;
}

.quantity-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.quantity-buttons button {
  padding: 12px 22px;
  border: 2px solid var(--border-color);
  background: var(--card-bg);
  color: var(--text-color);
  border-radius: 10px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
}

.quantity-buttons button.selected {
  border-color: #007AFF;
  background: rgba(0, 122, 255, 0.1);
  color: #007AFF;
}

.modal-actions {
  display: flex;
  gap: 10px;
}

/* My Offer specific */
.my-offer .offer-status {
  color: #10b981;
  font-size: 13px;
  font-weight: 500;
}

/* Tab Content */
.tab-content {
  min-height: 200px;
}
</style>
