import { computed, reactive, ref } from 'vue'
import { routeRequestJson } from '../utils/routeRequest'

export type RelationStatus = 'pending' | 'active' | 'expired' | 'revoked' | 'deleted' | string
export type CustomerTier = 'tier1' | 'tier2'

export interface CustomerRelation {
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
  bot_registration_link?: string | null
  web_registration_link?: string | null
  web_short_link?: string | null
  sms_status?: string | null
  expires_at: string | null
  activated_at: string | null
  deleted_at: string | null
  created_at: string
}

export interface CustomerSessionSummary {
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

export interface CustomerSessionTerminateResponse {
  detail: string
  terminated_session_id: string
  promoted_primary_session_id: string | null
}

export interface CustomerTradeSummary {
  id: number
  trade_number: number
  trade_type: string
  settlement_type: 'cash' | 'tomorrow'
  commodity_name: string
  quantity: number
  price: number
  status: string
  counterparty_name?: string | null
  created_at: string
}

export interface CustomerTradeStatsCommodity {
  commodity_id: number
  commodity_name: string
  total_quantity: number
}

export interface CustomerTradeStats {
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

export function makeEmptyCustomerCreateForm() {
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

export function makeEmptyCustomerDetailEditForm() {
  return {
    customer_tier: '',
    commission_rate: '',
    min_trade_quantity: '',
    max_trade_quantity: '',
    max_daily_trades: '',
    max_daily_commodity_volume: '',
  }
}

export function parseOwnerRelationApiError(payload: unknown, fallback: string) {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
  }
  return fallback
}

export function normalizeLatinDigits(value: string) {
  const persian = '۰۱۲۳۴۵۶۷۸۹'
  const arabic = '٠١٢٣٤٥٦٧٨٩'
  return String(value || '')
    .replace(/[۰-۹]/g, (digit) => String(persian.indexOf(digit)))
    .replace(/[٠-٩]/g, (digit) => String(arabic.indexOf(digit)))
}

export function normalizeOptionalNumber(value: string | number | null | undefined) {
  if (value == null) return null
  const cleaned = String(value).trim()
  if (!cleaned) return null
  const normalized = Number(cleaned)
  return Number.isFinite(normalized) ? normalized : null
}

export function normalizeCommissionRate(value: string | number | null | undefined) {
  const normalized = Number(normalizeLatinDigits(String(value ?? '')).replace(',', '.'))
  if (!Number.isFinite(normalized)) return 0
  return Math.min(100, Math.max(0, normalized))
}

export function buildCustomerPayload(form: {
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

export function buildCustomerDetailUpdatePayload(
  relation: CustomerRelation,
  detailEditForm: ReturnType<typeof makeEmptyCustomerDetailEditForm>,
) {
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

interface OwnerCustomerRequestOptions {
  signal?: AbortSignal | null
  retryNetwork?: boolean
}

function requireArrayPayload<T>(payload: unknown, fallback: string): T[] {
  if (!Array.isArray(payload)) throw new Error(fallback)
  return payload as T[]
}

export async function fetchOwnerCustomerRelations(options: OwnerCustomerRequestOptions = {}) {
  const payload = await routeRequestJson<unknown>('/api/customers/owner-relations', {
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'list',
      operation: 'load-list',
      fallbackMessage: 'دریافت لیست مشتریان ناموفق بود.',
    },
  })
  return requireArrayPayload<CustomerRelation>(payload, 'پاسخ لیست مشتریان معتبر نبود.')
}

export async function createOwnerCustomerRelation(
  payload: Record<string, unknown>,
  options: OwnerCustomerRequestOptions = {},
) {
  return routeRequestJson<CustomerRelation>('/api/customers/owner-relations', {
    method: 'POST',
    body: JSON.stringify(payload),
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'form',
      operation: 'submit',
      userInitiated: true,
      fallbackMessage: 'ایجاد مشتری ناموفق بود.',
    },
  })
}

export async function updateOwnerCustomerRelation(
  relationId: number,
  payload: Record<string, unknown>,
  options: OwnerCustomerRequestOptions = {},
) {
  return routeRequestJson<CustomerRelation>(`/api/customers/owner-relations/${relationId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'form',
      operation: 'update',
      userInitiated: true,
      fallbackMessage: 'ویرایش مشتری ناموفق بود.',
    },
  })
}

export async function deleteOwnerCustomerRelation(
  relationId: number,
  fallback: string,
  options: OwnerCustomerRequestOptions = {},
) {
  return routeRequestJson<CustomerRelation>(`/api/customers/owner-relations/${relationId}`, {
    method: 'DELETE',
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'action',
      operation: 'delete',
      userInitiated: true,
      fallbackMessage: fallback,
    },
  })
}

export async function fetchOwnerCustomerSessions(
  relationId: number,
  options: OwnerCustomerRequestOptions = {},
) {
  const payload = await routeRequestJson<unknown>(`/api/customers/owner-relations/${relationId}/sessions`, {
    method: 'GET',
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'list',
      operation: 'load-detail',
      fallbackMessage: 'دریافت نشست‌های مشتری ناموفق بود.',
    },
  })
  return requireArrayPayload<CustomerSessionSummary>(payload, 'پاسخ نشست‌های مشتری معتبر نبود.')
}

export async function terminateOwnerCustomerSession(
  relationId: number,
  sessionId: string,
  options: OwnerCustomerRequestOptions = {},
) {
  return routeRequestJson<CustomerSessionTerminateResponse>(`/api/customers/owner-relations/${relationId}/sessions/${sessionId}`, {
    method: 'DELETE',
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'action',
      operation: 'delete',
      userInitiated: true,
      fallbackMessage: 'پایان دادن نشست مشتری ناموفق بود.',
    },
  })
}

export async function fetchOwnerCustomerTrades(
  customerUserId: number,
  options: OwnerCustomerRequestOptions & { limit?: number } = {},
) {
  const payload = await routeRequestJson<unknown>(`/api/trades/with/${customerUserId}?limit=${options.limit ?? 20}`, {
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'list',
      operation: 'load-detail',
      fallbackMessage: 'دریافت معاملات مشتری ناموفق بود.',
    },
  })
  return requireArrayPayload<CustomerTradeSummary>(payload, 'پاسخ معاملات مشتری معتبر نبود.')
}

export async function fetchOwnerCustomerTradeStats(
  relationId: number,
  days: number,
  options: OwnerCustomerRequestOptions = {},
) {
  const payload = await routeRequestJson<unknown>(`/api/customers/owner-relations/${relationId}/trade-stats?days=${days}`, {
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'panel',
      operation: 'load-detail',
      fallbackMessage: 'دریافت آمار مشتری ناموفق بود.',
    },
  })
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('پاسخ آمار مشتری معتبر نبود.')
  }
  return payload as CustomerTradeStats
}

export function useOwnerCustomers() {
  const relations = ref<CustomerRelation[]>([])
  const createForm = reactive(makeEmptyCustomerCreateForm())
  const detailEditForm = reactive(makeEmptyCustomerDetailEditForm())
  const selectedRelationId = ref<number | null>(null)

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

  const pendingInvitationRelations = computed(() => orderedRelations.value.filter((relation) => relation.status === 'pending'))
  const manageableRelations = computed(() => orderedRelations.value.filter((relation) => relation.status !== 'pending'))
  const selectedRelation = computed(() => {
    if (selectedRelationId.value == null) return null
    return relations.value.find((relation) => relation.id === selectedRelationId.value) ?? null
  })

  return {
    relations,
    createForm,
    detailEditForm,
    selectedRelationId,
    orderedRelations,
    pendingInvitationRelations,
    manageableRelations,
    selectedRelation,
  }
}
