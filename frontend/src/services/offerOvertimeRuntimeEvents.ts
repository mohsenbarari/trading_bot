import type { OvertimeRequestPublicPayload } from './offerOvertimeApi'

export const OVERTIME_REQUESTER_ACKNOWLEDGED_EVENT =
  'offer-overtime:requester-acknowledged'

export function isRequesterOvertimeAcknowledgement(
  payload: unknown,
): payload is OvertimeRequestPublicPayload {
  if (!payload || typeof payload !== 'object') return false
  const candidate = payload as OvertimeRequestPublicPayload
  return candidate.workflow === 'overtime'
    && typeof candidate.request_public_id === 'string'
    && candidate.request_public_id.trim().length > 0
}

export function publishRequesterOvertimeAcknowledgement(payload: unknown): void {
  if (!isRequesterOvertimeAcknowledgement(payload) || typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<OvertimeRequestPublicPayload>(
    OVERTIME_REQUESTER_ACKNOWLEDGED_EVENT,
    { detail: payload },
  ))
}

export function readRequesterOvertimeAcknowledgement(
  event: Event,
): OvertimeRequestPublicPayload | null {
  const payload = (event as CustomEvent<unknown>).detail
  return isRequesterOvertimeAcknowledgement(payload) ? payload : null
}
