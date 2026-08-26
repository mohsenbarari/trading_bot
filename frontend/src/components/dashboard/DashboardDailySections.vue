<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ChevronDown, PackageCheck, RefreshCw, UsersRound } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import { useWebSocket } from '../../composables/useWebSocket'
import { WS_NOTIFICATION_EVENTS } from '../../types/notifications'
import { apiFetch } from '../../utils/auth'
import type { CurrentUserSummary } from '../../utils/currentUser'
import { formatIranDateTime, IRAN_TIME_ZONE } from '../../utils/iranTime'
import { tradeSettlementLabel } from '../../utils/settlementType'
import {
  AppButton,
  AppDisclosure,
  AppEmptyState,
  AppInput,
  AppInsetGroup,
  AppListItem,
  AppStatusBadge,
} from '../ui'

interface DashboardTrade {
  id: number
  trade_number: number
  trade_type: 'buy' | 'sell'
  settlement_type: string
  commodity_id: number
  commodity_name: string
  quantity: number
  price: number
  status: string
  offer_user_id: number | null
  offer_user_name: string | null
  responder_user_id: number | null
  responder_user_name: string | null
  counterparty_name: string | null
  offer_notes: string | null
  created_at: string
}

interface DashboardProjectUser {
  id: number
  account_name: string
}

interface DashboardCommodity {
  id: number
  name: string
  aliases: string[]
}

type BadgeTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'

const props = defineProps<{
  user: CurrentUserSummary
}>()

const PROJECT_USERS_PAGE_SIZE = 25
const router = useRouter()
const { on: wsOn, off: wsOff } = useWebSocket()
const trades = ref<DashboardTrade[]>([])
const tradesLoading = ref(false)
const tradesError = ref(false)
const coworkersOpen = ref(false)
const coworkers = ref<DashboardProjectUser[]>([])
const coworkersLoading = ref(false)
const coworkersLoadingMore = ref(false)
const coworkersLoaded = ref(false)
const coworkersError = ref(false)
const coworkersHasMore = ref(false)
const coworkersOffset = ref(0)
const coworkersQuery = ref('')
const lastCoworkersQuery = ref('')
const commoditiesOpen = ref(false)
const commodities = ref<DashboardCommodity[]>([])
const commoditiesLoading = ref(false)
const commoditiesLoaded = ref(false)
const commoditiesError = ref(false)
let tradesController: AbortController | null = null
let coworkersController: AbortController | null = null
let commoditiesController: AbortController | null = null
let identityGeneration = 0
let tradeRefreshTimer: ReturnType<typeof setTimeout> | null = null

const normalizedCurrentUserId = computed(() => normalizeId(props.user.id))
const perspectiveUserId = computed(() => {
  const ownerId = normalizeId(props.user.accountant_owner_user_id)
  return props.user.is_accountant === true && ownerId !== null
    ? ownerId
    : normalizedCurrentUserId.value
})
const coworkersTargetId = computed(() => {
  if (props.user.is_customer === true || props.user.customer_tier) return null
  return perspectiveUserId.value
})
const coworkersAvailable = computed(() => coworkersTargetId.value !== null)
const identityKey = computed(() => [
  normalizedCurrentUserId.value ?? 'unknown',
  perspectiveUserId.value ?? 'unknown',
  props.user.is_customer === true ? 'customer' : 'non-customer',
].join(':'))
const tradeCountLabel = computed(() => `${formatNumber(trades.value.length)} معامله`)
const coworkersMetaLabel = computed(() => {
  if (!coworkersAvailable.value) return 'محدود'
  if (coworkersLoading.value) return 'در حال دریافت'
  if (!coworkersLoaded.value) return 'برای مشاهده باز کنید'
  if (coworkersError.value) return 'خطا'
  return `${formatNumber(coworkers.value.length)} همکار`
})
const commoditiesMetaLabel = computed(() => {
  if (commoditiesLoading.value) return 'در حال دریافت'
  if (!commoditiesLoaded.value) return 'برای مشاهده باز کنید'
  if (commoditiesError.value) return 'خطا'
  return `${formatNumber(commodities.value.length)} کالا`
})

function normalizeId(value: unknown) {
  const id = Number(value)
  return Number.isInteger(id) && id > 0 ? id : null
}

function normalizeText(value: unknown, maxLength = 320) {
  if (typeof value !== 'string') return ''
  return value.trim().slice(0, maxLength)
}

function normalizeNullableId(value: unknown) {
  return value == null ? null : normalizeId(value)
}

function normalizeTrade(value: unknown): DashboardTrade | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const id = normalizeId(raw.id)
  const tradeNumber = normalizeId(raw.trade_number)
  const commodityId = normalizeId(raw.commodity_id)
  const commodityName = normalizeText(raw.commodity_name, 120)
  const quantity = Number(raw.quantity)
  const price = Number(raw.price)
  const tradeType = raw.trade_type === 'buy' || raw.trade_type === 'sell' ? raw.trade_type : null
  const createdAt = normalizeText(raw.created_at, 120)
  if (
    id === null || tradeNumber === null || commodityId === null || !commodityName || !tradeType ||
    !Number.isSafeInteger(quantity) || quantity < 0 || !Number.isSafeInteger(price) || price < 0 ||
    !createdAt
  ) return null

  return {
    id,
    trade_number: tradeNumber,
    trade_type: tradeType,
    settlement_type: normalizeText(raw.settlement_type, 32),
    commodity_id: commodityId,
    commodity_name: commodityName,
    quantity,
    price,
    status: normalizeText(raw.status, 40),
    offer_user_id: normalizeNullableId(raw.offer_user_id),
    offer_user_name: normalizeText(raw.offer_user_name, 120) || null,
    responder_user_id: normalizeNullableId(raw.responder_user_id),
    responder_user_name: normalizeText(raw.responder_user_name, 120) || null,
    counterparty_name: normalizeText(raw.counterparty_name, 120) || null,
    offer_notes: normalizeText(raw.offer_notes, 500) || null,
    created_at: createdAt,
  }
}

function normalizeProjectUser(value: unknown): DashboardProjectUser | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const id = normalizeId(raw.id)
  const accountName = normalizeText(raw.account_name, 120)
  if (id === null || !accountName) return null
  return { id, account_name: accountName }
}

function normalizeCommodity(value: unknown): DashboardCommodity | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const id = normalizeId(raw.id)
  const name = normalizeText(raw.name, 120)
  if (id === null || !name) return null
  const aliases = Array.isArray(raw.aliases)
    ? raw.aliases
        .map((entry) => {
          if (typeof entry === 'string') return normalizeText(entry, 120)
          if (!entry || typeof entry !== 'object') return ''
          return normalizeText((entry as Record<string, unknown>).alias, 120)
        })
        .filter((alias) => alias && alias !== name)
    : []
  return { id, name, aliases: Array.from(new Set(aliases)) }
}

function formatNumber(value: number | string) {
  const number = Number(value)
  return Number.isFinite(number) ? number.toLocaleString('fa-IR') : '—'
}

function formatIdentifier(value: number | string) {
  const number = Number(value)
  return Number.isSafeInteger(number)
    ? number.toLocaleString('fa-IR', { useGrouping: false })
    : '—'
}

function formatTotal(trade: DashboardTrade) {
  try {
    return (BigInt(trade.quantity) * BigInt(trade.price)).toLocaleString('fa-IR')
  } catch {
    return '—'
  }
}

function todayInIran() {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: IRAN_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const read = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value || ''
  return `${read('year')}-${read('month')}-${read('day')}`
}

function tradeBelongsToPerspective(trade: DashboardTrade) {
  const perspective = perspectiveUserId.value
  return perspective !== null && (
    trade.offer_user_id === perspective || trade.responder_user_id === perspective
  )
}

function tradeTypeForPerspective(trade: DashboardTrade) {
  if (trade.responder_user_id === perspectiveUserId.value) return trade.trade_type
  return trade.trade_type === 'buy' ? 'sell' : 'buy'
}

function tradeTypeLabel(trade: DashboardTrade) {
  return tradeTypeForPerspective(trade) === 'buy' ? 'خرید' : 'فروش'
}

function tradeStatusLabel(status: string) {
  const labels: Record<string, string> = {
    completed: 'انجام‌شده',
    active: 'فعال',
    pending: 'در انتظار',
    cancelled: 'لغوشده',
    canceled: 'لغوشده',
    failed: 'ناموفق',
  }
  return labels[status.toLowerCase()] || 'ثبت‌شده'
}

function tradeStatusTone(status: string): BadgeTone {
  const normalized = status.toLowerCase()
  if (normalized === 'completed') return 'success'
  if (normalized === 'pending' || normalized === 'active') return 'warning'
  if (normalized === 'cancelled' || normalized === 'canceled' || normalized === 'failed') return 'danger'
  return 'neutral'
}

function tradeCreatedAtLabel(value: string) {
  // The trade API returns an already-formatted Jalali value. Parsing that as a
  // Gregorian date produces impossible years such as ۷۸۴ in the browser.
  const normalizedDigits = value.trim().replace(/[۰-۹]/g, (digit) => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit)))
  if (/^(?:13|14)\d{2}\/\d{2}\/\d{2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?$/.test(normalizedDigits)) {
    return value.trim()
  }
  const formatted = formatIranDateTime(value)
  return formatted || value
}

function counterpartyLabel(trade: DashboardTrade) {
  if (trade.counterparty_name) return trade.counterparty_name
  return trade.responder_user_id === perspectiveUserId.value
    ? trade.offer_user_name || 'نامشخص'
    : trade.responder_user_name || 'نامشخص'
}

async function loadTodayTrades(options: { silent?: boolean } = {}) {
  const silent = options.silent === true
  tradesController?.abort()
  const controller = new AbortController()
  tradesController = controller
  const generation = identityGeneration
  if (!silent) tradesLoading.value = true
  tradesError.value = false

  try {
    const today = todayInIran()
    const rows: unknown[] = []
    const seenCursors = new Set<string>()
    let cursor = ''

    do {
      const params = new URLSearchParams({
        from_date: today,
        to_date: today,
        limit: '100',
      })
      if (cursor) params.set('cursor', cursor)
      const response = await apiFetch(`/api/trades/my/page?${params}`, {
        signal: controller.signal,
        retryNetwork: false,
      })
      if (!response.ok) throw new Error('trade-request-failed')
      const payload = await response.json().catch(() => null)
      if (controller.signal.aborted || generation !== identityGeneration) return
      if (!payload || typeof payload !== 'object' || !Array.isArray(payload.items)) {
        throw new Error('trade-response-invalid')
      }
      rows.push(...payload.items)
      const nextCursor = normalizeText(payload.next_cursor, 768)
      if (payload.has_more !== true) {
        cursor = ''
      } else if (!nextCursor || seenCursors.has(nextCursor)) {
        throw new Error('trade-pagination-invalid')
      } else {
        seenCursors.add(nextCursor)
        cursor = nextCursor
      }
    } while (cursor)

    trades.value = rows
      .map((entry) => normalizeTrade(entry))
      .filter((entry): entry is DashboardTrade => entry !== null)
      .filter(tradeBelongsToPerspective)
  } catch {
    if (controller.signal.aborted || generation !== identityGeneration) return
    if (!silent) {
      trades.value = []
      tradesError.value = true
    }
  } finally {
    if (!controller.signal.aborted && generation === identityGeneration) tradesLoading.value = false
  }
}

function scheduleTodayTradesRefresh() {
  if (tradeRefreshTimer !== null) clearTimeout(tradeRefreshTimer)
  tradeRefreshTimer = setTimeout(() => {
    tradeRefreshTimer = null
    void loadTodayTrades({ silent: true })
  }, 80)
}

function handleTradeCreated(payload: unknown) {
  const incoming = normalizeTrade(payload)
  if (incoming && tradeBelongsToPerspective(incoming)) {
    const previous = trades.value.find(
      (trade) => trade.id === incoming.id || trade.trade_number === incoming.trade_number,
    )
    const nextTrade = previous
      ? { ...previous, ...incoming, offer_notes: incoming.offer_notes ?? previous.offer_notes }
      : incoming
    trades.value = [
      nextTrade,
      ...trades.value.filter(
        (trade) => trade.id !== incoming.id && trade.trade_number !== incoming.trade_number,
      ),
    ]
  }
  // The private event is only delivered to an authorized trade audience. An
  // authoritative refresh also covers manager/accountant projections whose
  // participant ids may differ from the current dashboard perspective.
  scheduleTodayTradesRefresh()
}

function handleAppNotification(payload: unknown) {
  if (!payload || typeof payload !== 'object') return
  const notification = payload as Record<string, unknown>
  const embedded = notification.extra_payload
  const embeddedCategory = embedded && typeof embedded === 'object'
    ? (embedded as Record<string, unknown>).category
    : null
  const category = String(notification.category ?? embeddedCategory ?? '').trim().toLowerCase()
  if (category === 'trade') scheduleTodayTradesRefresh()
}

function handleRealtimeReconnect() {
  scheduleTodayTradesRefresh()
}

wsOn(WS_NOTIFICATION_EVENTS.tradeCreated, handleTradeCreated)
wsOn(WS_NOTIFICATION_EVENTS.appMessage, handleAppNotification)
wsOn(WS_NOTIFICATION_EVENTS.wsReconnect, handleRealtimeReconnect)

function resetCoworkers() {
  coworkers.value = []
  coworkersLoaded.value = false
  coworkersError.value = false
  coworkersHasMore.value = false
  coworkersOffset.value = 0
  lastCoworkersQuery.value = ''
}

async function loadCoworkers({ reset = false } = {}) {
  const targetId = coworkersTargetId.value
  if (targetId === null || coworkersLoading.value || coworkersLoadingMore.value) return
  if (reset) resetCoworkers()
  const query = coworkersQuery.value.trim()
  const isLoadMore = coworkersOffset.value > 0 && !reset
  if (!isLoadMore && coworkersLoaded.value && lastCoworkersQuery.value === query) return

  coworkersController?.abort()
  const controller = new AbortController()
  coworkersController = controller
  const generation = identityGeneration
  if (isLoadMore) coworkersLoadingMore.value = true
  else coworkersLoading.value = true
  coworkersError.value = false

  try {
    const params = new URLSearchParams({
      limit: String(PROJECT_USERS_PAGE_SIZE),
      offset: String(isLoadMore ? coworkersOffset.value : 0),
    })
    if (query) params.set('q', query)
    const response = await apiFetch(`/api/users-public/${targetId}/project-users?${params}`, {
      signal: controller.signal,
      retryNetwork: false,
    })
    if (!response.ok) throw new Error('coworkers-request-failed')
    const payload = await response.json().catch(() => null)
    if (controller.signal.aborted || generation !== identityGeneration) return
    const rawRows = Array.isArray(payload) ? payload : []
    const rows = rawRows
      .map((entry) => normalizeProjectUser(entry))
      .filter((entry): entry is DashboardProjectUser => entry !== null && entry.id !== targetId)
    const existing = new Set(coworkers.value.map((entry) => entry.id))
    coworkers.value = isLoadMore
      ? [...coworkers.value, ...rows.filter((entry) => !existing.has(entry.id))]
      : rows
    coworkersOffset.value = (isLoadMore ? coworkersOffset.value : 0) + rawRows.length
    coworkersHasMore.value = rawRows.length === PROJECT_USERS_PAGE_SIZE
    coworkersLoaded.value = true
    lastCoworkersQuery.value = query
  } catch {
    if (controller.signal.aborted || generation !== identityGeneration) return
    coworkersError.value = true
    coworkersLoaded.value = true
  } finally {
    if (!controller.signal.aborted && generation === identityGeneration) {
      coworkersLoading.value = false
      coworkersLoadingMore.value = false
    }
  }
}

async function toggleCoworkers() {
  coworkersOpen.value = !coworkersOpen.value
  if (coworkersOpen.value && coworkersAvailable.value && !coworkersLoaded.value) {
    await loadCoworkers({ reset: true })
  }
}

async function searchCoworkers() {
  await loadCoworkers({ reset: true })
}

function openCoworkerProfile(coworker: DashboardProjectUser) {
  void router.push({
    name: 'public-profile',
    params: { id: coworker.id },
    query: { account_name: coworker.account_name },
  })
}

async function loadCommodities() {
  commoditiesController?.abort()
  const controller = new AbortController()
  commoditiesController = controller
  const generation = identityGeneration
  commoditiesLoading.value = true
  commoditiesError.value = false

  try {
    const response = await apiFetch('/api/commodities/', {
      signal: controller.signal,
      retryNetwork: false,
    })
    if (!response.ok) throw new Error('commodities-request-failed')
    const payload = await response.json().catch(() => null)
    if (controller.signal.aborted || generation !== identityGeneration) return
    commodities.value = Array.isArray(payload)
      ? payload
          .map((entry) => normalizeCommodity(entry))
          .filter((entry): entry is DashboardCommodity => entry !== null)
      : []
    commoditiesLoaded.value = true
  } catch {
    if (controller.signal.aborted || generation !== identityGeneration) return
    commodities.value = []
    commoditiesError.value = true
    commoditiesLoaded.value = true
  } finally {
    if (!controller.signal.aborted && generation === identityGeneration) commoditiesLoading.value = false
  }
}

async function toggleCommodities() {
  commoditiesOpen.value = !commoditiesOpen.value
  if (commoditiesOpen.value && !commoditiesLoaded.value) await loadCommodities()
}

function resetForIdentity() {
  identityGeneration += 1
  tradesController?.abort()
  coworkersController?.abort()
  commoditiesController?.abort()
  trades.value = []
  tradesLoading.value = false
  tradesError.value = false
  coworkersOpen.value = false
  coworkersQuery.value = ''
  resetCoworkers()
  coworkersLoading.value = false
  coworkersLoadingMore.value = false
  commoditiesOpen.value = false
  commodities.value = []
  commoditiesLoading.value = false
  commoditiesLoaded.value = false
  commoditiesError.value = false
  void loadTodayTrades()
}

watch(identityKey, resetForIdentity, { immediate: true })

onUnmounted(() => {
  identityGeneration += 1
  tradesController?.abort()
  coworkersController?.abort()
  commoditiesController?.abort()
  if (tradeRefreshTimer !== null) clearTimeout(tradeRefreshTimer)
  wsOff(WS_NOTIFICATION_EVENTS.tradeCreated, handleTradeCreated)
  wsOff(WS_NOTIFICATION_EVENTS.appMessage, handleAppNotification)
  wsOff(WS_NOTIFICATION_EVENTS.wsReconnect, handleRealtimeReconnect)
})
</script>

<template>
  <div class="dashboard-daily">
    <section class="dashboard-today-trades" aria-label="معاملات امروز کاربر">
      <div class="dashboard-today-trades__heading">
        <h2>معاملات امروز</h2>
        <AppButton
          type="button"
          size="sm"
          variant="secondary"
          class="dashboard-today-trades__refresh"
          :loading="tradesLoading"
          aria-label="به‌روزرسانی معاملات امروز"
          @click="loadTodayTrades()"
        >
          <RefreshCw :size="16" aria-hidden="true" />
          به‌روزرسانی
        </AppButton>
      </div>
      <AppInsetGroup>
      <div v-if="tradesLoading" class="dashboard-daily-state" role="status">
        در حال بارگذاری…
      </div>
      <AppEmptyState
        v-else-if="tradesError"
        title="معاملات امروز دریافت نشد"
        message="اتصال را بررسی کنید و دوباره تلاش کنید."
        tone="danger"
        role="alert"
      >
        <template #actions>
          <AppButton type="button" size="sm" @click="loadTodayTrades">تلاش دوباره</AppButton>
        </template>
      </AppEmptyState>
      <AppEmptyState
        v-else-if="trades.length === 0"
        title="امروز معامله‌ای ثبت نشده است"
        role="status"
      />
      <div v-else class="dashboard-trades">
        <div
          class="dashboard-trades__scroller"
          tabindex="0"
          role="region"
          :aria-label="tradeCountLabel"
        >
          <table class="dashboard-trades__table">
            <caption class="sr-only">معاملات امروز</caption>
            <thead>
              <tr>
                <th scope="col">شماره معامله</th>
                <th scope="col">طرف مقابل</th>
                <th scope="col">نوع</th>
                <th scope="col">وضعیت</th>
                <th scope="col">کالا</th>
                <th scope="col">تسویه</th>
                <th scope="col">مقدار</th>
                <th scope="col">قیمت واحد</th>
                <th scope="col">ارزش کل</th>
                <th scope="col">زمان ثبت</th>
                <th scope="col">توضیحات</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="trade in trades" :key="trade.id" class="dashboard-trade-row">
                <th scope="row">{{ formatIdentifier(trade.trade_number) }}</th>
                <td>{{ counterpartyLabel(trade) }}</td>
                <td>
                  <AppStatusBadge :tone="tradeTypeForPerspective(trade) === 'buy' ? 'success' : 'danger'">
                    {{ tradeTypeLabel(trade) }}
                  </AppStatusBadge>
                </td>
                <td>
                  <AppStatusBadge :tone="tradeStatusTone(trade.status)">
                    {{ tradeStatusLabel(trade.status) }}
                  </AppStatusBadge>
                </td>
                <td>{{ trade.commodity_name }}</td>
                <td>{{ tradeSettlementLabel(trade.settlement_type) }}</td>
                <td>{{ formatNumber(trade.quantity) }}</td>
                <td>{{ formatNumber(trade.price) }} تومان</td>
                <td>{{ formatTotal(trade) }} تومان</td>
                <td>{{ tradeCreatedAtLabel(trade.created_at) }}</td>
                <td>{{ trade.offer_notes || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      </AppInsetGroup>
    </section>

    <AppDisclosure
      class="dashboard-coworkers"
      title="لیست همکاران"
      :open="coworkersOpen"
      panel-class="dashboard-directory-panel"
      @toggle="toggleCoworkers"
    >
      <template #leading><UsersRound :size="21" /></template>
      <template #meta>
        <AppStatusBadge tone="info">{{ coworkersMetaLabel }}</AppStatusBadge>
        <ChevronDown
          :size="18"
          class="dashboard-disclosure-chevron"
          :class="{ 'is-open': coworkersOpen }"
          aria-hidden="true"
        />
      </template>

      <AppEmptyState
        v-if="!coworkersAvailable"
        title="فهرست همکاران برای این نوع حساب نمایش داده نمی‌شود"
        message="دسترسی حساب مشتری به فهرست کامل فضای کاری، مطابق حریم خصوصی محدود است."
        tone="info"
        role="status"
      />
      <template v-else>
        <form class="dashboard-directory-search" @submit.prevent="searchCoworkers">
          <label for="dashboard-coworker-query">جستجو در همکاران</label>
          <div>
            <AppInput
              id="dashboard-coworker-query"
              v-model="coworkersQuery"
              type="search"
              autocomplete="off"
              placeholder="نام کاربری همکار"
            />
            <AppButton type="submit" size="sm" :loading="coworkersLoading">جستجو</AppButton>
          </div>
        </form>
        <div v-if="coworkersLoading" class="dashboard-daily-state" role="status">
          در حال بارگذاری…
        </div>
        <AppEmptyState
          v-else-if="coworkersError"
          title="فهرست همکاران دریافت نشد"
          message="اتصال را بررسی کنید و دوباره تلاش کنید."
          tone="danger"
          role="alert"
        >
          <template #actions>
            <AppButton type="button" size="sm" @click="loadCoworkers({ reset: true })">
              تلاش دوباره
            </AppButton>
          </template>
        </AppEmptyState>
        <AppEmptyState
          v-else-if="coworkersLoaded && coworkers.length === 0"
          title="همکاری پیدا نشد"
          :message="coworkersQuery.trim() ? 'نتیجه‌ای برای این جستجو وجود ندارد.' : 'هنوز همکاری برای نمایش وجود ندارد.'"
          role="status"
        />
        <div v-else-if="coworkers.length" class="dashboard-coworker-list">
          <AppListItem
            v-for="coworker in coworkers"
            :key="coworker.id"
            :title="coworker.account_name"
            interactive
            @select="openCoworkerProfile(coworker)"
          />
          <AppButton
            v-if="coworkersHasMore"
            type="button"
            size="sm"
            variant="secondary"
            :loading="coworkersLoadingMore"
            @click="loadCoworkers()"
          >
            نمایش بیشتر
          </AppButton>
        </div>
      </template>
    </AppDisclosure>

    <AppDisclosure
      class="dashboard-commodities"
      title="کالاهای مجاز برای معامله"
      :open="commoditiesOpen"
      panel-class="dashboard-directory-panel"
      @toggle="toggleCommodities"
    >
      <template #leading><PackageCheck :size="21" /></template>
      <template #meta>
        <AppStatusBadge tone="info">{{ commoditiesMetaLabel }}</AppStatusBadge>
        <ChevronDown
          :size="18"
          class="dashboard-disclosure-chevron"
          :class="{ 'is-open': commoditiesOpen }"
          aria-hidden="true"
        />
      </template>

      <div v-if="commoditiesLoading" class="dashboard-daily-state" role="status">
        در حال بارگذاری…
      </div>
      <AppEmptyState
        v-else-if="commoditiesError"
        title="فهرست کالاها دریافت نشد"
        message="اتصال را بررسی کنید و دوباره تلاش کنید."
        tone="danger"
        role="alert"
      >
        <template #actions>
          <AppButton type="button" size="sm" @click="loadCommodities">تلاش دوباره</AppButton>
        </template>
      </AppEmptyState>
      <AppEmptyState
        v-else-if="commoditiesLoaded && commodities.length === 0"
        title="کالایی برای معامله ثبت نشده است"
        role="status"
      />
      <div v-else-if="commodities.length" class="dashboard-commodity-list">
        <AppListItem
          v-for="commodity in commodities"
          :key="commodity.id"
          class="dashboard-commodity"
          :title="commodity.name"
          :description="commodity.aliases.length ? commodity.aliases.join('، ') : undefined"
          :meta="commodity.aliases.length ? `${formatNumber(commodity.aliases.length)} نام مستعار` : undefined"
        />
      </div>
    </AppDisclosure>
  </div>
</template>

<style scoped>
.dashboard-daily {
  display: grid;
  gap: 0.75rem;
}

.dashboard-today-trades {
  min-width: 0;
}

.dashboard-today-trades__heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin: 0 1rem 0.4rem;
}

.dashboard-today-trades__heading h2 {
  margin: 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  font-weight: 650;
  line-height: 1.4;
}

.dashboard-today-trades__refresh {
  min-width: 0;
}

.dashboard-daily-state {
  min-height: var(--ds-native-row-min-height);
  display: grid;
  place-items: center;
  padding: 0.85rem 1rem;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.7;
}

.dashboard-trades {
  min-width: 0;
}

.dashboard-trades__scroller {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
  overscroll-behavior-inline: contain;
  scrollbar-gutter: stable;
}

.dashboard-trades__scroller:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 3px rgba(245, 158, 11, 0.28);
}

.dashboard-trades__table {
  width: max-content;
  min-width: 100%;
  border-collapse: collapse;
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
}

.dashboard-trades__table th,
.dashboard-trades__table td {
  padding: 0.7rem 0.85rem;
  border-block-end: 1px solid var(--ds-native-hairline);
  text-align: start;
  white-space: nowrap;
  vertical-align: middle;
}

.dashboard-trades__table thead th {
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
  font-weight: 650;
}

.dashboard-trades__table tbody th {
  color: var(--ds-text-primary);
  font-weight: 750;
}

.dashboard-trades__table tbody tr:last-child > * {
  border-block-end: 0;
}

.dashboard-disclosure-chevron {
  flex: none;
  transition: transform 0.18s ease;
}

.dashboard-disclosure-chevron.is-open {
  transform: rotate(180deg);
}

.dashboard-directory-search {
  display: grid;
  gap: 0.5rem;
  margin-block-end: 0.75rem;
}

.dashboard-directory-search label {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 700;
}

.dashboard-directory-search > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.5rem;
}

.dashboard-coworker-list,
.dashboard-commodity-list {
  display: grid;
  gap: 0;
  min-width: 0;
}

.dashboard-commodity {
  min-width: 0;
}

@media (max-width: 540px) {
  .dashboard-today-trades__heading {
    align-items: stretch;
    flex-direction: column;
    margin-inline: 0;
  }

  .dashboard-trades__table th,
  .dashboard-trades__table td {
    padding: 0.65rem 0.75rem;
  }

  .dashboard-directory-search > div {
    grid-template-columns: 1fr;
  }
}

@media (prefers-reduced-motion: reduce) {
  .dashboard-disclosure-chevron {
    transition-duration: 1ms;
  }
}
</style>
