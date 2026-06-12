<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { BarChart3, ChevronLeft, ReceiptText, ShieldCheck, SlidersHorizontal, UserPlus, Users } from 'lucide-vue-next'
import { apiFetch } from '../utils/auth'
import { formatIranDateTime, parseIranDisplayDate } from '../utils/iranTime'
import HelpPopover from './HelpPopover.vue'

const emit = defineEmits<{
  (e: 'close'): void
}>()

type RelationStatus = 'pending' | 'active' | 'expired' | 'revoked' | 'deleted' | string
type CustomerTier = 'tier1' | 'tier2'
type DetailSection = 'detailOverview' | 'detailTrades' | 'detailStats' | 'detailSessions' | 'detailDanger'

interface CustomerRelation {
  id: number
  owner_user_id: number
  customer_user_id: number | null
  customer_account_name: string | null
  invitation_account_name: string | null
  mobile_number: string | null
  management_name: string
  customer_tier: CustomerTier
  commission_rate: number | null
  min_trade_quantity: number | null
  max_trade_quantity: number | null
  max_daily_trades: number | null
  max_daily_commodity_volume: number | null
  status: RelationStatus
  invitation_token: string
  registration_link: string | null
  expires_at: string | null
  activated_at: string | null
  deleted_at: string | null
  created_at: string
}

interface CustomerSessionSummary {
  id: string
  device_name: string
  device_ip: string | null
  platform: string
  home_server: string
  is_primary: boolean
  is_active: boolean
  created_at: string | null
  last_active_at: string | null
}

interface CustomerSessionTerminateResponse {
  detail: string
  terminated_session_id: string
  promoted_primary_session_id: string | null
}

interface CustomerTradeSummary {
  id: number
  trade_number: number
  trade_type: string
  commodity_name: string
  quantity: number
  price: number
  status: string
  counterparty_name?: string | null
  created_at: string
}

interface CustomerTradeStatsCommodity {
  commodity_id: number
  commodity_name: string
  total_quantity: number
}

interface CustomerTradeStats {
  relation_id: number
  customer_user_id: number
  period_days: number
  from_date: string
  to_date: string
  trade_count: number
  total_quantity: number
  commission_profit_toman: number
  commodities: CustomerTradeStatsCommodity[]
  profit_calculation_note: string
}

function makeEmptyCreateForm() {
  return {
    management_name: '',
    mobile_number: '',
    customer_tier: 'tier1' as CustomerTier,
    commission_rate: '0.50',
    min_trade_quantity: '',
    max_trade_quantity: '',
    max_daily_trades: '',
    max_daily_commodity_volume: '',
  }
}

function makeEmptyDetailEditForm() {
  return {
    customer_tier: '',
    commission_rate: '',
    min_trade_quantity: '',
    max_trade_quantity: '',
    max_daily_trades: '',
    max_daily_commodity_volume: '',
  }
}

const relations = ref<CustomerRelation[]>([])
const isLoading = ref(true)
const isSubmitting = ref(false)
const isSavingEdit = ref(false)
const error = ref('')
const notice = ref('')
const copiedRelationId = ref<number | null>(null)
const openSessionsRelationId = ref<number | null>(null)
const sessionsByRelationId = ref<Record<number, CustomerSessionSummary[]>>({})
const tradesByRelationId = ref<Record<number, CustomerTradeSummary[]>>({})
const statsByRelationId = ref<Record<number, CustomerTradeStats>>({})
const loadingSessionsRelationId = ref<number | null>(null)
const loadingTradesRelationId = ref<number | null>(null)
const loadingStatsRelationId = ref<number | null>(null)
const terminatingSessionId = ref<string | null>(null)
const currentTimeMs = ref(Date.now())
const selectedRelationId = ref<number | null>(null)
const statsPeriodDays = ref(7)

const createForm = reactive(makeEmptyCreateForm())
const detailEditForm = reactive(makeEmptyDetailEditForm())
const openSections = reactive({
  create: true,
  createLimits: true,
  relations: true,
  detailOverview: true,
  detailTrades: false,
  detailStats: false,
  detailSessions: false,
  detailDanger: false,
})
let countdownTimer: number | null = null
let commissionHoldTimer: number | null = null

function parseApiError(payload: unknown, fallback: string) {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
  }
  return fallback
}

function resetCreateForm() {
  Object.assign(createForm, makeEmptyCreateForm())
}

function toggleSection(section: keyof typeof openSections) {
  openSections[section] = !openSections[section]
}

function clearDetailEditState() {
  Object.assign(detailEditForm, makeEmptyDetailEditForm())
}

function normalizeLatinDigits(value: string) {
  const persian = '۰۱۲۳۴۵۶۷۸۹'
  const arabic = '٠١٢٣٤٥٦٧٨٩'
  return String(value || '')
    .replace(/[۰-۹]/g, (digit) => String(persian.indexOf(digit)))
    .replace(/[٠-٩]/g, (digit) => String(arabic.indexOf(digit)))
}

const generatedCreateAccountName = computed(() => {
  const mobileDigits = normalizeLatinDigits(createForm.mobile_number).replace(/\D/g, '')
  return mobileDigits ? `customer_${mobileDigits}` : ''
})

const selectedRelation = computed(() => {
  if (selectedRelationId.value == null) return null
  return relations.value.find((relation) => relation.id === selectedRelationId.value) ?? null
})

const selectedRelationTrades = computed(() => {
  const relationId = selectedRelation.value?.id
  return relationId == null ? [] : tradesByRelationId.value[relationId] ?? []
})

const selectedRelationStats = computed(() => {
  const relationId = selectedRelation.value?.id
  return relationId == null ? null : statsByRelationId.value[relationId] ?? null
})

function formatDateTime(value: string | null) {
  if (!value) return '---'
  return formatIranDateTime(value) || value
}

function getRelationSessions(relationId: number) {
  return sessionsByRelationId.value[relationId] ?? []
}

function formatSessionPlatform(platform: string) {
  if (platform === 'telegram_mini_app') return 'تلگرام'
  if (platform === 'android') return 'اندروید'
  if (platform === 'web') return 'وب'
  return platform || 'نامشخص'
}

function formatHomeServer(homeServer: string) {
  if (homeServer === 'iran') return 'ایران'
  if (homeServer === 'foreign') return 'خارج'
  return homeServer || 'نامشخص'
}

function normalizeOptionalNumber(value: string | number | null | undefined) {
  if (value == null) return null
  const cleaned = String(value).trim()
  if (!cleaned) return null
  const normalized = Number(cleaned)
  return Number.isFinite(normalized) ? normalized : null
}

function buildCustomerPayload(form: {
  customer_tier: CustomerTier
  commission_rate: string | number
  min_trade_quantity: string | number
  max_trade_quantity: string | number
  max_daily_trades: string | number
  max_daily_commodity_volume: string | number
}) {
  return {
    customer_tier: form.customer_tier,
    commission_rate: form.customer_tier === 'tier2' ? normalizeOptionalNumber(form.commission_rate) : null,
    min_trade_quantity: normalizeOptionalNumber(form.min_trade_quantity),
    max_trade_quantity: normalizeOptionalNumber(form.max_trade_quantity),
    max_daily_trades: normalizeOptionalNumber(form.max_daily_trades),
    max_daily_commodity_volume: normalizeOptionalNumber(form.max_daily_commodity_volume),
  }
}

function getRemainingMs(value: string | null) {
  if (!value) return null
  const timestamp = parseIranDisplayDate(value)?.getTime() ?? Number.NaN
  if (Number.isNaN(timestamp)) return null
  return timestamp - currentTimeMs.value
}

function formatCountdown(value: string | null) {
  const remainingMs = getRemainingMs(value)
  if (remainingMs == null) return '---'
  if (remainingMs <= 0) return '00:00:00'

  const totalSeconds = Math.floor(remainingMs / 1000)
  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const clock = [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
  if (days > 0) {
    return `${days} روز و ${clock}`
  }
  return clock
}

function getRelationAccountName(relation: CustomerRelation) {
  return relation.customer_account_name || relation.invitation_account_name || 'unknown'
}

function getCustomerTierLabel(tier: CustomerTier) {
  return tier === 'tier2' ? 'سطح 2' : 'سطح 1'
}

function formatMaybeNumber(value: number | null, suffix = '') {
  if (value == null) return '---'
  return `${value}${suffix}`
}

function formatPlainNumber(value: number, fractionDigits = 2) {
  const fixed = value.toFixed(fractionDigits)
  return fixed.replace(/\.00$/, '').replace(/(\.\d)0$/, '$1')
}

function formatMoneyToman(value: number | null | undefined) {
  const amount = Number(value || 0)
  if (!Number.isFinite(amount) || amount <= 0) return '۰ تومان'
  if (amount >= 1_000_000) {
    return `${formatPlainNumber(amount / 1_000_000)} میلیون تومان`
  }
  if (amount >= 1_000) {
    return `${formatPlainNumber(amount / 1_000, 1)} هزار تومان`
  }
  return `${Math.round(amount).toLocaleString('fa-IR')} تومان`
}

function normalizeCommissionRate(value: string | number | null | undefined) {
  const normalized = Number(normalizeLatinDigits(String(value ?? '')).replace(',', '.'))
  if (!Number.isFinite(normalized)) return 0
  return Math.min(100, Math.max(0, normalized))
}

function formatCommissionRate(value: string | number | null | undefined) {
  return normalizeCommissionRate(value).toFixed(2)
}

function getCommissionPreviewText(value: string | number | null | undefined) {
  const amount = 100_000_000 * normalizeCommissionRate(value) / 100
  return `نرخ کمیسیون شما به ازای هر ۱۰۰ میلیون ${formatMoneyToman(amount)} می‌باشد.`
}

const createCommissionPreview = computed(() => getCommissionPreviewText(createForm.commission_rate))

function adjustCreateCommission(delta: number) {
  createForm.commission_rate = formatCommissionRate(normalizeCommissionRate(createForm.commission_rate) + delta)
}

function adjustDetailCommission(delta: number) {
  const baseValue = detailEditForm.commission_rate || selectedRelation.value?.commission_rate || '0.50'
  detailEditForm.commission_rate = formatCommissionRate(normalizeCommissionRate(baseValue) + delta)
}

function startCommissionHold(delta: number, target: 'create' | 'detail' = 'create') {
  stopCommissionHold()
  const adjust = target === 'detail' ? adjustDetailCommission : adjustCreateCommission
  adjust(delta)
  if (typeof window === 'undefined') return
  commissionHoldTimer = window.setInterval(() => adjust(delta), 160)
}

function stopCommissionHold() {
  if (commissionHoldTimer === null || typeof window === 'undefined') return
  window.clearInterval(commissionHoldTimer)
  commissionHoldTimer = null
}

function getLimitPlaceholder(value: number | null) {
  return value == null ? 'بدون محدودیت' : String(value)
}

function getCommissionPlaceholder(relation: CustomerRelation) {
  return relation.commission_rate == null ? '0.50' : String(relation.commission_rate)
}

function getRelationStateText(relation: CustomerRelation) {
  if (relation.status === 'pending') {
    const remainingMs = getRemainingMs(relation.expires_at)
    if (remainingMs == null) return 'دعوت ثبت شده و در انتظار ثبت نام مشتری است.'
    if (remainingMs <= 0) return 'مهلت این دعوت تمام شده و در انتظار همگام سازی وضعیت است.'
    return `مهلت ثبت نام: ${formatCountdown(relation.expires_at)}`
  }

  if (relation.status === 'active') {
    return `این مشتری با @${getRelationAccountName(relation)} در ${getCustomerTierLabel(relation.customer_tier)} فعال است.`
  }

  if (relation.status === 'expired') {
    return 'مهلت این دعوت به پایان رسیده است.'
  }

  if (relation.status === 'revoked') {
    return 'این دعوت توسط مالک لغو شده است.'
  }

  if (relation.status === 'deleted') {
    return 'این رابطه حذف شده است.'
  }

  return ''
}

function refreshCurrentTime() {
  currentTimeMs.value = Date.now()
}

function startCountdownTimer() {
  if (countdownTimer !== null || typeof window === 'undefined') return
  countdownTimer = window.setInterval(() => {
    refreshCurrentTime()
  }, 1000)
}

function stopCountdownTimer() {
  if (countdownTimer === null || typeof window === 'undefined') return
  window.clearInterval(countdownTimer)
  countdownTimer = null
}

function statusLabel(status: RelationStatus) {
  return {
    pending: 'در انتظار ثبت‌نام',
    active: 'فعال',
    expired: 'منقضی‌شده',
    revoked: 'لغوشده',
    deleted: 'حذف‌شده',
  }[status] || status
}

const orderedRelations = computed(() => {
  const weight = (status: RelationStatus) => {
    if (status === 'pending') return 0
    if (status === 'active') return 1
    return 2
  }
  return [...relations.value].sort((left, right) => {
    const statusDiff = weight(left.status) - weight(right.status)
    if (statusDiff !== 0) return statusDiff
    return String(right.created_at).localeCompare(String(left.created_at))
  })
})

async function loadRelations() {
  isLoading.value = true
  error.value = ''

  try {
    const response = await apiFetch('/api/customers/owner-relations')
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت لیست مشتریان ناموفق بود.'))
    }
    relations.value = Array.isArray(payload) ? payload : []
    if (openSessionsRelationId.value !== null) {
      const openRelation = relations.value.find((relation) => relation.id === openSessionsRelationId.value)
      if (!openRelation || openRelation.status !== 'active' || !openRelation.customer_user_id) {
        openSessionsRelationId.value = null
      }
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت لیست مشتریان ناموفق بود.'
  } finally {
    isLoading.value = false
  }
}

async function loadSessionsForRelation(relationId: number) {
  loadingSessionsRelationId.value = relationId
  error.value = ''

  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relationId}/sessions`, {
      method: 'GET',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت نشست‌های مشتری ناموفق بود.'))
    }
    sessionsByRelationId.value = {
      ...sessionsByRelationId.value,
      [relationId]: Array.isArray(payload) ? (payload as CustomerSessionSummary[]) : [],
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت نشست‌های مشتری ناموفق بود.'
  } finally {
    if (loadingSessionsRelationId.value === relationId) {
      loadingSessionsRelationId.value = null
    }
  }
}

async function toggleSessionPanel(relation: CustomerRelation) {
  if (relation.status !== 'active' || !relation.customer_user_id) return
  if (openSessionsRelationId.value === relation.id) {
    openSessionsRelationId.value = null
    return
  }
  openSessionsRelationId.value = relation.id
  await loadSessionsForRelation(relation.id)
}

async function terminateCustomerSession(relation: CustomerRelation, session: CustomerSessionSummary) {
  if (terminatingSessionId.value === session.id) return
  if (!window.confirm(`نشست «${session.device_name || 'دستگاه مشتری'}» پایان یابد؟`)) return

  terminatingSessionId.value = session.id
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relation.id}/sessions/${session.id}`, {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'پایان دادن نشست مشتری ناموفق بود.'))
    }
    const result = payload as CustomerSessionTerminateResponse | null
    notice.value = result?.detail || 'نشست مشتری با موفقیت پایان یافت.'
    await loadSessionsForRelation(relation.id)
  } catch (err: any) {
    error.value = err?.message || 'پایان دادن نشست مشتری ناموفق بود.'
  } finally {
    if (terminatingSessionId.value === session.id) {
      terminatingSessionId.value = null
    }
  }
}

function handleCreateTierChange() {
  if (createForm.customer_tier === 'tier2') {
    createForm.commission_rate = createForm.commission_rate || '0.50'
  } else {
    createForm.commission_rate = '0.50'
  }
}

function buildDetailUpdatePayload(relation: CustomerRelation) {
  const payload: Record<string, string | number | null> = {}
  const requestedTier = detailEditForm.customer_tier as CustomerTier | ''
  const nextTier = requestedTier || relation.customer_tier
  if (requestedTier && requestedTier !== relation.customer_tier) {
    payload.customer_tier = requestedTier
  }

  const commissionInput = String(detailEditForm.commission_rate || '').trim()
  if (nextTier === 'tier2' && commissionInput) {
    payload.commission_rate = normalizeCommissionRate(commissionInput)
  } else if (requestedTier === 'tier1') {
    payload.commission_rate = null
  }

  const numericFields = [
    'min_trade_quantity',
    'max_trade_quantity',
    'max_daily_trades',
    'max_daily_commodity_volume',
  ] as const
  for (const field of numericFields) {
    const rawValue = String(detailEditForm[field] || '').trim()
    if (rawValue) {
      payload[field] = normalizeOptionalNumber(rawValue)
    }
  }

  return payload
}

async function createRelation() {
  if (isSubmitting.value) return
  isSubmitting.value = true
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch('/api/customers/owner-relations', {
      method: 'POST',
      body: JSON.stringify({
        account_name: generatedCreateAccountName.value,
        management_name: createForm.management_name,
        mobile_number: createForm.mobile_number,
        ...buildCustomerPayload(createForm),
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'ایجاد مشتری ناموفق بود.'))
    }
    const created = payload as CustomerRelation
    relations.value = [created, ...relations.value.filter((item) => item.id !== created.id)]
    resetCreateForm()
    notice.value = 'دعوت مشتری ثبت شد.'
    openSections.relations = true
  } catch (err: any) {
    error.value = err?.message || 'ایجاد مشتری ناموفق بود.'
  } finally {
    isSubmitting.value = false
  }
}

async function saveDetailEdit() {
  const relation = selectedRelation.value
  if (!relation || isSavingEdit.value) return
  const payload = buildDetailUpdatePayload(relation)
  if (!Object.keys(payload).length) {
    notice.value = 'تغییری برای ذخیره انتخاب نشده است.'
    return
  }

  isSavingEdit.value = true
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relation.id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    const responsePayload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(responsePayload, 'ویرایش مشتری ناموفق بود.'))
    }
    const updated = responsePayload as CustomerRelation
    relations.value = relations.value.map((item) => (item.id === updated.id ? updated : item))
    clearDetailEditState()
    notice.value = 'اطلاعات مشتری به‌روزرسانی شد.'
  } catch (err: any) {
    error.value = err?.message || 'ویرایش مشتری ناموفق بود.'
  } finally {
    isSavingEdit.value = false
  }
}

async function unlinkRelation(relation: CustomerRelation) {
  const isPending = relation.status === 'pending'
  const isActive = relation.status === 'active'
  if (!isPending && !isActive) return

  const promptMessage = isPending
    ? `دعوت ${relation.management_name} لغو شود؟`
    : `ارتباط مشتری ${relation.management_name} قطع شود؟ این عملیات دسترسی مشتری را کامل غیرفعال می‌کند.`
  if (!window.confirm(promptMessage)) return

  error.value = ''
  notice.value = ''
  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relation.id}`, {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, isPending ? 'لغو دعوت مشتری ناموفق بود.' : 'قطع ارتباط مشتری ناموفق بود.'))
    }
    relations.value = relations.value.filter((item) => item.id !== relation.id)
    if (openSessionsRelationId.value === relation.id) {
      openSessionsRelationId.value = null
    }
    notice.value = isPending ? 'دعوت مشتری لغو شد.' : 'ارتباط مشتری قطع شد و دسترسی او غیرفعال گردید.'
  } catch (err: any) {
    error.value = err?.message || (isPending ? 'لغو دعوت مشتری ناموفق بود.' : 'قطع ارتباط مشتری ناموفق بود.')
  }
}

async function copyRegistrationLink(relation: CustomerRelation) {
  if (!relation.registration_link) return
  try {
    await navigator.clipboard.writeText(relation.registration_link)
    copiedRelationId.value = relation.id
    window.setTimeout(() => {
      if (copiedRelationId.value === relation.id) {
        copiedRelationId.value = null
      }
    }, 1800)
  } catch {
    error.value = 'کپی لینک ثبت‌نام ممکن نشد.'
  }
}

function openCustomerDetail(relation: CustomerRelation) {
  selectedRelationId.value = relation.id
  clearDetailEditState()
  error.value = ''
  notice.value = ''
  openSections.detailOverview = true
  openSections.detailTrades = false
  openSections.detailStats = false
  openSections.detailSessions = false
  openSections.detailDanger = false
}

function backToCustomerList() {
  selectedRelationId.value = null
  clearDetailEditState()
}

async function loadCustomerTrades(relationId: number, options?: { force?: boolean }) {
  const relation = relations.value.find((item) => item.id === relationId)
  if (!relation?.customer_user_id) return
  if (!options?.force && tradesByRelationId.value[relationId]) return
  loadingTradesRelationId.value = relationId
  error.value = ''
  try {
    const response = await apiFetch(`/api/trades/with/${relation.customer_user_id}?limit=20`)
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت معاملات مشتری ناموفق بود.'))
    }
    tradesByRelationId.value = {
      ...tradesByRelationId.value,
      [relationId]: Array.isArray(payload) ? (payload as CustomerTradeSummary[]) : [],
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت معاملات مشتری ناموفق بود.'
  } finally {
    if (loadingTradesRelationId.value === relationId) {
      loadingTradesRelationId.value = null
    }
  }
}

async function loadCustomerStats(relationId: number, options?: { force?: boolean }) {
  const relation = relations.value.find((item) => item.id === relationId)
  if (!relation?.customer_user_id) return
  if (!options?.force && statsByRelationId.value[relationId]?.period_days === statsPeriodDays.value) return
  loadingStatsRelationId.value = relationId
  error.value = ''
  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relationId}/trade-stats?days=${statsPeriodDays.value}`)
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت آمار مشتری ناموفق بود.'))
    }
    statsByRelationId.value = {
      ...statsByRelationId.value,
      [relationId]: payload as CustomerTradeStats,
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت آمار مشتری ناموفق بود.'
  } finally {
    if (loadingStatsRelationId.value === relationId) {
      loadingStatsRelationId.value = null
    }
  }
}

async function toggleDetailSection(section: DetailSection) {
  openSections[section] = !openSections[section]
  const relation = selectedRelation.value
  if (!relation || !openSections[section]) return
  if (section === 'detailTrades') {
    await loadCustomerTrades(relation.id)
  } else if (section === 'detailStats') {
    await loadCustomerStats(relation.id)
  } else if (section === 'detailSessions' && relation.status === 'active' && relation.customer_user_id) {
    openSessionsRelationId.value = relation.id
    await loadSessionsForRelation(relation.id)
  }
}

async function setStatsPeriod(days: number) {
  statsPeriodDays.value = days
  const relation = selectedRelation.value
  if (relation) {
    await loadCustomerStats(relation.id, { force: true })
  }
}

function formatTradeType(type: string) {
  if (type === 'buy') return 'خرید'
  if (type === 'sell') return 'فروش'
  return type || 'نامشخص'
}

function formatTradeStatus(status: string) {
  if (status === 'completed') return 'تکمیل‌شده'
  if (status === 'pending') return 'در انتظار'
  if (status === 'cancelled') return 'لغوشده'
  if (status === 'confirmed') return 'تاییدشده'
  return status || 'نامشخص'
}

onMounted(() => {
  startCountdownTimer()
  void loadRelations()
})

onBeforeUnmount(() => {
  stopCountdownTimer()
  stopCommissionHold()
})
</script>

<template>
  <Teleport to="body">
    <div class="customer-manager-backdrop" @click.self="emit('close')">
      <div class="customer-manager-shell">
        <div class="customer-manager-header">
          <button type="button" class="customer-manager-back" aria-label="بازگشت" @click="emit('close')">
            <ChevronLeft :size="24" />
          </button>
          <div class="customer-manager-title">
            <p class="customer-manager-kicker">مدیریت ارتباطات</p>
            <h3>مشتریان مالک</h3>
          </div>
          <span class="customer-manager-header-spacer" aria-hidden="true"></span>
        </div>

        <div v-if="notice" class="customer-banner success">{{ notice }}</div>
        <div v-if="error" class="customer-banner error">{{ error }}</div>

        <section class="customer-panel customer-panel--accordion">
          <div class="ds-accordion" :class="{ open: openSections.create }">
            <div class="ds-accordion-header customer-main-menu-header" @click="toggleSection('create')">
              <div class="ds-accordion-header-info customer-menu-title">
                <UserPlus :size="18" class="customer-section-icon" />
                <h4>افزودن مشتری جدید</h4>
              </div>
              <div class="accordion-header-actions">
                <span class="customer-menu-note">دعوت و تنظیمات اولیه</span>
                <HelpPopover
                  button-test="customer-create-help"
                  note-test="customer-create-help-note"
                  label="راهنمای افزودن مشتری"
                  text="دعوت مشتری از همین پنل ثبت می‌شود و در صورت نیاز می‌توانید لینک ثبت‌نام را کپی کنید."
                />
                <ChevronLeft :size="20" class="ds-accordion-icon" />
              </div>
            </div>
            <div v-show="openSections.create" class="ds-accordion-body customer-accordion-body">
              <div class="customer-form-sections customer-form-sections--stacked">
                <section class="form-subpanel">
                  <div class="form-subpanel-head">
                    <h5>مشخصات مشتری</h5>
                    <p>نام مشتری، شماره موبایل و سطح دسترسی را وارد کنید.</p>
                  </div>
                  <div class="customer-form-grid create-main-grid">
                    <label class="field-block">
                      <span>نام مشتری</span>
                      <input v-model.trim="createForm.management_name" class="customer-input create-management-name" type="text" placeholder="مثلاً علی رضایی" />
                    </label>
                    <label class="field-block">
                      <span>شماره موبایل</span>
                      <input v-model.trim="createForm.mobile_number" class="customer-input create-mobile-number" type="tel" inputmode="numeric" placeholder="0912xxxxxxx" />
                    </label>
                    <label class="field-block">
                      <span>سطح مشتری</span>
                      <select v-model="createForm.customer_tier" class="customer-input create-tier-select" @change="handleCreateTierChange">
                        <option value="tier1">سطح 1</option>
                        <option value="tier2">سطح 2</option>
                      </select>
                    </label>
                  </div>

                  <div v-if="createForm.customer_tier === 'tier2'" class="commission-panel">
                    <label class="field-block commission-field">
                      <span>درصد کمیسیون</span>
                      <div class="commission-input-shell">
                        <button
                          type="button"
                          class="commission-step-btn"
                          @pointerdown.prevent="startCommissionHold(-0.01)"
                          @pointerup="stopCommissionHold"
                          @pointerleave="stopCommissionHold"
                          @pointercancel="stopCommissionHold"
                          @click.prevent
                        >
                          -
                        </button>
                        <input
                          v-model.trim="createForm.commission_rate"
                          class="customer-input create-commission-rate commission-input"
                          type="number"
                          min="0"
                          max="100"
                          step="0.01"
                          inputmode="decimal"
                          placeholder="0.50"
                        />
                        <button
                          type="button"
                          class="commission-step-btn"
                          @pointerdown.prevent="startCommissionHold(0.01)"
                          @pointerup="stopCommissionHold"
                          @pointerleave="stopCommissionHold"
                          @pointercancel="stopCommissionHold"
                          @click.prevent
                        >
                          +
                        </button>
                      </div>
                    </label>
                    <p class="commission-preview">{{ createCommissionPreview }}</p>
                  </div>
                </section>

                <section class="form-subpanel form-subpanel--accordion">
                  <div class="ds-accordion" :class="{ open: openSections.createLimits }">
                    <div class="ds-accordion-header" @click.stop="toggleSection('createLimits')">
                      <div class="ds-accordion-header-info">
                        <SlidersHorizontal :size="16" class="customer-subsection-icon" />
                        <div>
                          <h5>محدودیت‌های معاملاتی</h5>
                          <p>همه فیلدهای این بخش اختیاری هستند</p>
                        </div>
                      </div>
                      <ChevronLeft :size="18" class="ds-accordion-icon" />
                    </div>
                    <div v-show="openSections.createLimits" class="ds-accordion-body">
                      <div class="customer-form-grid">
                        <label class="field-block">
                          <span>حداقل مقدار معامله</span>
                          <input v-model.trim="createForm.min_trade_quantity" class="customer-input create-min-trade" type="number" min="0" step="1" placeholder="اختیاری" />
                          <small>کمترین تعداد قابل معامله برای این مشتری در هر معامله.</small>
                        </label>
                        <label class="field-block">
                          <span>حداکثر مقدار معامله</span>
                          <input v-model.trim="createForm.max_trade_quantity" class="customer-input create-max-trade" type="number" min="0" step="1" placeholder="اختیاری" />
                          <small>بیشترین تعداد مجاز در هر معامله تکی.</small>
                        </label>
                        <label class="field-block">
                          <span>سقف معاملات روزانه</span>
                          <input v-model.trim="createForm.max_daily_trades" class="customer-input create-max-daily-trades" type="number" min="0" step="1" placeholder="اختیاری" />
                          <small>حداکثر تعداد معامله‌ای که مشتری در یک روز می‌تواند انجام دهد.</small>
                        </label>
                        <label class="field-block">
                          <span>سقف حجم روزانه</span>
                          <input v-model.trim="createForm.max_daily_commodity_volume" class="customer-input create-max-daily-volume" type="number" min="0" step="1" placeholder="اختیاری" />
                          <small>حداکثر مجموع تعداد کالا در معاملات روزانه مشتری.</small>
                        </label>
                      </div>
                    </div>
                  </div>
                </section>
              </div>

              <div class="panel-actions">
                <button type="button" class="secondary-btn" :disabled="isSubmitting" @click="resetCreateForm">پاک کردن فرم</button>
                <button type="button" class="primary-btn submit-create" :disabled="isSubmitting" @click="createRelation">
                  {{ isSubmitting ? 'در حال ثبت...' : 'ثبت مشتری' }}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section class="customer-panel customer-panel--accordion">
          <div class="ds-accordion" :class="{ open: openSections.relations }">
            <div class="ds-accordion-header customer-main-menu-header" @click="toggleSection('relations')">
              <div class="ds-accordion-header-info customer-menu-title">
                <Users :size="18" class="customer-section-icon" />
                <h4>مدیریت مشتریان</h4>
              </div>
              <div class="accordion-header-actions">
                <span class="customer-menu-note">لیست، ویرایش، آمار</span>
                <HelpPopover
                  button-test="customer-list-help"
                  note-test="customer-list-help-note"
                  label="راهنمای لیست مشتریان"
                  text="برای مشتری فعال می‌توانید سطح و محدودیت‌ها را به‌روزرسانی یا ارتباط را قطع کنید."
                />
                <ChevronLeft :size="20" class="ds-accordion-icon" />
              </div>
            </div>
            <div v-show="openSections.relations" class="ds-accordion-body customer-accordion-body">
              <div v-if="selectedRelation" class="customer-detail-page">
                <div class="customer-detail-topbar">
                  <button type="button" class="ghost-btn ghost-btn--inline" @click="backToCustomerList">بازگشت به لیست</button>
                  <div>
                    <h4>{{ selectedRelation.management_name }}</h4>
                    <p>@{{ getRelationAccountName(selectedRelation) }}</p>
                  </div>
                </div>

                <div class="detail-accordion ds-accordion" :class="{ open: openSections.detailOverview }">
                  <div class="ds-accordion-header" @click="toggleDetailSection('detailOverview')">
                    <div class="ds-accordion-header-info">
                      <SlidersHorizontal :size="18" class="customer-section-icon" />
                      <div>
                        <h4>مشخصات و محدودیت‌ها</h4>
                        <p>سطح، کمیسیون و محدودیت‌های فعلی مشتری</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="ds-accordion-icon" />
                  </div>
                  <div v-show="openSections.detailOverview" class="ds-accordion-body customer-accordion-body">
                    <div class="customer-meta-grid detail-metric-grid">
                      <div class="meta-item metric-card">
                        <span class="meta-label">سطح مشتری</span>
                        <span class="meta-value">{{ getCustomerTierLabel(selectedRelation.customer_tier) }}</span>
                      </div>
                      <div class="meta-item metric-card">
                        <span class="meta-label">کمیسیون</span>
                        <span class="meta-value">{{ formatMaybeNumber(selectedRelation.commission_rate, '%') }}</span>
                      </div>
                      <div class="meta-item metric-card">
                        <span class="meta-label">حداقل معامله</span>
                        <span class="meta-value">{{ formatMaybeNumber(selectedRelation.min_trade_quantity) }}</span>
                      </div>
                      <div class="meta-item metric-card">
                        <span class="meta-label">حداکثر معامله</span>
                        <span class="meta-value">{{ formatMaybeNumber(selectedRelation.max_trade_quantity) }}</span>
                      </div>
                      <div class="meta-item metric-card">
                        <span class="meta-label">سقف معاملات روزانه</span>
                        <span class="meta-value">{{ formatMaybeNumber(selectedRelation.max_daily_trades) }}</span>
                      </div>
                      <div class="meta-item metric-card">
                        <span class="meta-label">سقف حجم روزانه</span>
                        <span class="meta-value">{{ formatMaybeNumber(selectedRelation.max_daily_commodity_volume) }}</span>
                      </div>
                    </div>

                    <div class="form-subpanel">
                      <div class="form-subpanel-head">
                        <h5>ویرایش سریع</h5>
                        <p>مقادیر فعلی به صورت placeholder نمایش داده شده‌اند؛ فقط فیلدهای تغییر یافته ذخیره می‌شوند.</p>
                      </div>
                      <div class="customer-form-grid compact-grid">
                        <label class="field-block">
                          <span>سطح مشتری</span>
                          <select v-model="detailEditForm.customer_tier" class="customer-input edit-tier-select">
                            <option value="">بدون تغییر ({{ getCustomerTierLabel(selectedRelation.customer_tier) }})</option>
                            <option value="tier1">سطح 1</option>
                            <option value="tier2">سطح 2</option>
                          </select>
                        </label>
                        <label v-if="(detailEditForm.customer_tier || selectedRelation.customer_tier) === 'tier2'" class="field-block">
                          <span>درصد کمیسیون</span>
                          <div class="commission-input-shell">
                            <button
                              type="button"
                              class="commission-step-btn"
                              @pointerdown.prevent="startCommissionHold(-0.01, 'detail')"
                              @pointerup="stopCommissionHold"
                              @pointerleave="stopCommissionHold"
                              @pointercancel="stopCommissionHold"
                              @click.prevent
                            >
                              -
                            </button>
                            <input
                              v-model.trim="detailEditForm.commission_rate"
                              class="customer-input edit-commission-rate commission-input"
                              type="number"
                              min="0"
                              max="100"
                              step="0.01"
                              inputmode="decimal"
                              :placeholder="getCommissionPlaceholder(selectedRelation)"
                            />
                            <button
                              type="button"
                              class="commission-step-btn"
                              @pointerdown.prevent="startCommissionHold(0.01, 'detail')"
                              @pointerup="stopCommissionHold"
                              @pointerleave="stopCommissionHold"
                              @pointercancel="stopCommissionHold"
                              @click.prevent
                            >
                              +
                            </button>
                          </div>
                          <small class="commission-preview compact">{{ getCommissionPreviewText(detailEditForm.commission_rate || selectedRelation.commission_rate || '0.50') }}</small>
                        </label>
                        <label class="field-block">
                          <span>حداقل مقدار معامله</span>
                          <input v-model.trim="detailEditForm.min_trade_quantity" class="customer-input edit-min-trade" type="number" min="0" step="1" :placeholder="getLimitPlaceholder(selectedRelation.min_trade_quantity)" />
                        </label>
                        <label class="field-block">
                          <span>حداکثر مقدار معامله</span>
                          <input v-model.trim="detailEditForm.max_trade_quantity" class="customer-input edit-max-trade" type="number" min="0" step="1" :placeholder="getLimitPlaceholder(selectedRelation.max_trade_quantity)" />
                        </label>
                        <label class="field-block">
                          <span>سقف معاملات روزانه</span>
                          <input v-model.trim="detailEditForm.max_daily_trades" class="customer-input edit-max-daily-trades" type="number" min="0" step="1" :placeholder="getLimitPlaceholder(selectedRelation.max_daily_trades)" />
                        </label>
                        <label class="field-block">
                          <span>سقف حجم روزانه</span>
                          <input v-model.trim="detailEditForm.max_daily_commodity_volume" class="customer-input edit-max-daily-volume" type="number" min="0" step="1" :placeholder="getLimitPlaceholder(selectedRelation.max_daily_commodity_volume)" />
                        </label>
                      </div>
                      <div class="panel-actions compact">
                        <button type="button" class="secondary-btn" :disabled="isSavingEdit" @click="clearDetailEditState">پاک کردن تغییرات</button>
                        <button type="button" class="primary-btn save-edit" :disabled="isSavingEdit" @click="saveDetailEdit">
                          {{ isSavingEdit ? 'در حال ذخیره...' : 'ذخیره تغییرات' }}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>

                <div class="detail-accordion ds-accordion" :class="{ open: openSections.detailTrades }">
                  <div class="ds-accordion-header" @click="toggleDetailSection('detailTrades')">
                    <div class="ds-accordion-header-info">
                      <ReceiptText :size="18" class="customer-section-icon" />
                      <div>
                        <h4>معاملات</h4>
                        <p>آخرین معاملات مشتری، مشابه تاریخچه معاملات</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="ds-accordion-icon" />
                  </div>
                  <div v-show="openSections.detailTrades" class="ds-accordion-body customer-accordion-body">
                    <div v-if="!selectedRelation.customer_user_id" class="customer-empty">این دعوت هنوز به کاربر فعال وصل نشده است.</div>
                    <div v-else-if="loadingTradesRelationId === selectedRelation.id" class="customer-loading">در حال دریافت معاملات...</div>
                    <div v-else-if="selectedRelationTrades.length === 0" class="customer-empty">معامله‌ای برای این مشتری ثبت نشده است.</div>
                    <div v-else class="trade-list">
                      <article v-for="trade in selectedRelationTrades" :key="trade.id" class="trade-row">
                        <div>
                          <strong>#{{ trade.trade_number }} - {{ trade.commodity_name }}</strong>
                          <p>{{ formatTradeType(trade.trade_type) }} · {{ formatTradeStatus(trade.status) }} · {{ trade.created_at }}</p>
                        </div>
                        <div class="trade-row-values">
                          <span>{{ trade.quantity.toLocaleString('fa-IR') }} عدد</span>
                          <span>{{ formatMoneyToman(trade.price) }}</span>
                        </div>
                      </article>
                    </div>
                    <div class="panel-actions compact">
                      <button type="button" class="ghost-btn refresh-trades" :disabled="loadingTradesRelationId === selectedRelation.id" @click="loadCustomerTrades(selectedRelation.id, { force: true })">
                        نوسازی معاملات
                      </button>
                    </div>
                  </div>
                </div>

                <div class="detail-accordion ds-accordion" :class="{ open: openSections.detailStats }">
                  <div class="ds-accordion-header" @click="toggleDetailSection('detailStats')">
                    <div class="ds-accordion-header-info">
                      <BarChart3 :size="18" class="customer-section-icon" />
                      <div>
                        <h4>آمار</h4>
                        <p>تعداد معاملات، حجم کالا و سود کمیسیون در بازه انتخابی</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="ds-accordion-icon" />
                  </div>
                  <div v-show="openSections.detailStats" class="ds-accordion-body customer-accordion-body">
                    <div class="stats-periods">
                      <button v-for="days in [1, 3, 7, 30, 90, 180]" :key="days" type="button" class="history-chip" :class="{ active: statsPeriodDays === days }" @click="setStatsPeriod(days)">
                        {{ days === 1 ? '۱ روز' : days === 3 ? '۳ روز' : days === 7 ? '۱ هفته' : days === 30 ? '۱ ماه' : days === 90 ? '۳ ماه' : '۶ ماه' }}
                      </button>
                    </div>
                    <div v-if="!selectedRelation.customer_user_id" class="customer-empty">این دعوت هنوز به کاربر فعال وصل نشده است.</div>
                    <div v-else-if="loadingStatsRelationId === selectedRelation.id" class="customer-loading">در حال محاسبه آمار...</div>
                    <div v-else-if="!selectedRelationStats" class="customer-empty">برای مشاهده آمار، یک بازه را انتخاب کنید.</div>
                    <div v-else class="stats-report">
                      <div class="customer-meta-grid detail-metric-grid">
                        <div class="meta-item metric-card">
                          <span class="meta-label">تعداد معاملات</span>
                          <span class="meta-value">{{ selectedRelationStats.trade_count.toLocaleString('fa-IR') }}</span>
                        </div>
                        <div class="meta-item metric-card">
                          <span class="meta-label">مجموع تعداد کالا</span>
                          <span class="meta-value">{{ selectedRelationStats.total_quantity.toLocaleString('fa-IR') }}</span>
                        </div>
                        <div class="meta-item metric-card profit-card">
                          <span class="meta-label">سود کمیسیون</span>
                          <span class="meta-value">{{ formatMoneyToman(selectedRelationStats.commission_profit_toman) }}</span>
                        </div>
                      </div>
                      <div class="commodity-breakdown">
                        <h5>جزئیات کالاها</h5>
                        <p v-if="selectedRelationStats.commodities.length === 0">در این بازه معامله‌ای ثبت نشده است.</p>
                        <div v-for="commodity in selectedRelationStats.commodities" :key="commodity.commodity_id" class="commodity-row">
                          <span>{{ commodity.commodity_name }}</span>
                          <strong>{{ commodity.total_quantity.toLocaleString('fa-IR') }} عدد</strong>
                        </div>
                      </div>
                      <p class="stats-note">{{ selectedRelationStats.profit_calculation_note }}</p>
                    </div>
                  </div>
                </div>

                <div class="detail-accordion ds-accordion" :class="{ open: openSections.detailSessions }">
                  <div class="ds-accordion-header" @click="toggleDetailSection('detailSessions')">
                    <div class="ds-accordion-header-info">
                      <ShieldCheck :size="18" class="customer-section-icon" />
                      <div>
                        <h4>نشست مشتری</h4>
                        <p>مشاهده و منقضی کردن نشست‌های فعال مشتری</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="ds-accordion-icon" />
                  </div>
                  <div v-show="openSections.detailSessions" class="ds-accordion-body customer-accordion-body">
                    <div v-if="selectedRelation.status !== 'active' || !selectedRelation.customer_user_id" class="customer-empty">نشست فقط برای مشتری فعال قابل مدیریت است.</div>
                    <div v-else-if="loadingSessionsRelationId === selectedRelation.id" class="customer-loading session-loading">در حال دریافت نشست‌های مشتری...</div>
                    <div v-else-if="!getRelationSessions(selectedRelation.id).length" class="customer-empty session-empty">در حال حاضر نشست فعالی برای این مشتری ثبت نشده است.</div>
                    <ul v-else class="session-list">
                      <li v-for="session in getRelationSessions(selectedRelation.id)" :key="session.id" class="session-item">
                        <div class="session-item-main">
                          <div class="session-item-top">
                            <strong>{{ session.device_name || 'دستگاه ناشناس' }}</strong>
                            <div class="session-badges">
                              <span v-if="session.is_primary" class="session-badge primary">primary</span>
                              <span class="session-badge neutral">{{ formatSessionPlatform(session.platform) }}</span>
                              <span class="session-badge neutral">{{ formatHomeServer(session.home_server) }}</span>
                            </div>
                          </div>
                          <div class="session-item-meta">
                            <span>آخرین فعالیت: {{ formatDateTime(session.last_active_at) }}</span>
                            <span>شروع نشست: {{ formatDateTime(session.created_at) }}</span>
                            <span v-if="session.device_ip">IP: {{ session.device_ip }}</span>
                          </div>
                        </div>
                        <button type="button" class="danger-btn terminate-session" :disabled="terminatingSessionId === session.id" @click="terminateCustomerSession(selectedRelation, session)">
                          {{ terminatingSessionId === session.id ? 'در حال پایان...' : 'پایان نشست' }}
                        </button>
                      </li>
                    </ul>
                    <div v-if="selectedRelation.status === 'active' && selectedRelation.customer_user_id" class="panel-actions compact">
                      <button type="button" class="ghost-btn refresh-sessions" :disabled="loadingSessionsRelationId === selectedRelation.id" @click="loadSessionsForRelation(selectedRelation.id)">نوسازی نشست‌ها</button>
                    </div>
                  </div>
                </div>

                <div class="detail-accordion ds-accordion danger-accordion" :class="{ open: openSections.detailDanger }">
                  <div class="ds-accordion-header" @click="toggleDetailSection('detailDanger')">
                    <div class="ds-accordion-header-info">
                      <Users :size="18" class="customer-section-icon" />
                      <div>
                        <h4>قطع رابطه با مشتری</h4>
                        <p>غیرفعال کردن رابطه و دسترسی مشتری</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="ds-accordion-icon" />
                  </div>
                  <div v-show="openSections.detailDanger" class="ds-accordion-body customer-accordion-body">
                    <p class="danger-copy">این عملیات رابطه این مشتری را غیرفعال می‌کند و باید فقط در صورت اطمینان انجام شود.</p>
                    <button v-if="selectedRelation.status === 'active'" type="button" class="danger-btn unlink-active" @click="unlinkRelation(selectedRelation)">قطع ارتباط با مشتری</button>
                    <button v-else-if="selectedRelation.status === 'pending'" type="button" class="danger-btn cancel-pending" @click="unlinkRelation(selectedRelation)">لغو دعوت مشتری</button>
                    <div v-else class="customer-empty">این رابطه در وضعیت قابل قطع نیست.</div>
                  </div>
                </div>
              </div>

              <div v-else-if="isLoading" class="customer-loading">در حال دریافت لیست مشتریان...</div>
              <div v-else-if="orderedRelations.length === 0" class="customer-empty">هنوز مشتری فعالی یا دعوت در انتظار ثبت نشده است.</div>

              <div v-else class="customer-list">
                <article
                  v-for="relation in orderedRelations"
                  :key="relation.id"
                  class="customer-card"
                >
                  <div class="customer-card-head customer-card-head--manage">
                    <button type="button" class="primary-btn manage-customer" @click="openCustomerDetail(relation)">مدیریت</button>
                    <div class="customer-card-main">
                      <div class="customer-card-title-row">
                        <div class="customer-identity-block">
                          <h5>{{ relation.management_name }}</h5>
                          <p class="customer-account-name">@{{ getRelationAccountName(relation) }}</p>
                        </div>
                        <div class="customer-card-head-side">
                          <span class="customer-status-badge" :class="`status-${relation.status}`">{{ statusLabel(relation.status) }}</span>
                          <span class="customer-tier-pill" :class="`tier-${relation.customer_tier}`">{{ getCustomerTierLabel(relation.customer_tier) }}</span>
                        </div>
                      </div>
                      <div class="customer-card-meta-pills">
                        <span v-if="relation.mobile_number" class="customer-info-pill customer-mobile-number">
                          <span>موبایل</span>
                          <strong>{{ relation.mobile_number }}</strong>
                        </span>
                        <span class="customer-info-pill">
                          <span>کمیسیون</span>
                          <strong>{{ formatMaybeNumber(relation.commission_rate, '%') }}</strong>
                        </span>
                        <span class="customer-info-pill">
                          <span>حداقل</span>
                          <strong>{{ formatMaybeNumber(relation.min_trade_quantity) }}</strong>
                        </span>
                        <span class="customer-info-pill">
                          <span>حداکثر</span>
                          <strong>{{ formatMaybeNumber(relation.max_trade_quantity) }}</strong>
                        </span>
                        <span class="customer-info-pill">
                          <span>سقف روزانه</span>
                          <strong>{{ formatMaybeNumber(relation.max_daily_trades) }}</strong>
                        </span>
                      </div>
                      <p v-if="getRelationStateText(relation)" class="customer-state-copy" :class="`status-${relation.status}`">{{ getRelationStateText(relation) }}</p>
                    </div>
                  </div>
                  <div v-if="relation.status === 'pending' && relation.registration_link" class="customer-actions">
                    <button type="button" class="secondary-btn copy-link" @click="copyRegistrationLink(relation)">
                      {{ copiedRelationId === relation.id ? 'کپی شد' : 'کپی لینک ثبت‌نام' }}
                    </button>
                  </div>
                </article>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.customer-manager-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: 20px 14px;
  background: rgba(15, 23, 42, 0.54);
  backdrop-filter: blur(10px);
}

.customer-manager-shell {
  width: min(1040px, 100%);
  max-height: 100%;
  overflow: auto;
  border-radius: 28px;
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98) 0%, rgba(255, 255, 255, 0.98) 26%, rgba(248, 250, 252, 0.98) 100%);
  box-shadow: 0 26px 80px rgba(15, 23, 42, 0.24);
  border: 1px solid rgba(245, 158, 11, 0.14);
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.customer-manager-header {
  display: grid;
  grid-template-columns: 44px 1fr 44px;
  align-items: center;
  min-height: 74px;
  gap: 12px;
  direction: ltr;
}

.customer-manager-title {
  text-align: center;
  direction: rtl;
}

.customer-manager-kicker {
  margin: 0 0 4px;
  font-size: 0.78rem;
  font-weight: 700;
  color: #d97706;
}

.customer-manager-header h3 {
  margin: 0;
  font-size: 1.35rem;
  color: #111827;
}

.customer-manager-back,
.ghost-btn,
.primary-btn,
.secondary-btn,
.danger-btn {
  border: 0;
  border-radius: 999px;
  min-height: 40px;
  padding: 0 16px;
  font-weight: 700;
  cursor: pointer;
}

.customer-manager-back {
  width: 44px;
  height: 44px;
  min-height: 44px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  color: #334155;
  border: 1px solid rgba(148, 163, 184, 0.16);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
}

.customer-manager-header-spacer {
  width: 44px;
  height: 44px;
}

.ghost-btn,
.secondary-btn {
  background: rgba(148, 163, 184, 0.14);
  color: #334155;
}

.primary-btn {
  background: linear-gradient(135deg, #f59e0b, #f97316);
  color: #fff;
  box-shadow: 0 10px 24px rgba(249, 115, 22, 0.24);
}

.danger-btn {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
}

.customer-manager-note,
.customer-banner {
  border-radius: 20px;
  padding: 14px 16px;
  font-size: 0.92rem;
}

.customer-manager-note {
  background: rgba(251, 191, 36, 0.14);
  color: #92400e;
}

.customer-banner.success {
  background: rgba(16, 185, 129, 0.14);
  color: #047857;
}

.customer-banner.error {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
}

.card-with-help {
  position: relative;
}

.manager-category-menu {
  padding: 1rem;
  padding-left: 3.8rem;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 1.25rem;
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.72), rgba(255, 255, 255, 0.96));
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.07);
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.manager-category-heading {
  margin-bottom: 0.7rem;
  padding-right: 0.2rem;
  font-size: 0.8rem;
  font-weight: 800;
  color: #92400e;
}

.menu-button {
  width: 100%;
  min-height: 3.4rem;
  padding: 0.78rem 0.9rem;
  font-size: 0.85rem;
  font-weight: 850;
  background: rgba(255, 255, 255, 0.94);
  color: #1f2937;
  border: 1px solid rgba(15, 23, 42, 0.07);
  border-radius: 1rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.72rem;
  transition: all 0.2s;
  text-align: right;
  -webkit-tap-highlight-color: transparent;
}

.menu-button:hover {
  border-color: rgba(245, 158, 11, 0.3);
  background: #fffbeb;
}

.menu-button:active {
  transform: scale(0.98);
}

.menu-button-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.8rem;
  background: rgba(245, 158, 11, 0.12);
  color: #92400e;
  flex: 0 0 auto;
}

.menu-button-copy {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.18rem;
}

.menu-button-label {
  flex: 1;
  min-width: 0;
}

.menu-button-note {
  font-size: 0.72rem;
  line-height: 1.55;
  font-weight: 600;
  color: #6b7280;
}

.settings-btn {
  background: linear-gradient(135deg, #fffbeb, #fef3c7) !important;
  color: #92400e !important;
  border-color: rgba(245, 158, 11, 0.2) !important;
}

.settings-btn .menu-button-icon {
  background: rgba(245, 158, 11, 0.12);
  color: #92400e;
}

.customer-panel {
  border-radius: 1rem;
}

.customer-panel--accordion .ds-accordion {
  border-radius: 1rem;
  border: 1px solid rgba(245, 158, 11, 0.18);
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  overflow: hidden;
  box-shadow: 0 10px 28px rgba(245, 158, 11, 0.08);
}

.customer-panel--accordion .ds-accordion-header {
  gap: 0.6rem;
  min-height: 3.28rem;
  padding: 0.72rem 0.82rem;
}

.customer-panel--accordion .ds-accordion-header-info {
  gap: 0.58rem;
}

.customer-panel--accordion .ds-accordion-header-info h4,
.form-subpanel--accordion .ds-accordion-header-info h5 {
  margin: 0;
  color: #92400e;
}

.customer-main-menu-header {
  display: grid;
  grid-template-columns: minmax(9rem, 1fr) minmax(0, auto);
  align-items: center;
  direction: rtl;
}

.customer-menu-title {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  min-width: 0;
}

.customer-menu-title h4 {
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: inherit;
  font-size: 0.88rem;
  font-weight: 850;
  letter-spacing: 0;
  line-height: 1.6;
}

.customer-menu-note {
  max-width: clamp(6.2rem, 28vw, 12rem);
  overflow: hidden;
  color: #6b7280;
  direction: rtl;
  font-size: 0.7rem;
  font-weight: 650;
  line-height: 1.45;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.customer-main-menu-header .accordion-header-actions {
  min-width: 0;
  max-width: clamp(7.5rem, 36vw, 13.5rem);
  gap: 0.38rem;
  justify-content: flex-end;
}

.form-subpanel--accordion .ds-accordion-header-info p {
  margin: 3px 0 0;
  font-size: 0.74rem;
  line-height: 1.55;
  color: #64748b;
}

.form-subpanel--accordion .ds-accordion-header-info h5,
.detail-accordion .ds-accordion-header-info h4 {
  font-size: 0.86rem;
  line-height: 1.7;
}

.detail-accordion .ds-accordion-header-info p {
  margin: 2px 0 0;
  color: #64748b;
  font-size: 0.74rem;
  line-height: 1.6;
}

.customer-accordion-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.customer-section-icon,
.customer-subsection-icon {
  color: #d97706;
}

.panel-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.panel-title-row h4 {
  margin: 0 0 4px;
  color: #0f172a;
}

.panel-title-row p {
  margin: 0;
  color: #64748b;
  font-size: 0.9rem;
}

.customer-form-grid,
.customer-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.customer-form-sections {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.customer-form-sections--compact {
  grid-template-columns: 1fr;
}

.customer-form-sections--stacked {
  grid-template-columns: 1fr;
}

.form-subpanel {
  border-radius: 20px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(248, 250, 252, 0.72);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-subpanel--compact {
  background: rgba(255, 255, 255, 0.9);
}

.form-subpanel--accordion {
  padding: 0;
  overflow: hidden;
}

.form-subpanel--accordion .ds-accordion {
  border: 0;
  border-radius: 20px;
  background: transparent;
}

.form-subpanel--accordion .ds-accordion-body {
  padding-top: 2px;
}

.form-subpanel-head h5 {
  margin: 0 0 4px;
  font-size: 0.92rem;
  color: #0f172a;
}

.form-subpanel-head p {
  margin: 0;
  font-size: 0.8rem;
  color: #64748b;
}

.compact-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.field-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-block small {
  color: #64748b;
  font-size: 0.75rem;
  line-height: 1.7;
}

.field-block span,
.meta-label {
  font-size: 0.84rem;
  font-weight: 700;
  color: #475569;
}

.customer-input {
  width: 100%;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(255, 255, 255, 0.96);
  min-height: 46px;
  padding: 0 14px;
  font: inherit;
  color: #0f172a;
}

.commission-panel {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  padding: 0.85rem;
  border-radius: 1rem;
  border: 1px solid rgba(245, 158, 11, 0.22);
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.94), rgba(255, 247, 237, 0.92));
}

.commission-input-shell {
  display: grid;
  grid-template-columns: 2.25rem minmax(0, 1fr) 2.25rem;
  gap: 0.5rem;
  align-items: center;
}

.commission-input {
  text-align: center;
  direction: ltr;
}

.commission-step-btn {
  width: 2.25rem;
  height: 2.25rem;
  border: 0;
  border-radius: 0.8rem;
  background: rgba(245, 158, 11, 0.16);
  color: #92400e;
  font-size: 1.15rem;
  font-weight: 900;
  cursor: pointer;
  touch-action: none;
}

.commission-preview {
  margin: 0;
  padding: 0.7rem 0.8rem;
  border-radius: 0.9rem;
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
  font-size: 0.78rem;
  font-weight: 800;
  line-height: 1.8;
}

.commission-preview.compact {
  display: block;
  padding: 0.55rem 0.65rem;
}

.panel-actions,
.customer-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.panel-actions.compact {
  justify-content: flex-start;
}

.customer-loading,
.customer-empty {
  text-align: center;
  padding: 20px 12px;
  color: #64748b;
}

.customer-list {
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.customer-card {
  border-radius: 14px;
  border: 1px solid rgba(15, 23, 42, 0.07);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(255, 251, 235, 0.72));
  padding: 0.7rem;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.045);
}

.customer-card-head {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.6rem;
}

.customer-card-head--manage {
  align-items: center;
}

.manage-customer {
  flex: 0 0 auto;
  order: -1;
  min-width: 74px;
  min-height: 2.5rem;
  padding: 0 12px;
  border-radius: 12px;
  font-size: 0.78rem;
  box-shadow: none;
}

.customer-card-main {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 0.48rem;
}

.customer-card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.customer-card-head-side {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 0 0 auto;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.customer-identity-block {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.customer-card-head h5 {
  margin: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 0.86rem;
  font-weight: 850;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.customer-account-name {
  margin: 0;
  color: #64748b;
  direction: ltr;
  font-size: 0.72rem;
  text-align: right;
}

.customer-mobile-number {
  direction: ltr;
  text-align: left;
}

.customer-status-badge {
  border-radius: 999px;
  padding: 4px 9px;
  font-size: 0.7rem;
  font-weight: 800;
  line-height: 1.5;
}

.customer-status-badge.status-pending {
  background: rgba(251, 191, 36, 0.18);
  color: #b45309;
}

.customer-status-badge.status-active {
  background: rgba(16, 185, 129, 0.16);
  color: #047857;
}

.customer-tier-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 25px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 800;
}

.customer-tier-pill.tier-tier1 {
  background: rgba(59, 130, 246, 0.12);
  color: #1d4ed8;
}

.customer-tier-pill.tier-tier2 {
  background: rgba(168, 85, 247, 0.12);
  color: #7e22ce;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-value {
  color: #0f172a;
  font-weight: 600;
}

.customer-card-meta-pills {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(6.8rem, 1fr));
  gap: 0.42rem;
}

.customer-info-pill {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  min-height: 2rem;
  gap: 0.42rem;
  padding: 0.28rem 0.52rem;
  border-radius: 0.78rem;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(148, 163, 184, 0.13);
  color: #475569;
  font-size: 0.68rem;
  font-weight: 700;
  line-height: 1.5;
}

.customer-info-pill span {
  color: #94a3b8;
  font-weight: 750;
}

.customer-info-pill strong {
  min-width: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 0.72rem;
  font-weight: 850;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.customer-state-copy {
  margin: 0;
  color: #64748b;
  font-size: 0.72rem;
  font-weight: 650;
  line-height: 1.7;
}

.customer-state-copy.status-pending {
  color: #b45309;
}

.customer-state-copy.status-active {
  color: #047857;
}

.customer-state-copy.status-expired,
.customer-state-copy.status-revoked,
.customer-state-copy.status-deleted {
  color: #b91c1c;
}

.session-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px dashed rgba(148, 163, 184, 0.4);
}

.session-panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.session-panel-header h6 {
  margin: 0 0 4px;
  font-size: 0.95rem;
  color: #0f172a;
}

.session-panel-header p {
  margin: 0;
  color: #64748b;
  font-size: 0.84rem;
  line-height: 1.7;
}

.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.accordion-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
  direction: ltr;
}

.ghost-btn--inline {
  min-height: 34px;
  padding: 0 12px;
}

.session-item-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.session-item-top {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.session-item-top strong {
  color: #0f172a;
}

.session-badges {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.session-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 800;
}

.session-badge.primary {
  background: rgba(16, 185, 129, 0.16);
  color: #047857;
}

.session-badge.neutral {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.session-item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  font-size: 0.82rem;
  color: #64748b;
}

.terminate-session {
  flex-shrink: 0;
}

.edit-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;
  border-top: 1px dashed rgba(148, 163, 184, 0.4);
}

.customer-detail-page {
  display: flex;
  flex-direction: column;
  gap: 0.875rem;
}

.customer-detail-topbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.9rem;
  padding: 0.9rem;
  border-radius: 1.1rem;
  background: rgba(255, 251, 235, 0.72);
  border: 1px solid rgba(245, 158, 11, 0.14);
}

.customer-detail-topbar h4,
.customer-detail-topbar p {
  margin: 0;
}

.customer-detail-topbar p {
  color: #64748b;
  direction: ltr;
  text-align: right;
}

.detail-accordion {
  border-radius: 1.15rem;
  overflow: hidden;
}

.detail-metric-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.metric-card {
  min-height: 5rem;
  padding: 0.85rem;
  border-radius: 1rem;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.profit-card {
  background: linear-gradient(135deg, rgba(236, 253, 245, 0.95), rgba(220, 252, 231, 0.84));
}

.trade-list,
.stats-report,
.commodity-breakdown {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.trade-row,
.commodity-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.9rem;
  padding: 0.85rem;
  border-radius: 1rem;
  border: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(255, 255, 255, 0.9);
}

.trade-row p,
.commodity-breakdown h5,
.commodity-breakdown p,
.stats-note,
.danger-copy {
  margin: 0;
}

.trade-row p,
.stats-note {
  color: #64748b;
  font-size: 0.8rem;
  line-height: 1.7;
}

.trade-row-values {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.25rem;
  color: #0f172a;
  font-weight: 800;
  white-space: nowrap;
}

.stats-periods {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.history-chip {
  border: 0;
  min-height: 2.15rem;
  padding: 0 0.8rem;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
  font-weight: 800;
  cursor: pointer;
}

.history-chip.active {
  background: linear-gradient(135deg, #f59e0b, #f97316);
  color: #fff;
}

.commodity-breakdown {
  padding: 0.85rem;
  border-radius: 1rem;
  background: rgba(248, 250, 252, 0.82);
  border: 1px solid rgba(148, 163, 184, 0.14);
}

.commodity-row strong {
  color: #0f172a;
}

.danger-accordion {
  border-color: rgba(239, 68, 68, 0.18) !important;
}

.danger-copy {
  color: #991b1b;
  line-height: 1.8;
  font-weight: 700;
}

@media (max-width: 720px) {
  .customer-manager-backdrop {
    padding: 0;
  }

  .customer-manager-shell {
    width: 100%;
    border-radius: 24px 24px 0 0;
    min-height: 100%;
    padding: 12px 20px 22px;
    gap: 10px;
  }

  .panel-title-row,
  .session-panel-header,
  .session-item {
    flex-direction: column;
  }

  .customer-form-sections,
  .customer-form-grid,
  .customer-meta-grid,
  .compact-grid,
  .detail-metric-grid {
    grid-template-columns: 1fr;
  }

  .customer-detail-topbar,
  .trade-row,
  .commodity-row {
    align-items: stretch;
    flex-direction: column;
  }

  .trade-row-values {
    align-items: flex-start;
  }

  .panel-actions,
  .customer-actions {
    justify-content: stretch;
  }

  .panel-actions > button,
  .customer-actions > button,
  .ghost-btn,
  .ghost-btn--inline {
    width: 100%;
  }

  .accordion-header-actions {
    justify-content: flex-start;
  }

  .customer-main-menu-header {
    grid-template-columns: minmax(8.6rem, 1fr) minmax(0, 7.7rem);
    min-height: 3.85rem;
  }

  .customer-menu-title h4 {
    font-size: 0.88rem;
  }

  .customer-menu-note {
    max-width: 5.8rem;
    font-size: 0.66rem;
    line-height: 1.35;
  }

  .customer-main-menu-header .accordion-header-actions {
    max-width: 7.7rem;
  }

  .customer-card {
    padding: 0.62rem;
  }

  .customer-card-head {
    flex-direction: row;
    align-items: flex-start;
    gap: 0.5rem;
  }

  .manage-customer {
    min-width: 66px;
    min-height: 2.35rem;
    padding: 0 10px;
    font-size: 0.72rem;
  }

  .customer-card-title-row {
    flex-direction: column;
    gap: 6px;
  }

  .customer-card-head-side {
    align-items: flex-start;
    justify-content: flex-start;
  }

  .customer-card-meta-pills {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
