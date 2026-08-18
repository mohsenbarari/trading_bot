import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { canPresentOvertimeOverlay } from './authenticatedOverlayPriority'
import { useWebSocket } from './useWebSocket'
import {
  M15_CANCELLED,
  M21_REQUESTER_QUEUED,
  M37_REVALIDATION_FAILED,
  formatOvertimeCountdown,
  formatQuantityLine,
} from '../constants/offerOvertimeCopy'
import {
  approveOvertimeRequest,
  cancelOvertimeRequest,
  fetchPendingOwnerOvertimeRequests,
  fetchPendingRequesterOvertimeRequests,
  fetchPublicOfferSummary,
  formatPublicOfferBody,
  readApiErrorMessage,
  rejectOvertimeRequest,
  type OvertimeRequestPublicPayload,
  type PublicOfferSummary,
} from '../services/offerOvertimeApi'
import {
  OVERTIME_REQUESTER_ACKNOWLEDGED_EVENT,
  readRequesterOvertimeAcknowledgement,
} from '../services/offerOvertimeRuntimeEvents'

const INITIAL_FETCH_DELAY_MS = 1000
const POLL_INTERVAL_MS = 2000
const LOCAL_REQUESTER_ACK_GRACE_MS = 15_000
const LOCAL_REQUESTER_CANCEL_GRACE_MS = 30_000
const ACTIONABLE_STATUSES = new Set([
  'overtime_presented',
  'overtime_delivering',
  'overtime_claimed',
])

interface SessionSummary {
  is_current?: boolean
  is_primary?: boolean
}

function deadlineMs(payload: OvertimeRequestPublicPayload | null | undefined): number | null {
  const raw = payload?.decision_deadline_at
  if (!raw) return null
  const parsed = Date.parse(raw)
  return Number.isFinite(parsed) ? parsed : null
}

function remainingFromPayload(
  payload: OvertimeRequestPublicPayload | null | undefined,
  nowMs: number,
): number {
  const absolute = deadlineMs(payload)
  if (absolute != null) {
    return Math.max(0, Math.floor((absolute - nowMs) / 1000))
  }
  const remaining = payload?.remaining_decision_seconds
  if (typeof remaining === 'number' && Number.isFinite(remaining)) {
    return Math.max(0, Math.floor(remaining))
  }
  return 0
}

export function useOvertimeApprovalRuntime() {
  const { on, off } = useWebSocket()

  const ownerRequest = ref<OvertimeRequestPublicPayload | null>(null)
  const requesterRequest = ref<OvertimeRequestPublicPayload | null>(null)
  const ownerOffer = ref<PublicOfferSummary | null>(null)
  const ownerOfferText = ref('')
  const showOwnerModal = ref(false)
  const showRequesterStatus = ref(false)
  const ownerCountdown = ref(0)
  const requesterCountdown = ref(0)
  const loading = ref(false)
  const ownerError = ref<string | null>(null)
  const requesterMessage = ref('')
  const requesterTerminalNotice = ref<string | null>(null)

  let pollTimer: number | null = null
  let countdownTimer: number | null = null
  let initialFetchTimeout: number | null = null
  let terminalNoticeTimeout: number | null = null
  let ownerOfferFetchToken = 0
  let refreshInFlight: Promise<void> | null = null
  let requesterRevision = 0
  let localRequesterAckId: string | null = null
  let localRequesterAckExpiresAt = 0
  let localRequesterCancelledId: string | null = null
  let localRequesterCancelledExpiresAt = 0

  const hasAuthToken = () => Boolean(localStorage.getItem('auth_token'))

  const ownerVisible = computed(
    () => showOwnerModal.value && canPresentOvertimeOverlay.value && Boolean(ownerRequest.value),
  )

  const requesterVisible = computed(
    () => showRequesterStatus.value && Boolean(requesterRequest.value || requesterTerminalNotice.value),
  )

  const ownerCountdownLabel = computed(() => formatOvertimeCountdown(ownerCountdown.value))
  const requesterCountdownLabel = computed(() => formatOvertimeCountdown(requesterCountdown.value))

  const ownerQuantityLine = computed(() => {
    const offer = ownerOffer.value
    const quantity = ownerRequest.value?.requested_quantity
    if (!offer || offer.is_wholesale !== false) return null
    if (typeof quantity !== 'number' || quantity <= 0) return null
    return formatQuantityLine(quantity)
  })

  const clearCountdownTimer = () => {
    if (countdownTimer != null) {
      window.clearInterval(countdownTimer)
      countdownTimer = null
    }
  }

  const clearPollTimer = () => {
    if (pollTimer != null) {
      window.clearInterval(pollTimer)
      pollTimer = null
    }
  }

  const clearInitialFetchTimeout = () => {
    if (initialFetchTimeout != null) {
      window.clearTimeout(initialFetchTimeout)
      initialFetchTimeout = null
    }
  }

  const clearTerminalNoticeTimeout = () => {
    if (terminalNoticeTimeout != null) {
      window.clearTimeout(terminalNoticeTimeout)
      terminalNoticeTimeout = null
    }
  }

  const closeOwnerModal = () => {
    ownerRequest.value = null
    ownerOffer.value = null
    ownerOfferText.value = ''
    showOwnerModal.value = false
    ownerCountdown.value = 0
    ownerError.value = null
  }

  const closeRequesterStatus = () => {
    requesterRequest.value = null
    showRequesterStatus.value = false
    requesterCountdown.value = 0
    requesterMessage.value = ''
  }

  const clearLocalRequesterAcknowledgement = () => {
    localRequesterAckId = null
    localRequesterAckExpiresAt = 0
  }

  const clearLocalRequesterCancellation = () => {
    localRequesterCancelledId = null
    localRequesterCancelledExpiresAt = 0
  }

  const syncCountdowns = () => {
    const now = Date.now()
    if (ownerRequest.value) {
      ownerCountdown.value = remainingFromPayload(ownerRequest.value, now)
      if (ownerCountdown.value <= 0 && ownerRequest.value.is_actionable) {
        closeOwnerModal()
      }
    } else {
      ownerCountdown.value = 0
    }

    if (requesterRequest.value?.is_actionable || requesterRequest.value?.presented_at) {
      requesterCountdown.value = remainingFromPayload(requesterRequest.value, now)
      if (requesterCountdown.value <= 0 && requesterRequest.value?.is_actionable) {
        closeRequesterStatus()
      }
    } else {
      requesterCountdown.value = 0
    }
  }

  const ensureCountdownTimer = () => {
    if (countdownTimer != null) return
    countdownTimer = window.setInterval(() => {
      syncCountdowns()
    }, 1000)
  }

  const shouldDisplayOwnerPromptOnThisSession = async () => {
    if (!hasAuthToken()) return false
    try {
      const response = await apiSessionsActive()
      if (!response.ok) return true
      const sessions = await response.json()
      const current = Array.isArray(sessions)
        ? (sessions as SessionSummary[]).find((session) => session?.is_current)
        : undefined
      // Explicit multi-tab rule: only the primary session shows owner prompts.
      // Non-primary tabs stay silent; there is no cross-tab handoff.
      return current ? Boolean(current.is_primary) : true
    } catch {
      return true
    }
  }

  async function apiSessionsActive() {
    const { apiFetch } = await import('../utils/auth')
    return apiFetch('/api/sessions/active')
  }

  const loadOwnerOfferContext = async (offerPublicId: string | undefined) => {
    if (!offerPublicId) {
      ownerOffer.value = null
      ownerOfferText.value = ''
      return
    }
    const token = ++ownerOfferFetchToken
    const summary = await fetchPublicOfferSummary(offerPublicId)
    if (token !== ownerOfferFetchToken) return
    ownerOffer.value = summary
    ownerOfferText.value = summary ? formatPublicOfferBody(summary) : ''
  }

  const applyOwnerPayload = async (payload: OvertimeRequestPublicPayload | null) => {
    if (!payload?.request_public_id || !payload.is_occupying) {
      closeOwnerModal()
      return
    }
    if (!(await shouldDisplayOwnerPromptOnThisSession())) {
      closeOwnerModal()
      return
    }
    const previousRequestId = ownerRequest.value?.request_public_id
    ownerRequest.value = payload
    showOwnerModal.value = true
    syncCountdowns()
    ensureCountdownTimer()
    if (previousRequestId !== payload.request_public_id || !ownerOfferText.value) {
      await loadOwnerOfferContext(payload.offer_public_id)
    }
  }

  const applyRequesterPayload = (payload: OvertimeRequestPublicPayload | null) => {
    if (
      payload?.request_public_id === localRequesterCancelledId
      && Date.now() < localRequesterCancelledExpiresAt
    ) {
      return
    }
    if (localRequesterCancelledId && Date.now() >= localRequesterCancelledExpiresAt) {
      clearLocalRequesterCancellation()
    }
    if (!payload?.request_public_id) {
      if (
        localRequesterAckId
        && requesterRequest.value?.request_public_id === localRequesterAckId
        && Date.now() < localRequesterAckExpiresAt
      ) {
        return
      }
      if (!requesterTerminalNotice.value) {
        closeRequesterStatus()
      }
      return
    }
    requesterRequest.value = payload
    showRequesterStatus.value = true
    const status = String(payload.result_status || '')
    if (payload.is_actionable || ACTIONABLE_STATUSES.has(status)) {
      requesterMessage.value = ''
      syncCountdowns()
      ensureCountdownTimer()
    } else {
      requesterMessage.value = M21_REQUESTER_QUEUED
      requesterCountdown.value = 0
    }
  }

  const runRefreshPending = async () => {
    if (!hasAuthToken()) {
      requesterRevision += 1
      clearLocalRequesterAcknowledgement()
      clearLocalRequesterCancellation()
      closeOwnerModal()
      closeRequesterStatus()
      return
    }

    const requesterRevisionAtStart = requesterRevision
    try {
      const [ownerBody, requesterBody] = await Promise.all([
        fetchPendingOwnerOvertimeRequests(),
        fetchPendingRequesterOvertimeRequests(),
      ])
      await applyOwnerPayload(ownerBody.current)
      if (requesterRevisionAtStart !== requesterRevision) return
      const firstRequester = Array.isArray(requesterBody.items) ? requesterBody.items[0] ?? null : null
      if (firstRequester?.request_public_id === localRequesterAckId) {
        clearLocalRequesterAcknowledgement()
      }
      applyRequesterPayload(firstRequester)
    } catch {
      // Soft-fail: keep last known UI until the next poll.
    }
  }

  const refreshPending = (): Promise<void> => {
    if (refreshInFlight) return refreshInFlight
    refreshInFlight = runRefreshPending().finally(() => {
      refreshInFlight = null
    })
    return refreshInFlight
  }

  const handleRequesterAcknowledged = (event: Event) => {
    const payload = readRequesterOvertimeAcknowledgement(event)
    if (!payload?.request_public_id) return
    requesterRevision += 1
    localRequesterAckId = payload.request_public_id
    localRequesterAckExpiresAt = Date.now() + LOCAL_REQUESTER_ACK_GRACE_MS
    requesterTerminalNotice.value = null
    applyRequesterPayload(payload)
  }

  const triggerRefresh = () => {
    clearInitialFetchTimeout()
    void refreshPending()
  }

  const approve = async () => {
    const requestId = ownerRequest.value?.request_public_id
    if (!requestId || loading.value) return
    loading.value = true
    ownerError.value = null
    try {
      const response = await approveOvertimeRequest(requestId)
      if (response.ok || response.status === 201) {
        closeOwnerModal()
        void refreshPending()
        return
      }
      const message = await readApiErrorMessage(response, M37_REVALIDATION_FAILED)
      ownerError.value = message || M37_REVALIDATION_FAILED
      void refreshPending()
    } catch {
      ownerError.value = M37_REVALIDATION_FAILED
    } finally {
      loading.value = false
    }
  }

  const reject = async () => {
    const requestId = ownerRequest.value?.request_public_id
    if (!requestId || loading.value) return
    loading.value = true
    ownerError.value = null
    try {
      const response = await rejectOvertimeRequest(requestId)
      if (response.ok) {
        closeOwnerModal()
        void refreshPending()
        return
      }
      ownerError.value = await readApiErrorMessage(response, '')
      void refreshPending()
    } catch {
      ownerError.value = null
    } finally {
      loading.value = false
    }
  }

  const cancel = async () => {
    const requestId = requesterRequest.value?.request_public_id
    if (!requestId || loading.value) return
    loading.value = true
    try {
      const response = await cancelOvertimeRequest(requestId)
      if (response.ok) {
        requesterRevision += 1
        clearLocalRequesterAcknowledgement()
        localRequesterCancelledId = requestId
        localRequesterCancelledExpiresAt = Date.now() + LOCAL_REQUESTER_CANCEL_GRACE_MS
        closeRequesterStatus()
        requesterTerminalNotice.value = M15_CANCELLED
        showRequesterStatus.value = true
        clearTerminalNoticeTimeout()
        terminalNoticeTimeout = window.setTimeout(() => {
          requesterTerminalNotice.value = null
          if (!requesterRequest.value) {
            showRequesterStatus.value = false
          }
        }, 2500)
        void refreshPending()
        return
      }
      void refreshPending()
    } catch {
      // keep status visible for retry
    } finally {
      loading.value = false
    }
  }

  const handleVisibilityChange = () => {
    if (document.visibilityState === 'visible') {
      triggerRefresh()
    }
  }

  const handleWsReconnect = () => {
    triggerRefresh()
  }

  watch(canPresentOvertimeOverlay, (allowed) => {
    if (allowed && ownerRequest.value) {
      showOwnerModal.value = true
      syncCountdowns()
    }
  })

  onMounted(() => {
    initialFetchTimeout = window.setTimeout(() => {
      void refreshPending()
    }, INITIAL_FETCH_DELAY_MS)
    pollTimer = window.setInterval(() => {
      void refreshPending()
    }, POLL_INTERVAL_MS)
    document.addEventListener('visibilitychange', handleVisibilityChange)
    window.addEventListener(OVERTIME_REQUESTER_ACKNOWLEDGED_EVENT, handleRequesterAcknowledged)
    on('ws:reconnect', handleWsReconnect)
  })

  onBeforeUnmount(() => {
    clearInitialFetchTimeout()
    clearPollTimer()
    clearCountdownTimer()
    clearTerminalNoticeTimeout()
    document.removeEventListener('visibilitychange', handleVisibilityChange)
    window.removeEventListener(OVERTIME_REQUESTER_ACKNOWLEDGED_EVENT, handleRequesterAcknowledged)
    off('ws:reconnect', handleWsReconnect)
  })

  return {
    ownerRequest,
    requesterRequest,
    ownerOfferText,
    ownerQuantityLine,
    ownerVisible,
    requesterVisible,
    ownerCountdown,
    requesterCountdown,
    ownerCountdownLabel,
    requesterCountdownLabel,
    requesterMessage,
    requesterTerminalNotice,
    loading,
    ownerError,
    approve,
    reject,
    cancel,
    refreshPending,
  }
}
