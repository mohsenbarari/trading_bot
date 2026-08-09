import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const shellMocks = vi.hoisted(() => ({
  on: vi.fn(),
  off: vi.fn(),
  connect: vi.fn(),
  sendPresenceUpdate: vi.fn(),
  useNotificationRuntime: vi.fn(),
  initChatUploadBackground: vi.fn(async () => {}),
  hasPendingUploadResumeHint: vi.fn(() => false),
  setUploadResumeHint: vi.fn(),
  initChatDocumentDownloadBackground: vi.fn(async () => {}),
  hasPendingDocumentDownloadResumeHint: vi.fn(() => false),
  setDocumentDownloadResumeHint: vi.fn(),
  initChatFileDebugOverlay: vi.fn(),
  setupExpiryTimer: vi.fn(),
  apiFetch: vi.fn(async () => ({})),
  route: { path: '/' },
}))

vi.mock('../composables/useWebSocket', () => ({
  useWebSocket: () => ({
    on: shellMocks.on,
    off: shellMocks.off,
    connect: shellMocks.connect,
    sendPresenceUpdate: shellMocks.sendPresenceUpdate,
  }),
}))

vi.mock('vue-router', () => ({
  useRoute: () => shellMocks.route,
}))

vi.mock('../composables/useNotificationRuntime', () => ({
  useNotificationRuntime: shellMocks.useNotificationRuntime,
}))

vi.mock('../services/chatTransferResumeHints', () => ({
  hasPendingDocumentDownloadResumeHint: shellMocks.hasPendingDocumentDownloadResumeHint,
  hasPendingUploadResumeHint: shellMocks.hasPendingUploadResumeHint,
  setDocumentDownloadResumeHint: shellMocks.setDocumentDownloadResumeHint,
  setUploadResumeHint: shellMocks.setUploadResumeHint,
}))

vi.mock('../services/chatUploadBackground', () => ({
  initChatUploadBackground: shellMocks.initChatUploadBackground,
}))

vi.mock('../services/chatDocumentDownloadBackground', () => ({
  initChatDocumentDownloadBackground: shellMocks.initChatDocumentDownloadBackground,
}))

vi.mock('../composables/chat/useChatFileHandler', () => ({
  initChatFileDebugOverlay: shellMocks.initChatFileDebugOverlay,
}))

vi.mock('../utils/auth', () => ({
  setupExpiryTimer: shellMocks.setupExpiryTimer,
  apiFetch: shellMocks.apiFetch,
}))

describe('AppAuthenticatedShell.vue', () => {
  beforeEach(() => {
    shellMocks.on.mockReset()
    shellMocks.off.mockReset()
    shellMocks.connect.mockReset()
    shellMocks.sendPresenceUpdate.mockReset()
    shellMocks.useNotificationRuntime.mockReset()
    shellMocks.initChatUploadBackground.mockClear()
    shellMocks.hasPendingUploadResumeHint.mockReset()
    shellMocks.hasPendingUploadResumeHint.mockReturnValue(false)
    shellMocks.setUploadResumeHint.mockReset()
    shellMocks.initChatDocumentDownloadBackground.mockClear()
    shellMocks.hasPendingDocumentDownloadResumeHint.mockReset()
    shellMocks.hasPendingDocumentDownloadResumeHint.mockReturnValue(false)
    shellMocks.setDocumentDownloadResumeHint.mockReset()
    shellMocks.initChatFileDebugOverlay.mockClear()
    shellMocks.setupExpiryTimer.mockClear()
    shellMocks.apiFetch.mockReset()
    shellMocks.apiFetch.mockResolvedValue({})
    shellMocks.route.path = '/'
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
    delete (window as any).deferredPrompt
  })

  it('skips eager background recovery when no pending transfer hint exists and forwards ensureSessionValidation', async () => {
    localStorage.setItem('auth_token', 'jwt')
    localStorage.setItem('refresh_token', 'refresh-token')
    shellMocks.useNotificationRuntime.mockImplementation(({ ensureSessionValidation }) => {
      ;(shellMocks.useNotificationRuntime as any).capturedEnsure = ensureSessionValidation
    })

    const AppAuthenticatedShell = (await import('./AppAuthenticatedShell.vue')).default
    const wrapper = mount(AppAuthenticatedShell, {
      props: { v2Scope: true },
      global: {
        stubs: {
          BottomNav: true,
          SessionApprovalModal: true,
          AppToasts: true,
        },
      },
    })

    expect(shellMocks.initChatUploadBackground).not.toHaveBeenCalled()
    expect(shellMocks.initChatDocumentDownloadBackground).not.toHaveBeenCalled()
    expect(shellMocks.useNotificationRuntime).toHaveBeenCalledWith({
      connect: shellMocks.connect,
      on: shellMocks.on,
      off: shellMocks.off,
      ensureSessionValidation: expect.any(Function),
    })
    expect(shellMocks.setupExpiryTimer).toHaveBeenCalledTimes(1)
    expect(shellMocks.initChatFileDebugOverlay).toHaveBeenCalledTimes(1)
    expect(shellMocks.on).toHaveBeenCalledWith('ws:reconnect', expect.any(Function))
    expect(shellMocks.sendPresenceUpdate).toHaveBeenCalledWith('/', true)
    expect(wrapper.findComponent({ name: 'SessionApprovalModal' }).props('v2Portal')).toBe(true)
    expect(wrapper.findComponent({ name: 'BottomNav' }).props('v2Scope')).toBe(true)

    const ensureSessionValidation = (shellMocks.useNotificationRuntime as any).capturedEnsure
    await ensureSessionValidation()
    expect(shellMocks.apiFetch).toHaveBeenCalledWith('/api/sessions/verify', {
      method: 'POST',
      body: JSON.stringify({ refresh_token: 'refresh-token' }),
    })

    wrapper.unmount()
    expect(shellMocks.off).toHaveBeenCalledWith('ws:reconnect', expect.any(Function))
    expect(shellMocks.sendPresenceUpdate).toHaveBeenLastCalledWith('/', false)
  }, 15_000)

  it('starts background recovery immediately when a pending transfer hint exists', async () => {
    shellMocks.hasPendingUploadResumeHint.mockReturnValue(true)
    shellMocks.hasPendingDocumentDownloadResumeHint.mockReturnValue(true)

    const AppAuthenticatedShell = (await import('./AppAuthenticatedShell.vue')).default
    mount(AppAuthenticatedShell, {
      global: {
        stubs: {
          BottomNav: true,
          SessionApprovalModal: true,
          AppToasts: true,
        },
      },
    })

    await vi.waitFor(() => {
      expect(shellMocks.initChatUploadBackground).toHaveBeenCalledTimes(1)
      expect(shellMocks.initChatDocumentDownloadBackground).toHaveBeenCalledTimes(1)
    })
  })

  it('keeps security layers but omits BottomNav in focused authenticated mode', async () => {
    const AppAuthenticatedShell = (await import('./AppAuthenticatedShell.vue')).default
    const wrapper = mount(AppAuthenticatedShell, {
      props: { v2Scope: true, showDailyNavigation: false },
      global: {
        stubs: {
          BottomNav: true,
          SessionApprovalModal: true,
          AppToasts: true,
        },
      },
    })

    expect(wrapper.findComponent({ name: 'BottomNav' }).exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'SessionApprovalModal' }).props('v2Portal')).toBe(true)
    expect(wrapper.findComponent({ name: 'AppToasts' }).props('v2Scope')).toBe(true)
  })

  it('skips session verification without a refresh token and swallows verification failures', async () => {
    shellMocks.useNotificationRuntime.mockImplementation(({ ensureSessionValidation }) => {
      ;(shellMocks.useNotificationRuntime as any).capturedEnsure = ensureSessionValidation
    })
    shellMocks.apiFetch.mockRejectedValueOnce(new Error('unauthorized'))

    const AppAuthenticatedShell = (await import('./AppAuthenticatedShell.vue')).default
    mount(AppAuthenticatedShell, {
      global: {
        stubs: {
          BottomNav: true,
          SessionApprovalModal: true,
          AppToasts: true,
        },
      },
    })

    const ensureSessionValidation = (shellMocks.useNotificationRuntime as any).capturedEnsure
    await ensureSessionValidation()
    expect(shellMocks.apiFetch).not.toHaveBeenCalled()

    localStorage.setItem('refresh_token', 'refresh-token')
    await expect(ensureSessionValidation()).resolves.toBeUndefined()
  })
})
