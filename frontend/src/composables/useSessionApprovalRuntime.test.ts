import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useSessionApprovalRuntime } from './useSessionApprovalRuntime'
import { WS_NOTIFICATION_EVENTS } from '../types/notifications'

const routerPushMock = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
}))

const sessionRuntimeMocks = vi.hoisted(() => {
  const handlers = new Map<string, Array<(payload?: any) => void>>()
  return {
    handlers,
    on: vi.fn((event: string, callback: (payload?: any) => void) => {
      const current = handlers.get(event) ?? []
      current.push(callback)
      handlers.set(event, current)
    }),
    off: vi.fn(),
    apiFetch: vi.fn(),
  }
})

vi.mock('./useWebSocket', () => ({
  useWebSocket: () => ({
    on: sessionRuntimeMocks.on,
    off: sessionRuntimeMocks.off,
  }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: sessionRuntimeMocks.apiFetch,
}))

function emitWsEvent(event: string, payload?: any) {
  for (const handler of sessionRuntimeMocks.handlers.get(event) ?? []) {
    handler(payload)
  }
}

function createRequest(overrides: Record<string, any> = {}) {
  return {
    request_id: 'req-1',
    expires_at: new Date(Date.now() + 5_000).toISOString(),
    device_name: 'Chrome on Android',
    ip_address: '127.0.0.1',
    ...overrides,
  } as any
}

function createRecoveryPrompt(overrides: Record<string, any> = {}) {
  return {
    recovery_id: 'recovery-1',
    user_id: 44,
    user_name: 'علی رضایی',
    prompt_text: 'درخواست بازیابی',
    inline_action_expires_at: new Date(Date.now() + 120_000).toISOString(),
    can_approve: true,
    can_reject: true,
    can_request_identity: true,
    ...overrides,
  } as any
}

function mountRuntime() {
  let runtime!: ReturnType<typeof useSessionApprovalRuntime>
  const Harness = defineComponent({
    setup() {
      runtime = useSessionApprovalRuntime()
      return () => h('div')
    },
  })

  const wrapper = mount(Harness)
  return { wrapper, runtime }
}

describe('useSessionApprovalRuntime', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-14T12:00:00Z'))
    routerPushMock.mockReset()
    sessionRuntimeMocks.handlers.clear()
    sessionRuntimeMocks.on.mockClear()
    sessionRuntimeMocks.off.mockClear()
    sessionRuntimeMocks.apiFetch.mockReset()
    localStorage.clear()
    localStorage.setItem('auth_token', 'token-1')
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      value: 'visible',
    })
  })

  it('fetches pending requests after the initial delay, opens the modal, and falls back to a 120-second countdown', async () => {
    sessionRuntimeMocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([
        createRequest({ expires_at: undefined }),
      ]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
      }))

    const { runtime } = mountRuntime()

    expect(sessionRuntimeMocks.apiFetch).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(sessionRuntimeMocks.apiFetch).toHaveBeenNthCalledWith(1, '/api/sessions/recovery/pending')
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenNthCalledWith(2, '/api/sessions/login-requests/pending')
    expect(runtime.showModal.value).toBe(true)
    expect(runtime.pendingRequest.value?.request_id).toBe('req-1')
    expect(runtime.countdown.value).toBe(120)

    await vi.advanceTimersByTimeAsync(120_000)
    expect(runtime.showModal.value).toBe(false)
    expect(runtime.pendingRequest.value).toBeNull()
    expect(runtime.countdown.value).toBe(0)
  })

  it('ignores realtime requests for non-primary sessions and shows them for the primary session', async () => {
    sessionRuntimeMocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify([
        { is_current: true, is_primary: false },
      ]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([
        { is_current: true, is_primary: true },
      ]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const { runtime } = mountRuntime()

    emitWsEvent(WS_NOTIFICATION_EVENTS.sessionLoginRequest, createRequest({ request_id: 'req-denied' }))
    await flushPromises()

    expect(runtime.showModal.value).toBe(false)

    emitWsEvent(WS_NOTIFICATION_EVENTS.sessionLoginRequest, createRequest({ request_id: 'req-allowed' }))
    await flushPromises()

    expect(runtime.showModal.value).toBe(true)
    expect(runtime.pendingRequest.value?.request_id).toBe('req-allowed')
    expect(runtime.countdown.value).toBeGreaterThan(0)
    expect(runtime.countdown.value).toBeLessThanOrEqual(5)
  })

  it('approves and rejects pending requests, refreshes on reconnect/visibility, and unregisters listeners on unmount', async () => {
    const addEventSpy = vi.spyOn(document, 'addEventListener')
    const removeEventSpy = vi.spyOn(document, 'removeEventListener')
    sessionRuntimeMocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([
        createRequest({ request_id: 'req-visible' }),
      ]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const { wrapper, runtime } = mountRuntime()

    runtime.pendingRequest.value = createRequest({ request_id: 'req-approve' })
    runtime.showModal.value = true
    await runtime.approve()
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/login-requests/req-approve/approve', {
      method: 'POST',
    })
    expect(runtime.showModal.value).toBe(false)
    expect(runtime.loading.value).toBe(false)

    runtime.pendingRequest.value = createRequest({ request_id: 'req-reject' })
    runtime.showModal.value = true
    await runtime.reject()
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/login-requests/req-reject/reject', {
      method: 'POST',
    })
    expect(runtime.showModal.value).toBe(false)

    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/login-requests/pending')
    expect(runtime.pendingRequest.value?.request_id).toBe('req-visible')

    runtime.showModal.value = false
    runtime.pendingRequest.value = null
    emitWsEvent(WS_NOTIFICATION_EVENTS.wsReconnect)
    await flushPromises()
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/login-requests/pending')

    expect(addEventSpy).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
    wrapper.unmount()
    expect(sessionRuntimeMocks.off).toHaveBeenCalledWith(WS_NOTIFICATION_EVENTS.sessionLoginRequest, expect.any(Function))
    expect(sessionRuntimeMocks.off).toHaveBeenCalledWith(WS_NOTIFICATION_EVENTS.wsReconnect, expect.any(Function))
    expect(removeEventSpy).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
  })

  it('shows pending recovery prompts, routes to the chat thread, and calls recovery actions', async () => {
    sessionRuntimeMocks.apiFetch
      .mockResolvedValueOnce(new Response(JSON.stringify([
        createRecoveryPrompt(),
      ]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))

    const { runtime } = mountRuntime()

    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(runtime.pendingRecovery.value?.recovery_id).toBe('recovery-1')
    expect(runtime.showModal.value).toBe(true)

    await runtime.openRecoveryThread()
    expect(routerPushMock).toHaveBeenCalledWith({
      path: '/chat',
      query: {
        user_id: '44',
        user_name: 'علی رضایی',
      },
    })
    expect(runtime.showModal.value).toBe(false)

    runtime.pendingRecovery.value = createRecoveryPrompt({ recovery_id: 'recovery-approve' })
    runtime.showModal.value = true
    await runtime.approveRecovery()
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/recovery/recovery-approve/approve', {
      method: 'POST',
    })

    runtime.pendingRecovery.value = createRecoveryPrompt({ recovery_id: 'recovery-reject' })
    runtime.showModal.value = true
    await runtime.rejectRecovery()
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/recovery/recovery-reject/reject', {
      method: 'POST',
    })

    runtime.pendingRecovery.value = createRecoveryPrompt({ recovery_id: 'recovery-identity' })
    runtime.showModal.value = true
    await runtime.requestRecoveryIdentity()
    expect(sessionRuntimeMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/recovery/recovery-identity/request-identity', {
      method: 'POST',
    })
    expect(runtime.showModal.value).toBe(false)
  })
})