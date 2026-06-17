<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from 'vue'
import { Bell, BellOff, ChevronDown, Loader2, Send } from 'lucide-vue-next'
import { useOffers } from '../composables/useOffers'
import { useWebSocket } from '../composables/useWebSocket'
import { pushBackState, popBackState, clearBackStack } from '../composables/useBackButton'
import OffersList from '../components/OffersList.vue'
import OfferPreviewModal from '../components/OfferPreviewModal.vue'
import { AppEmptyState, AppFilterChips, AppIconButton, AppLoadingState, AppStatusBadge } from '../components/ui'
import { apiFetch, apiFetchJson } from '../utils/auth'
import { cacheCurrentUserSummary, currentUserSummary } from '../utils/currentUser'
import { createHttpErrorFromResponse, getUserFacingErrorMessage } from '../utils/httpErrorPolicy'
import { buildOfferDraftText } from '../utils/offerDraftText'
import { fetchNotificationPreferences, updateNotificationPreferences } from '../services/webPush'

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
  expired_at?: string | null
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
type MarketFilterType = 'all' | 'buy' | 'sell' | 'my'

function normalizeCustomerTier(raw: unknown): CustomerTierValue {
  return raw === 'tier1' || raw === 'tier2' ? raw : null
}

const MARKET_CLOSED_DETAIL = 'بازار در حال حاضر بسته است. لطفاً در زمان فعال بودن بازار اقدام کنید.'
const initialCurrentUserSummary = currentUserSummary.value

const { offers, isLoading, fetchOffers, startPolling, stopPolling } = useOffers()
const { on: wsOn, off: wsOff } = useWebSocket()
const currentUserId = ref<number | undefined>(initialCurrentUserSummary?.id)
const currentUserCustomerTier = ref<CustomerTierValue>(normalizeCustomerTier(initialCurrentUserSummary?.customer_tier))
const marketRuntime = ref<MarketRuntimeState>({
  is_open: true,
  active_web_notice_visible: false,
  offers_since_last_open: 0,
  last_transition_at: null,
  next_transition_at: null,
})

const filterType = ref<MarketFilterType>('all')

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
const pendingOfferIdempotencyKey = ref<string | null>(null)
const recentOffers = ref<RecentOfferSummary[]>([])
const recentOffersOpen = ref(false)
const recentOffersLoading = ref(false)
const recentOffersError = ref('')
const recentOffersRef = ref<HTMLElement | null>(null)
const recentOffersToggleRef = ref<any>(null)
const recentOffersOpenDirection = ref<'above' | 'below'>('above')
const recentOffersDropdownStyle = ref<Record<string, string>>({})
const offerInputRef = ref<HTMLTextAreaElement | null>(null)
const adminMarketMessage = ref<AdminMarketMessage | null>(null)
const adminMarketMessageExpanded = ref(false)
const marketOfferPushEnabled = ref(true)
const marketOfferPushLoading = ref(false)
const marketOfferPushSaving = ref(false)
const marketOfferPushError = ref('')
const isTier2Customer = computed(() => currentUserCustomerTier.value === 'tier2')
const visibleTabs = computed<MarketFilterType[]>(() => (
  isTier2Customer.value ? ['all', 'buy', 'sell'] : ['all', 'buy', 'sell', 'my']
))
const marketFilterLabels: Record<MarketFilterType, string> = {
  all: 'همه',
  buy: 'خریدار',
  sell: 'فروشنده',
  my: 'لفظ‌های شما',
}
const visibleFilterOptions = computed(() => visibleTabs.value.map((tab) => ({
  key: tab,
  label: marketFilterLabels[tab],
})))
const isMarketOpen = computed(() => marketRuntime.value.is_open)
const showMarketNotice = computed(() => !marketRuntime.value.is_open || marketRuntime.value.active_web_notice_visible)
const marketNoticeText = computed(() => (marketRuntime.value.is_open ? 'شروع فعالیت بازار' : 'پایان فعالیت بازار'))
const marketInputPlaceholder = computed(() => (isMarketOpen.value ? randomPlaceholder.value : 'بازار بسته است'))
const marketOfferPushButtonLabel = computed(() => (
  marketOfferPushEnabled.value
    ? 'خاموش کردن اعلان آفرهای بازار'
    : 'روشن کردن اعلان آفرهای بازار'
))
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

async function fetchMarketNotificationPreferences() {
  marketOfferPushLoading.value = true
  marketOfferPushError.value = ''
  try {
    const preferences = await fetchNotificationPreferences()
    marketOfferPushEnabled.value = preferences?.market_offer_push_enabled !== false
  } catch (e) {
    console.error('Failed to load notification preferences', e)
    marketOfferPushError.value = 'وضعیت اعلان بازار دریافت نشد.'
  } finally {
    marketOfferPushLoading.value = false
  }
}

async function toggleMarketOfferPush() {
  if (marketOfferPushLoading.value || marketOfferPushSaving.value) return
  marketOfferPushSaving.value = true
  marketOfferPushError.value = ''
  const nextEnabled = !marketOfferPushEnabled.value
  try {
    const preferences = await updateNotificationPreferences({
      market_offer_push_enabled: nextEnabled,
    })
    marketOfferPushEnabled.value = preferences?.market_offer_push_enabled !== false
  } catch (e) {
    console.error('Failed to update notification preferences', e)
    marketOfferPushError.value = 'تغییر وضعیت اعلان بازار ذخیره نشد.'
  } finally {
    marketOfferPushSaving.value = false
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

function createMutationIdempotencyKey(prefix: string) {
  const randomPart = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
  return `${prefix}:${randomPart}`.slice(0, 64)
}

function getPendingOfferIdempotencyKey() {
  if (!pendingOfferIdempotencyKey.value) {
    pendingOfferIdempotencyKey.value = createMutationIdempotencyKey('offer')
  }
  return pendingOfferIdempotencyKey.value
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

function getRecentOffersToggleElement() {
  const raw = recentOffersToggleRef.value
  if (!raw) return null
  if (raw instanceof HTMLElement) return raw
  if (raw.$el instanceof HTMLElement) return raw.$el as HTMLElement
  return null
}

function updateRecentOffersMenuPosition() {
  if (!recentOffersOpen.value) return

  const toggleEl = getRecentOffersToggleElement()
  const dropdownEl = recentOffersRef.value?.querySelector('.recent-offers-dropdown') as HTMLElement | null
  if (!toggleEl) return

  const toggleRect = toggleEl.getBoundingClientRect()
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 360
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 640
  const gap = 12
  const edgePadding = 8
  const dropdownWidth = Math.min(304, viewportWidth - (edgePadding * 2))
  const measuredHeight = dropdownEl?.offsetHeight || 280
  const spaceAbove = Math.max(0, toggleRect.top - edgePadding)
  const spaceBelow = Math.max(0, viewportHeight - toggleRect.bottom - edgePadding)
  const shouldOpenAbove = spaceAbove >= measuredHeight + gap || spaceAbove > spaceBelow
  recentOffersOpenDirection.value = shouldOpenAbove ? 'above' : 'below'
  const availableHeight = Math.max(160, (shouldOpenAbove ? spaceAbove : spaceBelow) - gap)
  const dropdownHeight = Math.min(measuredHeight, availableHeight)
  const left = Math.min(
    Math.max(toggleRect.left, edgePadding),
    Math.max(edgePadding, viewportWidth - dropdownWidth - edgePadding),
  )
  const top = shouldOpenAbove
    ? Math.max(edgePadding, toggleRect.top - dropdownHeight - gap)
    : Math.min(
        Math.max(edgePadding, viewportHeight - dropdownHeight - edgePadding),
        toggleRect.bottom + gap,
      )

  recentOffersDropdownStyle.value = {
    position: 'fixed',
    left: `${left}px`,
    top: `${top}px`,
    width: `${dropdownWidth}px`,
    maxHeight: `${availableHeight}px`,
    transformOrigin: shouldOpenAbove ? 'bottom left' : 'top left',
    '--recent-offers-enter-offset': shouldOpenAbove ? '0.35rem' : '-0.35rem',
  }
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
  void nextTick(() => updateRecentOffersMenuPosition())
}

function openRecentOfferPreview(offer: RecentOfferSummary) {
  republishedFromOfferId.value = offer.id
  pendingOfferIdempotencyKey.value = null
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
  pendingOfferIdempotencyKey.value = null
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

function handleRecentOffersViewportChange() {
  updateRecentOffersMenuPosition()
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
  if (isSubmitting.value) return
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
        idempotency_key: getPendingOfferIdempotencyKey(),
      }),
      retryNetwork: false,
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
    pendingOfferIdempotencyKey.value = null
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
  if (isSubmitting.value) return
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
  pendingOfferIdempotencyKey.value = null
  isSubmitting.value = true
  parseError.value = ''
  previewError.value = ''
  previewWarning.value = null
  
  apiFetchJson('/api/offers/parse', {
      method: 'POST',
      body: JSON.stringify({ text: offerText.value }),
      retryNetwork: false,
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
  if (isSubmitting.value) return
  isSubmitting.value = true
  parseError.value = ''
  try {
    const response = await apiFetch('/api/offers/cancel-all', { method: 'POST', retryNetwork: false })
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
      const cachedSummary = cacheCurrentUserSummary(data)
      currentUserId.value = cachedSummary?.id
        ?? (typeof data.id === 'number' ? data.id : Number.isFinite(Number(data.id)) ? Number(data.id) : undefined)
      currentUserCustomerTier.value = cachedSummary?.customer_tier ?? normalizeCustomerTier(data.customer_tier)
        }
    } catch (e) {
        console.error('Failed to load current user', e)
    }
}

watch(isTier2Customer, (blocked) => {
  if (!blocked) return
  if (filterType.value === 'my') {
    filterType.value = 'all'
  }
  offerText.value = ''
  pendingOfferPreview.value = null
  previewWarning.value = null
  previewError.value = ''
  republishedFromOfferId.value = null
  pendingOfferIdempotencyKey.value = null
  closeRecentOffersMenu()
  recentOffers.value = []
})

watch(offerText, () => {
  syncOfferInputHeight()
})

watch(
  () => [recentOffersOpen.value, recentOffersLoading.value, recentOffersError.value, recentOffers.value.length],
  () => {
    if (!recentOffersOpen.value) return
    void nextTick(() => updateRecentOffersMenuPosition())
  },
)

onMounted(() => {
    fetchOffers()
    startPolling()
    fetchCommodities()
    fetchTradingSettings()
  fetchMarketState()
    fetchMarketNotificationPreferences()
    fetchAdminMarketMessage()
    fetchCurrentUser()
  document.addEventListener('pointerdown', handleRecentOffersPointerDown)
  window.addEventListener('resize', handleRecentOffersViewportChange)
  document.addEventListener('scroll', handleRecentOffersViewportChange, true)
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
  window.removeEventListener('resize', handleRecentOffersViewportChange)
  document.removeEventListener('scroll', handleRecentOffersViewportChange, true)
  wsOff('market:opened', handleMarketOpened)
  wsOff('market:closed', handleMarketClosed)
  wsOff('market:notice_hidden', handleMarketNoticeHidden)
  wsOff('market:admin_message_published', handleAdminMarketMessagePublished)
})
</script>

<template>
  <div class="market-page ds-page">

    <div class="market-header">
      <div class="header-controls">
        <AppIconButton
          class="market-notification-toggle"
          :class="{ 'market-notification-toggle--muted': !marketOfferPushEnabled }"
          :label="marketOfferPushButtonLabel"
          :title="marketOfferPushButtonLabel"
          :aria-pressed="marketOfferPushEnabled"
          :disabled="marketOfferPushLoading || marketOfferPushSaving"
          @click="toggleMarketOfferPush"
        >
          <Loader2 v-if="marketOfferPushLoading || marketOfferPushSaving" class="animate-spin" :size="18" />
          <Bell v-else-if="marketOfferPushEnabled" :size="18" />
          <BellOff v-else :size="18" />
        </AppIconButton>
        <AppFilterChips v-model="filterType" class="tabs-container market-filter-chips" label="فیلتر لفظ‌های بازار" :options="visibleFilterOptions" />
      </div>
      <div v-if="marketOfferPushError" class="market-notification-error" role="status">
        {{ marketOfferPushError }}
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
    <div v-if="!isTier2Customer" class="market-action-bar">
      <div class="action-bar-inner">
        <!-- Text Input Row -->
        <div ref="recentOffersRef" class="input-wrapper">
          <AppIconButton
            ref="recentOffersToggleRef"
            class="recent-offers-toggle"
            :class="{ 'recent-offers-toggle--open': recentOffersOpen }"
            :disabled="isSubmitting"
            label="نمایش لفظ‌های اخیر"
            :aria-expanded="recentOffersOpen"
            aria-controls="recent-offers-dropdown"
            @click="toggleRecentOffersMenu"
          >
            <Loader2 v-if="recentOffersLoading" class="animate-spin" :size="17" />
            <ChevronDown v-else :size="17" />
          </AppIconButton>
          <transition name="recent-offers-dropdown">
            <div
              v-if="recentOffersOpen"
              id="recent-offers-dropdown"
              class="recent-offers-dropdown"
              :class="recentOffersOpenDirection === 'above' ? 'recent-offers-dropdown--above' : 'recent-offers-dropdown--below'"
              :style="recentOffersDropdownStyle"
            >
              <AppLoadingState
                v-if="recentOffersLoading"
                class="recent-offers-state"
                label="در حال دریافت لفظ‌های اخیر"
              />
              <AppEmptyState
                v-else-if="recentOffersError"
                class="recent-offers-state recent-offers-state--error"
                title="لفظ‌های اخیر دریافت نشد"
                :message="recentOffersError"
                tone="danger"
              />
              <AppEmptyState
                v-else-if="!recentOffers.length"
                class="recent-offers-state"
                title="لفظ اخیری وجود ندارد"
                message="در یک ساعت گذشته لفظی برای بازنشر ثبت نشده است."
                tone="neutral"
              />
              <button
                v-for="offer in recentOffers"
                :key="offer.id"
                type="button"
                class="recent-offer-item"
                @click="openRecentOfferPreview(offer)"
              >
                <div class="recent-offer-item-main">
                  <AppStatusBadge
                    class="recent-offer-item-badge"
                    :tone="offer.offer_type === 'buy' ? 'success' : 'danger'"
                  >
                    {{ offer.offer_type === 'buy' ? 'خرید' : 'فروش' }}
                  </AppStatusBadge>
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
            aria-label="متن لفظ بازار"
            @input="syncOfferInputHeight"
            @keydown.enter.prevent="parseAndSubmitTextOffer"
          ></textarea>
          <AppIconButton
            @click="parseAndSubmitTextOffer"
            :disabled="!isMarketOpen || !offerText.trim() || isSubmitting"
            class="send-btn"
            variant="neutral"
            label="ارسال لفظ برای پیش‌نمایش"
          >
            <Loader2 v-if="isSubmitting" class="animate-spin" :size="20" />
            <Send v-else :size="20" />
          </AppIconButton>
        </div>
        <div v-if="parseError" class="parse-error">{{ parseError }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.market-page {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  width: 100%;
  max-width: 100%;
  background: var(--ds-bg-page);
  overflow: hidden;
}

/* Header & Filters */
.market-header {
  position: sticky;
  top: 0;
  z-index: 20;
  display: grid;
  gap: 0.65rem;
  padding: calc(0.85rem + var(--ds-safe-area-top, 0px)) 1rem 0.65rem;
  background: var(--ds-bg-card);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--ds-border-light);
}

.header-controls {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  max-width: var(--ds-page-max-width);
  width: 100%;
  margin: 0 auto;
}

.market-notification-toggle {
  flex: 0 0 auto;
  width: var(--ds-touch-target, 48px);
  height: var(--ds-touch-target, 48px);
  color: #0f766e;
  border-color: rgba(15, 118, 110, 0.18);
  background: rgba(240, 253, 250, 0.92);
}

.market-notification-toggle:hover:not(:disabled) {
  color: #115e59;
  border-color: rgba(15, 118, 110, 0.28);
  background: rgba(204, 251, 241, 0.96);
}

.market-notification-toggle--muted {
  color: var(--ds-text-tertiary, #64748b);
  border-color: var(--ds-border-light);
  background: var(--ds-bg-inset);
}

.market-notification-error {
  max-width: var(--ds-page-max-width);
  width: 100%;
  margin: -0.25rem auto 0;
  padding: 0 0.1rem;
  color: var(--ds-danger-600);
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1.5;
}

.tabs-container {
  flex: 1;
  min-width: 0;
}

.market-filter-chips :deep(.ui-filter-chips) {
  width: 100%;
}

.market-filter-chips :deep(.ui-filter-chip) {
  min-height: 2.85rem;
}

.market-filter-chips :deep(.ui-filter-chip:focus-visible),
.market-notification-toggle:focus-visible,
.recent-offers-toggle:focus-visible,
.text-offer-input:focus-visible,
.send-btn:focus-visible,
.recent-offer-item:focus-visible,
.admin-market-message-expand:focus-visible {
  outline: 3px solid rgba(245, 158, 11, 0.34);
  outline-offset: 3px;
}

@media (max-width: 430px) {
  .market-filter-chips :deep(.ui-filter-chip) {
    min-height: 2.72rem;
  }
}

/* Main Content */
.market-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0.9rem 0 calc(7rem + var(--ds-safe-area-bottom, 0px));
  -webkit-overflow-scrolling: touch;
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
  padding: 0.72rem 0.95rem;
  border-radius: var(--ds-radius-lg);
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.98), rgba(236, 253, 245, 0.95));
  border: 1px solid rgba(217, 119, 6, 0.22);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
  color: var(--ds-text-primary);
}

.admin-market-message-title {
  font-size: 0.78rem;
  font-weight: 950;
  color: #92400e;
  margin-bottom: 0.3rem;
}

.admin-market-message-body {
  white-space: pre-wrap;
  line-height: 1.65;
  font-size: 0.86rem;
  font-weight: 700;
}

.admin-market-message--collapsed {
  cursor: pointer;
}

.admin-market-message--collapsed .admin-market-message-body {
  display: -webkit-box;
  line-clamp: 1;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.admin-market-message-expand {
  margin-top: 0.45rem;
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
  padding: 0.75rem 1rem calc(1rem + var(--ds-safe-area-bottom, 0px));
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

.recent-offers-toggle {
  position: absolute;
  right: 3.65rem;
  bottom: 0.58rem;
  width: 2.1rem;
  height: 2.1rem;
  min-width: var(--ds-touch-target, 48px);
  min-height: var(--ds-touch-target, 48px);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  color: var(--ds-accent, #b45309);
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
  padding: 0.45rem;
  display: grid;
  gap: 0.3rem;
  border-radius: 1rem;
  background: rgba(255, 255, 255, 0.98);
  border: 1px solid var(--ds-border-light);
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.14);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  overflow-y: auto;
}

.recent-offers-state {
  margin: 0;
}

.recent-offers-state--error {
  color: var(--ds-danger-700);
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
  align-self: flex-start;
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
  padding: 0.82rem 6.85rem 0.82rem 1rem;
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
  min-width: var(--ds-touch-target, 48px);
  min-height: var(--ds-touch-target, 48px);
  padding: 0;
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
  transform: translateY(var(--recent-offers-enter-offset, 0.35rem));
}

.fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

.ltr { direction: ltr; }
</style>
