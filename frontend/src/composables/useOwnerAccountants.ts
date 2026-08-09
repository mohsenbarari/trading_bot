import { computed, reactive, ref } from 'vue'
import { routeRequestJson } from '../utils/routeRequest'
import { type RelationStatus } from './useOwnerCustomers'

export interface AccountantRelation {
  id: number
  owner_user_id: number
  accountant_user_id: number | null
  accountant_account_name: string | null
  global_account_name: string
  relation_display_name: string
  duty_description: string | null
  mobile_number: string
  status: RelationStatus
  registration_link?: string | null
  bot_registration_link?: string | null
  web_registration_link?: string | null
  web_short_link?: string | null
  sms_status?: string | null
  expires_at: string
  activated_at: string | null
  deleted_at: string | null
  created_at: string
}

export interface AccountantSessionSummary {
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

export interface AccountantSessionTerminateResponse {
  detail: string
  terminated_session_id: string
  promoted_primary_session_id: string | null
}

export function makeEmptyAccountantCreateForm() {
  return {
    account_name: '',
    relation_display_name: '',
    mobile_number: '',
    duty_description: '',
  }
}

export function makeEmptyAccountantEditForm() {
  return {
    duty_description: '',
  }
}

export function normalizeDutyDescription(value: string) {
  const cleaned = value.trim()
  return cleaned || null
}

interface OwnerAccountantRequestOptions {
  signal?: AbortSignal | null
}

function requireArrayPayload<T>(payload: unknown, fallback: string): T[] {
  if (!Array.isArray(payload)) throw new Error(fallback)
  return payload as T[]
}

export async function fetchOwnerAccountantRelations(options: OwnerAccountantRequestOptions = {}) {
  const payload = await routeRequestJson<unknown>('/api/accountants/owner-relations', {
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'list',
      operation: 'load-list',
      fallbackMessage: 'دریافت لیست حسابداران ناموفق بود.',
    },
  })
  return requireArrayPayload<AccountantRelation>(payload, 'پاسخ لیست حسابداران معتبر نبود.')
}

export async function createOwnerAccountantRelation(
  payload: Record<string, unknown>,
  options: OwnerAccountantRequestOptions = {},
) {
  return routeRequestJson<AccountantRelation>('/api/accountants/owner-relations', {
    method: 'POST',
    body: JSON.stringify(payload),
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'form',
      operation: 'submit',
      userInitiated: true,
      fallbackMessage: 'ایجاد حسابدار ناموفق بود.',
    },
  })
}

export async function updateOwnerAccountantRelation(
  relationId: number,
  payload: Record<string, unknown>,
  options: OwnerAccountantRequestOptions = {},
) {
  return routeRequestJson<AccountantRelation>(`/api/accountants/owner-relations/${relationId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'form',
      operation: 'update',
      userInitiated: true,
      fallbackMessage: 'ویرایش حسابدار ناموفق بود.',
    },
  })
}

export async function deleteOwnerAccountantRelation(
  relationId: number,
  fallback: string,
  options: OwnerAccountantRequestOptions = {},
) {
  return routeRequestJson<AccountantRelation>(`/api/accountants/owner-relations/${relationId}`, {
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

export async function fetchOwnerAccountantSessions(
  relationId: number,
  options: OwnerAccountantRequestOptions = {},
) {
  const payload = await routeRequestJson<unknown>(`/api/accountants/owner-relations/${relationId}/sessions`, {
    method: 'GET',
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'list',
      operation: 'load-detail',
      fallbackMessage: 'دریافت نشست‌های حسابدار ناموفق بود.',
    },
  })
  return requireArrayPayload<AccountantSessionSummary>(payload, 'پاسخ نشست‌های حسابدار معتبر نبود.')
}

export async function terminateOwnerAccountantSession(
  relationId: number,
  sessionId: string,
  options: OwnerAccountantRequestOptions = {},
) {
  return routeRequestJson<AccountantSessionTerminateResponse>(`/api/accountants/owner-relations/${relationId}/sessions/${sessionId}`, {
    method: 'DELETE',
    ...(options.signal ? { signal: options.signal } : {}),
    errorContext: {
      scope: 'action',
      operation: 'delete',
      userInitiated: true,
      fallbackMessage: 'پایان دادن نشست حسابدار ناموفق بود.',
    },
  })
}

export function useOwnerAccountants() {
  const relations = ref<AccountantRelation[]>([])
  const createForm = reactive(makeEmptyAccountantCreateForm())
  const editForm = reactive(makeEmptyAccountantEditForm())
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
    editForm,
    selectedRelationId,
    orderedRelations,
    pendingInvitationRelations,
    manageableRelations,
    selectedRelation,
  }
}
