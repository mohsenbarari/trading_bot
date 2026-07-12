import { computed, reactive, ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { parseOwnerRelationApiError, type RelationStatus } from './useOwnerCustomers'

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
  invitation_token: string
  registration_link: string | null
  bot_registration_link?: string | null
  web_registration_link?: string | null
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

async function parseJson(response: Response) {
  return response.json().catch(() => null)
}

export async function fetchOwnerAccountantRelations() {
  const response = await apiFetch('/api/accountants/owner-relations')
  const payload = await parseJson(response)
  if (!response.ok) {
    throw new Error(parseOwnerRelationApiError(payload, 'دریافت لیست حسابداران ناموفق بود.'))
  }
  return Array.isArray(payload) ? payload as AccountantRelation[] : []
}

export async function createOwnerAccountantRelation(payload: Record<string, unknown>) {
  const response = await apiFetch('/api/accountants/owner-relations', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  const responsePayload = await parseJson(response)
  if (!response.ok) {
    throw new Error(parseOwnerRelationApiError(responsePayload, 'ایجاد حسابدار ناموفق بود.'))
  }
  return responsePayload as AccountantRelation
}

export async function updateOwnerAccountantRelation(relationId: number, payload: Record<string, unknown>) {
  const response = await apiFetch(`/api/accountants/owner-relations/${relationId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
  const responsePayload = await parseJson(response)
  if (!response.ok) {
    throw new Error(parseOwnerRelationApiError(responsePayload, 'ویرایش حسابدار ناموفق بود.'))
  }
  return responsePayload as AccountantRelation
}

export async function deleteOwnerAccountantRelation(relationId: number, fallback: string) {
  const response = await apiFetch(`/api/accountants/owner-relations/${relationId}`, {
    method: 'DELETE',
  })
  const payload = await parseJson(response)
  if (!response.ok) {
    throw new Error(parseOwnerRelationApiError(payload, fallback))
  }
  return payload
}

export async function fetchOwnerAccountantSessions(relationId: number) {
  const response = await apiFetch(`/api/accountants/owner-relations/${relationId}/sessions`, {
    method: 'GET',
  })
  const payload = await parseJson(response)
  if (!response.ok) {
    throw new Error(parseOwnerRelationApiError(payload, 'دریافت نشست‌های حسابدار ناموفق بود.'))
  }
  return Array.isArray(payload) ? payload as AccountantSessionSummary[] : []
}

export async function terminateOwnerAccountantSession(relationId: number, sessionId: string) {
  const response = await apiFetch(`/api/accountants/owner-relations/${relationId}/sessions/${sessionId}`, {
    method: 'DELETE',
  })
  const payload = await parseJson(response)
  if (!response.ok) {
    throw new Error(parseOwnerRelationApiError(payload, 'پایان دادن نشست حسابدار ناموفق بود.'))
  }
  return payload as AccountantSessionTerminateResponse | null
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
