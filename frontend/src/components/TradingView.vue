<script setup lang="ts">
import { ref, onMounted, computed, watch, onUnmounted } from 'vue'
import LoadingSkeleton from './LoadingSkeleton.vue'
import OfferPreviewModal from './OfferPreviewModal.vue'
import TradeLotSuggestionAlert from './TradeLotSuggestionAlert.vue'
import { useWebSocket } from '../composables/useWebSocket'
import { useTradingSort } from '../composables/useTradingSort'
import { resolveTradeParticipantProfileTarget } from '../utils/accountantChatIdentity'
import { apiFetch, apiFetchJson } from '../utils/auth'

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
  user_id: number | null
  user_account_name: string
  is_own_offer: boolean
  offer_type: 'buy' | 'sell'
  commodity_id: number
  commodity_name: string
  quantity: number
  remaining_quantity: number
  price: number
  raw_price?: number
  market_published_price?: number
  viewer_effective_price?: number
  is_wholesale: boolean
  lot_sizes: number[] | null
  notes: string | null
  status: string
  channel_message_id: number | null
  customer_badge_visible?: boolean
  customer_management_name?: string | null
  customer_tier?: 'tier1' | 'tier2' | null
  created_at: string
  expires_at_ts?: number
}

interface Trade {
  id: number
  trade_number: number
  offer_id?: number | null
  trade_type: string
  commodity_id?: number | null
  commodity_name: string
  quantity: number
  price: number
  status?: string | null
  offer_user_id: number | null
  offer_user_name: string | null
  offer_user_profile_user_id?: number | null
  offer_user_profile_account_name?: string | null
  offer_user_resolved_from_accountant_id?: number | null
  offer_user_highlight_accountant_user_id?: number | null
  offer_user_highlight_accountant_relation_display_name?: string | null
  responder_user_id: number | null
  responder_user_name: string | null
  responder_user_profile_user_id?: number | null
  responder_user_profile_account_name?: string | null
  responder_user_resolved_from_accountant_id?: number | null
  responder_user_highlight_accountant_user_id?: number | null
  responder_user_highlight_accountant_relation_display_name?: string | null
  counterparty_user_id?: number | null
  counterparty_name?: string | null
  counterparty_profile_user_id?: number | null
  counterparty_profile_account_name?: string | null
  counterparty_highlight_accountant_user_id?: number | null
  counterparty_highlight_accountant_relation_display_name?: string | null
  customer_context_visible?: boolean
  customer_context_user_id?: number | null
  customer_context_management_name?: string | null
  customer_context_tier?: 'tier1' | 'tier2' | null
  trade_path_kind?: string | null
  trade_path_summary?: string | null
  audience_user_ids?: number[]
  recipient_specific?: boolean
  created_at: string
}

function normalizeTradeRealtimePayload(payload: unknown): Trade | null {
  if (!payload || typeof payload !== 'object') {
    return null
  }

  const rawTrade = payload as Partial<Trade>
  const tradeId = Number(rawTrade.id)
  const tradeNumber = Number(rawTrade.trade_number)
  const quantity = Number(rawTrade.quantity)
  const price = Number(rawTrade.price)
  const offerUserId = rawTrade.offer_user_id == null ? null : Number(rawTrade.offer_user_id)
  const responderUserId = rawTrade.responder_user_id == null ? null : Number(rawTrade.responder_user_id)

  if (!Number.isFinite(tradeId) || !Number.isFinite(tradeNumber) || !Number.isFinite(quantity) || !Number.isFinite(price)) {
    return null
  }

  if (typeof rawTrade.trade_type !== 'string' || typeof rawTrade.commodity_name !== 'string' || !rawTrade.commodity_name.trim()) {
    return null
  }

  return {
    id: tradeId,
    trade_number: tradeNumber,
    offer_id: rawTrade.offer_id ?? null,
    trade_type: rawTrade.trade_type,
    commodity_id: rawTrade.commodity_id ?? null,
    commodity_name: rawTrade.commodity_name,
    quantity,
    price,
    status: rawTrade.status ?? null,
    offer_user_id: Number.isFinite(offerUserId as number) ? offerUserId : null,
    offer_user_name: rawTrade.offer_user_name ?? null,
    offer_user_profile_user_id: rawTrade.offer_user_profile_user_id ?? null,
    offer_user_profile_account_name: rawTrade.offer_user_profile_account_name ?? null,
    offer_user_resolved_from_accountant_id: rawTrade.offer_user_resolved_from_accountant_id ?? null,
    offer_user_highlight_accountant_user_id: rawTrade.offer_user_highlight_accountant_user_id ?? null,
    offer_user_highlight_accountant_relation_display_name: rawTrade.offer_user_highlight_accountant_relation_display_name ?? null,
    responder_user_id: Number.isFinite(responderUserId as number) ? responderUserId : null,
    responder_user_name: rawTrade.responder_user_name ?? null,
    responder_user_profile_user_id: rawTrade.responder_user_profile_user_id ?? null,
    responder_user_profile_account_name: rawTrade.responder_user_profile_account_name ?? null,
    responder_user_resolved_from_accountant_id: rawTrade.responder_user_resolved_from_accountant_id ?? null,
    responder_user_highlight_accountant_user_id: rawTrade.responder_user_highlight_accountant_user_id ?? null,
    responder_user_highlight_accountant_relation_display_name: rawTrade.responder_user_highlight_accountant_relation_display_name ?? null,
    counterparty_user_id: rawTrade.counterparty_user_id ?? null,
    counterparty_name: rawTrade.counterparty_name ?? null,
    counterparty_profile_user_id: rawTrade.counterparty_profile_user_id ?? null,
    counterparty_profile_account_name: rawTrade.counterparty_profile_account_name ?? null,
    counterparty_highlight_accountant_user_id: rawTrade.counterparty_highlight_accountant_user_id ?? null,
    counterparty_highlight_accountant_relation_display_name: rawTrade.counterparty_highlight_accountant_relation_display_name ?? null,
    customer_context_visible: rawTrade.customer_context_visible === true,
    customer_context_user_id: rawTrade.customer_context_user_id ?? null,
    customer_context_management_name: rawTrade.customer_context_management_name ?? null,
    customer_context_tier: rawTrade.customer_context_tier === 'tier1' || rawTrade.customer_context_tier === 'tier2'
      ? rawTrade.customer_context_tier
      : null,
    trade_path_kind: typeof rawTrade.trade_path_kind === 'string' ? rawTrade.trade_path_kind : null,
    trade_path_summary: typeof rawTrade.trade_path_summary === 'string' ? rawTrade.trade_path_summary : null,
    audience_user_ids: Array.isArray((rawTrade as { audience_user_ids?: unknown }).audience_user_ids)
      ? (rawTrade as { audience_user_ids: unknown[] }).audience_user_ids
          .map((value) => Number(value))
          .filter((value) => Number.isFinite(value))
      : [],
    recipient_specific: (rawTrade as { recipient_specific?: unknown }).recipient_specific === true,
    created_at: typeof rawTrade.created_at === 'string' && rawTrade.created_at.trim() ? rawTrade.created_at : 'همین الان',
  }
}

function mergeRealtimeTrade(existingTrade: Trade, incomingTrade: Trade): Trade {
  return {
    ...existingTrade,
    ...incomingTrade,
    offer_user_name: incomingTrade.offer_user_name ?? existingTrade.offer_user_name,
    offer_user_profile_user_id: incomingTrade.offer_user_profile_user_id ?? existingTrade.offer_user_profile_user_id,
    offer_user_profile_account_name: incomingTrade.offer_user_profile_account_name ?? existingTrade.offer_user_profile_account_name,
    offer_user_resolved_from_accountant_id:
      incomingTrade.offer_user_resolved_from_accountant_id ?? existingTrade.offer_user_resolved_from_accountant_id,
    offer_user_highlight_accountant_user_id:
      incomingTrade.offer_user_highlight_accountant_user_id ?? existingTrade.offer_user_highlight_accountant_user_id,
    offer_user_highlight_accountant_relation_display_name:
      incomingTrade.offer_user_highlight_accountant_relation_display_name
      ?? existingTrade.offer_user_highlight_accountant_relation_display_name,
    responder_user_name: incomingTrade.responder_user_name ?? existingTrade.responder_user_name,
    responder_user_profile_user_id:
      incomingTrade.responder_user_profile_user_id ?? existingTrade.responder_user_profile_user_id,
    responder_user_profile_account_name:
      incomingTrade.responder_user_profile_account_name ?? existingTrade.responder_user_profile_account_name,
    responder_user_resolved_from_accountant_id:
      incomingTrade.responder_user_resolved_from_accountant_id ?? existingTrade.responder_user_resolved_from_accountant_id,
    responder_user_highlight_accountant_user_id:
      incomingTrade.responder_user_highlight_accountant_user_id ?? existingTrade.responder_user_highlight_accountant_user_id,
    responder_user_highlight_accountant_relation_display_name:
      incomingTrade.responder_user_highlight_accountant_relation_display_name
      ?? existingTrade.responder_user_highlight_accountant_relation_display_name,
    counterparty_user_id: incomingTrade.counterparty_user_id ?? existingTrade.counterparty_user_id,
    counterparty_name: incomingTrade.counterparty_name ?? existingTrade.counterparty_name,
    counterparty_profile_user_id:
      incomingTrade.counterparty_profile_user_id ?? existingTrade.counterparty_profile_user_id,
    counterparty_profile_account_name:
      incomingTrade.counterparty_profile_account_name ?? existingTrade.counterparty_profile_account_name,
    counterparty_highlight_accountant_user_id:
      incomingTrade.counterparty_highlight_accountant_user_id ?? existingTrade.counterparty_highlight_accountant_user_id,
    counterparty_highlight_accountant_relation_display_name:
      incomingTrade.counterparty_highlight_accountant_relation_display_name
      ?? existingTrade.counterparty_highlight_accountant_relation_display_name,
    customer_context_visible: incomingTrade.customer_context_visible || existingTrade.customer_context_visible,
    customer_context_user_id: incomingTrade.customer_context_user_id ?? existingTrade.customer_context_user_id,
    customer_context_management_name:
      incomingTrade.customer_context_management_name ?? existingTrade.customer_context_management_name,
    customer_context_tier: incomingTrade.customer_context_tier ?? existingTrade.customer_context_tier,
    trade_path_kind: incomingTrade.trade_path_kind ?? existingTrade.trade_path_kind,
    trade_path_summary: incomingTrade.trade_path_summary ?? existingTrade.trade_path_summary,
    audience_user_ids: incomingTrade.audience_user_ids?.length ? incomingTrade.audience_user_ids : existingTrade.audience_user_ids,
    recipient_specific: incomingTrade.recipient_specific || existingTrade.recipient_specific,
  }
}

function upsertTradeFromRealtime(payload: unknown): boolean {
  const trade = normalizeTradeRealtimePayload(payload)
  if (!trade) {
    return false
  }

  const currentUserId = Number(props.user?.id)
  if (!Number.isFinite(currentUserId)) {
    return false
  }

  const isTargetedAudience = Array.isArray(trade.audience_user_ids)
    && trade.audience_user_ids.some((audienceUserId) => Number(audienceUserId) === currentUserId)
  const isParticipant = Number(trade.offer_user_id) === currentUserId || Number(trade.responder_user_id) === currentUserId
  if (!isParticipant && !trade.recipient_specific && !isTargetedAudience) {
    return true
  }

  const existingTrade = myTrades.value.find((currentTrade) => currentTrade.id === trade.id)
  const nextTrade = existingTrade ? mergeRealtimeTrade(existingTrade, trade) : trade
  myTrades.value = [nextTrade, ...myTrades.value.filter((currentTrade) => currentTrade.id !== trade.id)]
  return true
}

interface TradingSettings {
  offer_min_quantity: number
  offer_max_quantity: number
  lot_min_size: number
  lot_max_count: number
  offer_expiry_minutes: number
}

interface TradeLotSuggestionState {
  title: string
  introText: string
  offerId: number
  offerType: 'buy' | 'sell' | ''
  offerTypeLabel: string
  commodityName: string
  price: number
  remainingQuantity: number
  lotSummary: string
  availableLots: number[]
  expiresAtTs?: number | null
  sourceSignature?: string | null
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
const {
  filterType,
  sortCommodity,
  sortDirection,
  showSortPanel,
  filteredOffers,
  toggleSort,
  clearSort,
} = useTradingSort(offers)

// Text Offer Mode
const offerText = ref('')
const parseError = ref('')
const pendingOfferPreview = ref<ParsedOfferPreview | null>(null)
const previewError = ref('')
const previewWarning = ref<OfferPriceWarning | null>(null)
const isSubmittingTextOffer = ref(false)

// Trade Modal State
const showTradeModal = ref(false)
const selectedOffer = ref<Offer | null>(null)
const tradeQuantity = ref(0)
const isTrading = ref(false)
const tradeSuggestion = ref<TradeLotSuggestionState | null>(null)

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

function getLotButtons(offer: Offer): number[] {
  if (offer.is_wholesale || !offer.lot_sizes?.length) {
    return [offer.remaining_quantity]
  }

  const remaining = typeof offer.remaining_quantity === 'number' ? offer.remaining_quantity : offer.quantity
  if (remaining <= 0) {
    return []
  }

  const uniqueLots = [...new Set([remaining, ...(offer.lot_sizes || [])].filter((amount) => amount > 0 && amount <= remaining))]
  // Keep ascending data order so the largest amount renders on the left in RTL.
  return uniqueLots.sort((a, b) => a - b)
}

function formatLotSummary(amounts: number[]): string {
  return [...amounts].sort((a, b) => b - a).join(' + ')
}

function getOfferDisplayPrice(offer: Offer | null | undefined): number {
  const numeric = Number(offer?.viewer_effective_price ?? offer?.price ?? 0)
  return Number.isFinite(numeric) ? numeric : 0
}

function getCustomerTierLabel(tier: Offer['customer_tier'] | Trade['customer_context_tier']): string {
  if (tier === 'tier2') return 'سطح 2'
  if (tier === 'tier1') return 'سطح 1'
  return 'سطح نامشخص'
}

function buildOfferSignature(offer: Offer | null): string | null {
  if (!offer) return null
  const availableLots = getLotButtons(offer)
  const remaining = Number(offer.remaining_quantity ?? offer.quantity ?? 0)
  return [offer.status || '', remaining, availableLots.join(','), offer.expires_at_ts ?? ''].join('|')
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
  isSubmittingTextOffer.value = true
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
      throw new Error(payload?.detail || `خطا: ${response.status}`)
    }

    successMessage.value = 'لفظ متنی ثبت شد'
    offerText.value = ''
    pendingOfferPreview.value = null
    previewWarning.value = null
    await loadOffers()
  } catch (e: any) {
    previewError.value = e.message || 'خطا در ثبت لفظ'
  } finally {
    isSubmittingTextOffer.value = false
  }
}

async function parseAndSubmitTextOffer() {
  if (!offerText.value.trim()) return
  isSubmittingTextOffer.value = true
  parseError.value = ''
  previewError.value = ''
  previewWarning.value = null
  try {
    const res = await api('/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value })
    })
    if (res.success && res.data) {
       pendingOfferPreview.value = res.data as ParsedOfferPreview
    } else {
      parseError.value = res.error || 'خطا در پردازش متن'
    }
  } catch (e: any) {
    parseError.value = e.message
  } finally {
    isSubmittingTextOffer.value = false
  }
}

// Trade Logic
function openTradeModal(offer: Offer, qty?: number) {
  if (offer.is_own_offer) return
  selectedOffer.value = offer
  tradeQuantity.value = qty || offer.remaining_quantity
  showTradeModal.value = true
}

async function executeTrade() {
  if (!selectedOffer.value) return
  isTrading.value = true
  try {
    const response = await apiFetch('/api/trades/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        offer_id: selectedOffer.value.id,
        quantity: tradeQuantity.value
      })
    })
    let data: any = null
    try {
      data = await response.json()
    } catch {
      data = null
    }

    if (!response.ok) {
      if (data?.error_code === 'TRADE_LOT_UNAVAILABLE' && Array.isArray(data.available_lots) && data.available_lots.length > 0) {
        tradeSuggestion.value = {
          title: data.title || 'پیشنهاد معامله',
          introText: data.intro_text || data.detail || 'لات انتخابی شما دیگر در دسترس نیست.',
          offerId: data.offer_id || selectedOffer.value.id,
          offerType: data.offer_type || selectedOffer.value.offer_type || '',
          offerTypeLabel: data.offer_type_label || ((data.offer_type || selectedOffer.value.offer_type) === 'buy' ? 'خرید' : 'فروش'),
          commodityName: data.commodity_name || selectedOffer.value.commodity_name || 'کالا',
          price: Number(data.price ?? getOfferDisplayPrice(selectedOffer.value) ?? 0),
          remainingQuantity: Number(data.remaining_quantity || selectedOffer.value.remaining_quantity || tradeQuantity.value),
          lotSummary: data.lot_summary || (Array.isArray(data.available_lots) ? formatLotSummary(data.available_lots) : ''),
          availableLots: data.available_lots,
          expiresAtTs: selectedOffer.value.expires_at_ts ?? null,
          sourceSignature: buildOfferSignature(selectedOffer.value),
        }
        return
      }
      throw new Error(data?.detail || 'خطا در انجام معامله')
    }

    tradeSuggestion.value = null
    successMessage.value = 'معامله انجام شد'
    showTradeModal.value = false
    await loadOffers()
  } catch (e: any) {
    error.value = e.message
  } finally {
    isTrading.value = false
  }
}

function closeTradeSuggestion() {
  tradeSuggestion.value = null
}

async function executeSuggestedTrade(amount: number) {
  if (!selectedOffer.value) return
  tradeQuantity.value = amount
  await executeTrade()
}

function syncTradeSuggestionFromOffers() {
  if (!tradeSuggestion.value) return
  const sourceOffer = offers.value.find((offer) => offer.id === tradeSuggestion.value?.offerId)
  const currentSourceSignature = buildOfferSignature(sourceOffer ?? null)
  if (currentSourceSignature === tradeSuggestion.value.sourceSignature) {
    return
  }
  if (!sourceOffer) {
    closeTradeSuggestion()
    return
  }

  const expired = !!sourceOffer.expires_at_ts && sourceOffer.expires_at_ts <= now.value
  const remaining = Number(sourceOffer.remaining_quantity ?? sourceOffer.quantity ?? 0)
  const availableLots = getLotButtons(sourceOffer as any)
  if (expired || sourceOffer.status !== 'active' || remaining <= 0 || availableLots.length === 0) {
    closeTradeSuggestion()
    return
  }

  tradeSuggestion.value = {
    ...tradeSuggestion.value,
    offerType: sourceOffer.offer_type,
    offerTypeLabel: sourceOffer.offer_type === 'buy' ? 'خرید' : 'فروش',
    commodityName: sourceOffer.commodity_name,
    price: getOfferDisplayPrice(sourceOffer),
    remainingQuantity: remaining,
    lotSummary: formatLotSummary(availableLots),
    availableLots,
    expiresAtTs: sourceOffer.expires_at_ts ?? null,
    sourceSignature: currentSourceSignature,
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

function getStatusLabel(s: string) {
  const map: Record<string, string> = { active: 'فعال', completed: 'تکمیل', expired: 'منقضی', cancelled: 'لغو' }
  return map[s] || s
}

function getUserTradeType(t: Trade) {
  if (t.responder_user_id === props.user?.id) return t.trade_type
  return t.trade_type === 'active' ? 'active' : (t.trade_type === 'buy' ? 'sell' : 'buy')
}

function getTradeCounterpartyLabel(trade: Trade) {
  if (typeof trade.counterparty_name === 'string' && trade.counterparty_name.trim()) {
    return trade.counterparty_name
  }
  return Number(trade.responder_user_id) === Number(props.user?.id)
    ? trade.offer_user_name
    : trade.responder_user_name
}

function getTradeCounterpartyProfileTarget(trade: Trade) {
  if (
    Number.isInteger(trade.counterparty_profile_user_id)
    && typeof trade.counterparty_profile_account_name === 'string'
    && trade.counterparty_profile_account_name.trim()
  ) {
    return {
      id: Number(trade.counterparty_profile_user_id),
      account_name: trade.counterparty_profile_account_name,
      highlight_accountant_user_id: Number.isInteger(trade.counterparty_highlight_accountant_user_id)
        ? Number(trade.counterparty_highlight_accountant_user_id)
        : null,
      highlight_accountant_relation_display_name:
        typeof trade.counterparty_highlight_accountant_relation_display_name === 'string'
          ? trade.counterparty_highlight_accountant_relation_display_name
          : null,
    }
  }
  return resolveTradeParticipantProfileTarget(
    trade,
    Number(trade.responder_user_id) === Number(props.user?.id) ? 'offer_user' : 'responder_user',
  )
}

function showTradeCustomerContext(trade: Trade) {
  if (!trade.customer_context_visible) {
    return false
  }
  return Boolean(trade.customer_context_management_name || trade.customer_context_tier)
}

function openTradeCounterpartyProfile(trade: Trade) {
  const target = getTradeCounterpartyProfileTarget(trade)
  if (!target) {
    return
  }
  emit('navigate', 'public_profile', target)
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

function handleTradeCreatedWS(data: any) {
  if (activeTab.value === 'offers') loadOffers(true)
  else if (activeTab.value === 'my_trades' && !upsertTradeFromRealtime(data)) loadMyTrades(true)
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
  if (tradeSuggestion.value?.expiresAtTs && tradeSuggestion.value.expiresAtTs <= now.value) {
    closeTradeSuggestion()
  }
})

watch(offers, () => {
  syncTradeSuggestionFromOffers()
}, { deep: true })

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
    <TradeLotSuggestionAlert
      :show="!!tradeSuggestion"
      :title="tradeSuggestion?.title || ''"
      :intro-text="tradeSuggestion?.introText || ''"
      :offer-type="tradeSuggestion?.offerType || ''"
      :offer-type-label="tradeSuggestion?.offerTypeLabel || ''"
      :commodity-name="tradeSuggestion?.commodityName || ''"
      :price="tradeSuggestion?.price || 0"
      :remaining-quantity="tradeSuggestion?.remainingQuantity || 0"
      :lot-summary="tradeSuggestion?.lotSummary || ''"
      :available-lots="tradeSuggestion?.availableLots || []"
      :busy="isTrading"
      :busy-amount="tradeQuantity"
      :auto-close-seconds="15"
      @close="closeTradeSuggestion"
      @select-lot="executeSuggestedTrade"
    />
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
              <span class="price">{{ getOfferDisplayPrice(offer).toLocaleString() }}</span>
            </div>
            <div v-if="offer.customer_badge_visible" class="customer-context-row">
              <span class="customer-context-badge">مشتری</span>
              <span v-if="offer.customer_management_name" class="customer-context-name">{{ offer.customer_management_name }}</span>
              <span v-if="offer.customer_tier" class="customer-context-tier">{{ getCustomerTierLabel(offer.customer_tier) }}</span>
            </div>
            <div v-if="offer.notes" class="offer-notes">
              توضیحات: {{ offer.notes }}
            </div>
          </div>
          
          <div class="offer-footer">
            <div class="trade-buttons" v-if="!offer.is_own_offer">
              <template v-if="offer.is_wholesale || !offer.lot_sizes">
                <button class="trade-btn full-width" @click="openTradeModal(offer)">
                  {{ offer.remaining_quantity }} عدد
                </button>
              </template>
              <template v-else>
                <button 
                  v-for="amount in getLotButtons(offer)"
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
              <span class="price">{{ getOfferDisplayPrice(offer).toLocaleString() }}</span>
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
              <span v-else class="text-only-note">ثبت مجدد فقط با متن</span>
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
                v-if="!getTradeCounterpartyProfileTarget(trade)"
                class="info-value"
              >
                {{ getTradeCounterpartyLabel(trade) }}
              </span>
              <span
                v-else
                class="info-value profile-link"
                @click.stop="openTradeCounterpartyProfile(trade)"
              >
                {{ getTradeCounterpartyLabel(trade) }}
              </span>
            </div>
            <div v-if="showTradeCustomerContext(trade)" class="trade-info-row">
              <span class="info-label">🪪 مشتری:</span>
              <span class="info-value trade-customer-context-value">
                <span class="customer-context-badge">مشتری</span>
                <span v-if="trade.customer_context_management_name" class="customer-context-name">{{ trade.customer_context_management_name }}</span>
                <span v-if="trade.customer_context_tier" class="customer-context-tier">{{ getCustomerTierLabel(trade.customer_context_tier) }}</span>
              </span>
            </div>
            <div v-if="trade.trade_path_summary" class="trade-info-row">
              <span class="info-label">🧭 مسیر:</span>
              <span class="info-value">{{ trade.trade_path_summary }}</span>
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

    <!-- Bottom Fixed Section (Text Input) -->
    <div class="bottom-fixed" v-if="!showTradeModal">
      <div class="text-offer-section">
        <div class="text-offer-container">
          <button
            class="send-btn"
            @click="parseAndSubmitTextOffer"
            :disabled="isLoading || isSubmittingTextOffer || !offerText.trim()"
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
    </div>

    <OfferPreviewModal
      v-if="pendingOfferPreview"
      :offer="pendingOfferPreview"
      :submitting="isSubmittingTextOffer"
      :error="previewError"
      :warning="previewWarning"
      @confirm="confirmOfferPreview"
      @cancel="cancelOfferPreview"
    />
    
    <!-- Trade Modal -->
    <div v-if="showTradeModal && selectedOffer" class="modal-overlay" @click.self="showTradeModal = false">
      <div class="modal">
        <div class="modal-header">
          <h2>{{ selectedOffer.offer_type === 'buy' ? '🔴 فروش' : '🟢 خرید' }}</h2>
        </div>
        
        <div class="modal-body">
          <p><strong>کالا:</strong> {{ selectedOffer.commodity_name }}</p>
          <p><strong>قیمت:</strong> {{ getOfferDisplayPrice(selectedOffer).toLocaleString() }}</p>
          <p><strong>تعداد:</strong> {{ tradeQuantity }}</p>
          <p><strong>مجموع:</strong> {{ (getOfferDisplayPrice(selectedOffer) * tradeQuantity).toLocaleString() }} تومان</p>
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

.customer-context-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}

.customer-context-badge,
.customer-context-tier {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 800;
  line-height: 1;
}

.customer-context-badge {
  color: #92400e;
  background: rgba(251, 191, 36, 0.2);
  border: 1px solid rgba(245, 158, 11, 0.35);
}

.customer-context-name {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
}

.customer-context-tier {
  color: #1d4ed8;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.22);
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

.text-only-note {
  font-size: 12px;
  color: var(--text-secondary);
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

