import { apiFetch } from '../utils/auth'

export interface OvertimeRequestPublicPayload {
  workflow?: string
  request_public_id?: string
  offer_public_id?: string
  request_home_server?: string
  result_status?: string
  requested_quantity?: number
  presented_at?: string | null
  decision_deadline_at?: string | null
  remaining_decision_seconds?: number | null
  is_occupying?: boolean
  is_actionable?: boolean
  viewer_role?: string
  promoted?: boolean
  duplicate_replay?: boolean
  is_local_home?: boolean
}

export interface PendingOwnerOvertimeResponse {
  request_home_server?: string
  viewer_role?: string
  current: OvertimeRequestPublicPayload | null
  items: OvertimeRequestPublicPayload[]
}

export interface PendingRequesterOvertimeResponse {
  request_home_server?: string
  viewer_role?: string
  items: OvertimeRequestPublicPayload[]
}

export interface OfferOvertimePreferenceSaveResponse {
  offer_overtime_minutes: number
  detail: string
  warning?: string | null
}

export interface PublicOfferSummary {
  offer_public_id: string
  status?: string
  offer_type?: string
  settlement_type?: string
  commodity_name?: string
  quantity?: number
  remaining_quantity?: number
  price?: number
  is_wholesale?: boolean
  lot_sizes?: number[] | null
  notes?: string | null
}

function extractDetailMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null
  const detail = (payload as { detail?: unknown }).detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object') {
    const message = (detail as { message?: unknown }).message
    if (typeof message === 'string' && message.trim()) return message
  }
  return null
}

export async function readApiErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const payload = await response.json()
    return extractDetailMessage(payload) || fallback
  } catch {
    return fallback
  }
}

export async function fetchPendingOwnerOvertimeRequests(): Promise<PendingOwnerOvertimeResponse> {
  const response = await apiFetch('/api/trades/overtime-requests/pending-owner')
  if (!response.ok) {
    throw new Error(await readApiErrorMessage(response, ''))
  }
  return response.json()
}

export async function fetchPendingRequesterOvertimeRequests(): Promise<PendingRequesterOvertimeResponse> {
  const response = await apiFetch('/api/trades/overtime-requests/pending-requester')
  if (!response.ok) {
    throw new Error(await readApiErrorMessage(response, ''))
  }
  return response.json()
}

export async function approveOvertimeRequest(requestPublicId: string): Promise<Response> {
  return apiFetch(`/api/trades/overtime-requests/${encodeURIComponent(requestPublicId)}/approve`, {
    method: 'POST',
    retryNetwork: false,
  })
}

export async function rejectOvertimeRequest(requestPublicId: string): Promise<Response> {
  return apiFetch(`/api/trades/overtime-requests/${encodeURIComponent(requestPublicId)}/reject`, {
    method: 'POST',
    retryNetwork: false,
  })
}

export async function cancelOvertimeRequest(requestPublicId: string): Promise<Response> {
  return apiFetch(`/api/trades/overtime-requests/${encodeURIComponent(requestPublicId)}/cancel`, {
    method: 'POST',
    retryNetwork: false,
  })
}

export async function saveOfferOvertimePreference(
  minutes: number,
): Promise<OfferOvertimePreferenceSaveResponse> {
  const response = await apiFetch('/api/auth/me/offer-overtime', {
    method: 'PUT',
    body: JSON.stringify({ offer_overtime_minutes: minutes }),
  })
  if (!response.ok) {
    throw new Error(await readApiErrorMessage(response, ''))
  }
  return response.json()
}

export async function fetchPublicOfferSummary(
  offerPublicId: string,
): Promise<PublicOfferSummary | null> {
  const response = await apiFetch(`/api/offers/public/${encodeURIComponent(offerPublicId)}`)
  if (!response.ok) return null
  return response.json()
}

export function formatPublicOfferBody(offer: PublicOfferSummary): string {
  const typeLabel = offer.offer_type === 'buy' ? 'خرید' : 'فروش'
  const settlement =
    offer.settlement_type === 'tomorrow' || offer.settlement_type === 'فردا' ? 'فردا' : 'نقد'
  const commodity = (offer.commodity_name || 'کالا').trim()
  const quantity = Number(offer.remaining_quantity ?? offer.quantity ?? 0)
  const price = Number(offer.price ?? 0)
  const lines = [
    `${typeLabel} ${settlement}`,
    commodity,
    `تعداد: ${quantity.toLocaleString('fa-IR')}`,
    `فی: ${price.toLocaleString('fa-IR')}`,
  ]
  if (offer.is_wholesale === false) {
    const lots = Array.isArray(offer.lot_sizes) ? offer.lot_sizes : []
    if (lots.length) {
      lines.push(`خرد · پله‌ها: ${lots.map((lot) => lot.toLocaleString('fa-IR')).join(' + ')}`)
    } else {
      lines.push('خرد')
    }
  }
  const notes = offer.notes?.trim()
  if (notes) {
    lines.push(notes)
  }
  return lines.join('\n')
}
