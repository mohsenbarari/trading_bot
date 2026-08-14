/** Server-authoritative offer lifecycle helpers for market cards. */

export type OfferLifecyclePhase =
  | 'normal'
  | 'overtime'
  | 'final_tail'
  | 'expired'
  | string
  | null
  | undefined

export function getOfferLifecyclePhase(offer: {
  lifecycle_phase?: OfferLifecyclePhase
} | null | undefined): string | null {
  const phase = offer?.lifecycle_phase
  if (typeof phase !== 'string') return null
  const normalized = phase.trim().toLowerCase()
  return normalized || null
}

export function isOvertimePhase(offer: { lifecycle_phase?: OfferLifecyclePhase } | null | undefined): boolean {
  return getOfferLifecyclePhase(offer) === 'overtime'
}

export function isFinalTailPhase(offer: { lifecycle_phase?: OfferLifecyclePhase } | null | undefined): boolean {
  return getOfferLifecyclePhase(offer) === 'final_tail'
}

/** Active rows that must stay on the market list despite expires_at_ts. */
export function isActiveLifecycleVisible(
  offer: {
    lifecycle_phase?: OfferLifecyclePhase
    accepts_new_public_interaction?: boolean | null
    expires_at_ts?: number | null
    status?: string | null
  } | null | undefined,
  nowSec: number,
): boolean {
  if (!offer) return false
  const status = String(offer.status ?? '').toLowerCase()
  if (status && status !== 'active') return false
  if (isFinalTailPhase(offer)) return true
  if (offer.accepts_new_public_interaction === true) return true
  if (!offer.expires_at_ts) return true
  return Number(offer.expires_at_ts) > nowSec
}

export function showOvertimeMarker(
  offer: {
    lifecycle_phase?: OfferLifecyclePhase
    overtime_trade_committed?: boolean | null
    is_read_only?: boolean
    history_state?: string | null
    status?: string | null
  } | null | undefined,
): boolean {
  if (!offer) return false
  if (isOvertimePhase(offer) || isFinalTailPhase(offer)) return true
  const isHistory = offer.is_read_only === true
    || typeof offer.history_state === 'string'
    || (String(offer.status ?? '').toLowerCase() !== '' && String(offer.status).toLowerCase() !== 'active')
  return isHistory && offer.overtime_trade_committed === true
}

export function isOvertimeMarkerAnimated(
  offer: { lifecycle_phase?: OfferLifecyclePhase } | null | undefined,
): boolean {
  return isOvertimePhase(offer)
}

function positiveTs(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

/** Deadline used for the visible timer ring for the current phase. */
export function timerDeadlineTs(offer: {
  lifecycle_phase?: OfferLifecyclePhase
  expires_at_ts?: number | null
  normal_deadline_ts?: number | null
  final_deadline_ts?: number | null
} | null | undefined): number | null {
  if (!offer) return null
  const phase = getOfferLifecyclePhase(offer)
  if (phase === 'overtime') {
    return positiveTs(offer.final_deadline_ts) ?? positiveTs(offer.expires_at_ts)
  }
  if (phase === 'final_tail' || phase === 'expired') return null
  return positiveTs(offer.normal_deadline_ts) ?? positiveTs(offer.expires_at_ts)
}
