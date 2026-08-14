/**
 * Cross-modal arbitration for authenticated-shell overlays.
 *
 * Session / recovery approval always blocks overtime prompts. Overtime may
 * appear immediately afterward with its remaining server-authoritative time.
 */
import { computed, ref } from 'vue'

const sessionApprovalBlocking = ref(false)

export function setSessionApprovalBlocking(active: boolean) {
  sessionApprovalBlocking.value = Boolean(active)
}

export function isSessionApprovalBlocking() {
  return sessionApprovalBlocking.value
}

export const canPresentOvertimeOverlay = computed(() => !sessionApprovalBlocking.value)
