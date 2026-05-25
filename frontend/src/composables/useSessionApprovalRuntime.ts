import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiFetch } from '../utils/auth'
import { useWebSocket } from './useWebSocket'
import {
    type SessionLoginRequestPayload,
    type SessionRecoveryPromptPayload,
    WS_NOTIFICATION_EVENTS,
} from '../types/notifications'

interface SessionSummary {
    is_current?: boolean
    is_primary?: boolean
}

const SESSION_REQUEST_FALLBACK_SECONDS = 120
const INITIAL_PENDING_FETCH_DELAY_MS = 1000

export function useSessionApprovalRuntime() {
    const { on, off } = useWebSocket()
    const router = useRouter()

    const pendingRequest = ref<SessionLoginRequestPayload | null>(null)
    const pendingRecovery = ref<SessionRecoveryPromptPayload | null>(null)
    const showModal = ref(false)
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

    const clearInitialFetchTimeout = () => {
        if (initialFetchTimeout !== null) {
            window.clearTimeout(initialFetchTimeout)
            initialFetchTimeout = null
        }
    }

    const closeModal = () => {
        pendingRequest.value = null
        pendingRecovery.value = null
        showModal.value = false
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
        pendingRecovery.value = null
        pendingRequest.value = request
        showModal.value = true
        startCountdown(request.expires_at)
    }

    const showPendingRecovery = (prompt: SessionRecoveryPromptPayload) => {
        pendingRequest.value = null
        pendingRecovery.value = prompt
        showModal.value = true
        startCountdown(prompt.inline_action_expires_at || prompt.chat_action_expires_at)
    }

    const closeRecoveryPrompt = () => {
        pendingRecovery.value = null
        if (!pendingRequest.value) {
            showModal.value = false
            clearCountdown()
            countdown.value = 0
        }
    }

    const fetchPendingRecoveries = async () => {
        if (!hasAuthToken() || showModal.value) return false

        try {
            const response = await apiFetch('/api/sessions/recovery/pending')
            if (!response.ok) return false

            const data = await response.json()
            if (Array.isArray(data) && data.length > 0) {
                showPendingRecovery(data[0] as SessionRecoveryPromptPayload)
                return true
            }
        } catch {
            // Ignore for non-admin sessions.
        }

        return false
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

    const fetchPendingPrompts = async () => {
        const hasRecoveryPrompt = await fetchPendingRecoveries()
        if (!hasRecoveryPrompt) {
            await fetchPendingRequests()
        }
    }

    const triggerPendingPromptsRefresh = () => {
        clearInitialFetchTimeout()
        void fetchPendingPrompts()
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

    const handleRecoveryPrompt = (prompt: SessionRecoveryPromptPayload) => {
        if (!hasAuthToken()) return

        if (prompt?.visible === false) {
            if (pendingRecovery.value?.recovery_id === prompt.recovery_id) {
                closeRecoveryPrompt()
            }
            return
        }

        showPendingRecovery(prompt)
    }

    const approve = async () => {
        const requestId = pendingRequest.value?.request_id
        if (!requestId) return

        loading.value = true
        try {
            await apiFetch(`/api/sessions/login-requests/${requestId}/approve`, {
                method: 'POST'
            })
            pendingRequest.value = null
            if (!pendingRecovery.value) {
                showModal.value = false
                clearCountdown()
                countdown.value = 0
            }
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
            pendingRequest.value = null
            if (!pendingRecovery.value) {
                showModal.value = false
                clearCountdown()
                countdown.value = 0
            }
        } catch (error) {
            console.error('Reject error:', error)
        } finally {
            loading.value = false
        }
    }

    const approveRecovery = async () => {
        const recoveryId = pendingRecovery.value?.recovery_id
        if (!recoveryId) return

        loading.value = true
        try {
            await apiFetch(`/api/sessions/recovery/${recoveryId}/approve`, {
                method: 'POST',
            })
            closeRecoveryPrompt()
        } catch (error) {
            console.error('Recovery approve error:', error)
        } finally {
            loading.value = false
        }
    }

    const rejectRecovery = async () => {
        const recoveryId = pendingRecovery.value?.recovery_id
        if (!recoveryId) return

        loading.value = true
        try {
            await apiFetch(`/api/sessions/recovery/${recoveryId}/reject`, {
                method: 'POST',
            })
            closeRecoveryPrompt()
        } catch (error) {
            console.error('Recovery reject error:', error)
        } finally {
            loading.value = false
        }
    }

    const requestRecoveryIdentity = async () => {
        const recoveryId = pendingRecovery.value?.recovery_id
        if (!recoveryId) return

        loading.value = true
        try {
            await apiFetch(`/api/sessions/recovery/${recoveryId}/request-identity`, {
                method: 'POST',
            })
            closeRecoveryPrompt()
        } catch (error) {
            console.error('Recovery request-identity error:', error)
        } finally {
            loading.value = false
        }
    }

    const openRecoveryThread = async () => {
        const targetUserId = pendingRecovery.value?.user_id
        if (!targetUserId) return

        await router.push({
            path: '/chat',
            query: {
                user_id: String(targetUserId),
                user_name: pendingRecovery.value?.user_name || undefined,
            },
        })
        closeRecoveryPrompt()
    }

    const handleVisibilityChange = () => {
        if (document.visibilityState === 'visible') {
            triggerPendingPromptsRefresh()
        }
    }

    onMounted(() => {
        if (hasAuthToken()) {
            initialFetchTimeout = window.setTimeout(() => {
                initialFetchTimeout = null
                void fetchPendingPrompts()
            }, INITIAL_PENDING_FETCH_DELAY_MS)
        }

        on(WS_NOTIFICATION_EVENTS.sessionLoginRequest, handleLoginRequest)
        on(WS_NOTIFICATION_EVENTS.sessionRecoveryUpdate, handleRecoveryPrompt)
        on(WS_NOTIFICATION_EVENTS.wsReconnect, triggerPendingPromptsRefresh)
        document.addEventListener('visibilitychange', handleVisibilityChange)
    })

    onBeforeUnmount(() => {
        off(WS_NOTIFICATION_EVENTS.sessionLoginRequest, handleLoginRequest)
        off(WS_NOTIFICATION_EVENTS.sessionRecoveryUpdate, handleRecoveryPrompt)
        off(WS_NOTIFICATION_EVENTS.wsReconnect, triggerPendingPromptsRefresh)
        document.removeEventListener('visibilitychange', handleVisibilityChange)

        clearInitialFetchTimeout()

        clearCountdown()
    })

    return {
        approve,
        approveRecovery,
        countdown,
        loading,
        openRecoveryThread,
        pendingRecovery,
        pendingRequest,
        reject,
        rejectRecovery,
        requestRecoveryIdentity,
        showModal,
    }
}