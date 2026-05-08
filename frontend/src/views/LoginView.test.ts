import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const routerPushMock = vi.fn()
const setupExpiryTimerMock = vi.fn()
const pushBackStateMock = vi.fn()
const popBackStateMock = vi.fn()
const clearBackStackMock = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
}))

vi.mock('../utils/auth', () => ({
  setupExpiryTimer: setupExpiryTimerMock,
}))

vi.mock('../composables/useBackButton', () => ({
  pushBackState: pushBackStateMock,
  popBackState: popBackStateMock,
  clearBackStack: clearBackStackMock,
}))

function makeJsonResponse(payload: unknown, ok = true, status = ok ? 200 : 400) {
  return {
    ok,
    status,
    json: async () => payload,
  }
}

describe('LoginView.vue', () => {
  beforeEach(() => {
    vi.resetModules()
    routerPushMock.mockReset()
    setupExpiryTimerMock.mockReset()
    pushBackStateMock.mockReset()
    popBackStateMock.mockReset()
    clearBackStackMock.mockReset()
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn())
    window.matchMedia = vi.fn().mockReturnValue({
      matches: false,
      media: '',
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }) as any
  })

  it('moves to the OTP step after a successful OTP request', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ method: 'telegram' }) as any)
    const LoginView = (await import('./LoginView.vue')).default

    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/request-otp',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(pushBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('کد ارسال شده به 09123456789')
    expect(wrapper.text()).toContain('00:30')
    wrapper.unmount()
  })

  it('switches to waiting approval when verify-otp requires session approval', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-1',
          expires_at: '2026-05-08T08:10:00.000Z',
        }) as any,
      )

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/auth/verify-otp',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(wrapper.text()).toContain('در انتظار تایید')
    expect(wrapper.text()).toContain('درخواست ورود شما به دستگاه اصلی ارسال شد')
    wrapper.unmount()
  })
})