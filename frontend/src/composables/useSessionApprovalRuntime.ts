import { onBeforeUnmount, onMounted, ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { useWebSocket } from './useWebSocket'
import { type SessionLoginRequestPayload, WS_NOTIFICATION_EVENTS } from '../types/notifications'

interface SessionSummary {
    is_current?: boolean
    is_primary?: boolean
}

const SESSION_REQUEST_FALLBACK_SECONDS = 120
const INITIAL_PENDING_FETCH_DELAY_MS = 1000

export function useSessionApprovalRuntime() {
    const { on, off } = useWebSocket()

    const showModal = ref(false)
    const pendingRequest = ref<SessionLoginRequestPayload | null>(null)
    const loading = ref(false)
    const countdown = ref(0)

    let countdownInterval: number | null = null
    let initialFetchTimeout: number | null = null

    const hasAuthToken = () => Boolean(localStorage.getItem('auth_token'))

    const clearCountdown = () => {
        if (countdownInterval !== null) {
            window.clearInterval(countdownInterval)
            countdownInterval = null
        }
    }

    const closeModal = () => {
        showModal.value = false
        pendingRequest.value = null
        clearCountdown()
        countdown.value = 0
    }

    const startCountdown = (expiresAt?: string) => {
        clearCountdown()

        if (expiresAt) {
            const expires = new Date(expiresAt).getTime()
            const now = Date.now()
            countdown.value = Math.max(0, Math.floor((expires - now) / 1000))
        } else {
            countdown.value = SESSION_REQUEST_FALLBACK_SECONDS
        }

        if (countdown.value <= 0) {
            closeModal()
            return
        }

        countdownInterval = window.setInterval(() => {
            countdown.value -= 1
            if (countdown.value <= 0) {
                closeModal()
            }
        }, 1000)
    }

    const showPendingRequest = (request: SessionLoginRequestPayload) => {
        pendingRequest.value = request
        showModal.value = true
        startCountdown(request.expires_at)
    }

    const fetchPendingRequests = async () => {
        if (!hasAuthToken() || showModal.value) return

        try {
            const response = await apiFetch('/api/sessions/login-requests/pending')
            if (!response.ok) return

            const data = await response.json()
            if (Array.isArray(data) && data.length > 0) {
                showPendingRequest(data[0] as SessionLoginRequestPayload)
            }
        } catch {
            // Ignore here; the endpoint can fail for non-primary devices.
        }
    }

    const shouldDisplayRealtimeRequest = async () => {
        if (!hasAuthToken()) return false

        try {
            const response = await apiFetch('/api/sessions/active')
            if (!response.ok) return true

            const sessions = await response.json()
            const currentSession = Array.isArray(sessions)
                ? (sessions as SessionSummary[]).find((session) => session?.is_current)
                : undefined

            return currentSession ? Boolean(currentSession.is_primary) : true
        } catch {
            return true
        }
    }

    const handleLoginRequest = async (request: SessionLoginRequestPayload) => {
        if (!(await shouldDisplayRealtimeRequest())) return
        showPendingRequest(request)
    }

    const approve = async () => {
        const requestId = pendingRequest.value?.request_id
        if (!requestId) return

        loading.value = true
        try {
            await apiFetch(`/api/sessions/login-requests/${requestId}/approve`, {
                method: 'POST'
            })
            closeModal()
        } catch (error) {
            console.error('Approve error:', error)
        } finally {
            loading.value = false
        }
    }

    const reject = async () => {
        const requestId = pendingRequest.value?.request_id
        if (!requestId) return

        loading.value = true
        try {
            await apiFetch(`/api/sessions/login-requests/${requestId}/reject`, {
                method: 'POST'
            })
            closeModal()
        } catch (error) {
            console.error('Reject error:', error)
        } finally {
            loading.value = false
        }
    }

    const handleVisibilityChange = () => {
        if (document.visibilityState === 'visible') {
            void fetchPendingRequests()
        }
    }

    onMounted(() => {
        if (hasAuthToken()) {
            initialFetchTimeout = window.setTimeout(() => {
                void fetchPendingRequests()
            }, INITIAL_PENDING_FETCH_DELAY_MS)
        }

        on(WS_NOTIFICATION_EVENTS.sessionLoginRequest, handleLoginRequest)
        on(WS_NOTIFICATION_EVENTS.wsReconnect, fetchPendingRequests)
        document.addEventListener('visibilitychange', handleVisibilityChange)
    })

    onBeforeUnmount(() => {
        off(WS_NOTIFICATION_EVENTS.sessionLoginRequest, handleLoginRequest)
        off(WS_NOTIFICATION_EVENTS.wsReconnect, fetchPendingRequests)
        document.removeEventListener('visibilitychange', handleVisibilityChange)

        if (initialFetchTimeout !== null) {
            window.clearTimeout(initialFetchTimeout)
            initialFetchTimeout = null
        }

        clearCountdown()
    })

    return {
        approve,
        countdown,
        loading,
        pendingRequest,
        reject,
        showModal,
    }
}