import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const routerPushMock = vi.fn()
const setupExpiryTimerMock = vi.fn()
const apiFetchMock = vi.fn()
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
  apiFetch: apiFetchMock,
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

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  if (!button) {
    throw new Error(`Button not found: ${text}`)
  }
  return button
}

describe('LoginView.vue', () => {
  beforeEach(() => {
    vi.resetModules()
    routerPushMock.mockReset()
    setupExpiryTimerMock.mockReset()
    apiFetchMock.mockReset()
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
    ;(window as any).deferredPrompt = null
  })

  afterEach(() => {
    vi.useRealTimers()
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

  it('primes the current user cache before routing after a successful OTP verification', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          access_token: 'access-token',
          refresh_token: 'refresh-token',
          token_type: 'bearer',
        }) as any,
      )
    apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 1,
        role: 'مدیر ارشد',
        full_name: 'محسن',
        account_name: 'mohsen',
      }) as any,
    )

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(JSON.parse(localStorage.getItem('current_user_summary') || '{}')).toMatchObject({
      role: 'مدیر ارشد',
      account_name: 'mohsen',
    })
    expect(routerPushMock).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })

  it('shows validation errors, enters the OTP step on rate limiting, and lets the user go back to the mobile step', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse({ detail: 'ارسال مجدد پس از 45 ثانیه' }, false, 429) as any,
    )

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('button.btn-primary').trigger('click')
    expect(wrapper.text()).toContain('شماره موبایل معتبر نیست')

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/request-otp',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(wrapper.text()).toContain('کد ارسال شده به 09123456789')
    expect(wrapper.text()).toContain('00:45')

    vi.advanceTimersByTime(1000)
    await flushPromises()
    expect(wrapper.text()).toContain('00:44')

    await findButtonByText(wrapper, 'ویرایش شماره').trigger('click')
    expect(popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('input[type="tel"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('resends OTP through SMS after a Telegram delivery and handles rejected approval polling', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'telegram' }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ expires_in: 60 }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-2',
          expires_at: '2026-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(makeJsonResponse({ status: 'rejected' }) as any)

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()

    vi.advanceTimersByTime(30000)
    await flushPromises()
    await findButtonByText(wrapper, 'ارسال مجدد کد').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/auth/resend-otp-sms',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(wrapper.text()).toContain('01:00')

    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    vi.advanceTimersByTime(2000)
    await flushPromises()

    expect(fetchMock).toHaveBeenLastCalledWith('/api/sessions/login-requests/req-2/status')
    expect(wrapper.text()).toContain('درخواست ورود شما رد شد.')
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('supports the developer quick-login flow and clears suspended refresh tokens', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse({
        access_token: 'dev-access',
        refresh_token: 'dev-refresh',
      }) as any,
    )
    apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        id: 10,
        role: 'مدیر ارشد',
        full_name: 'دولوپر',
        account_name: 'dev',
      }) as any,
    )
    localStorage.setItem('suspended_refresh_token', 'stale-token')

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await findButtonByText(wrapper, 'ورود سریع ۱ ساله').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/auth/dev-login', { method: 'POST' })
    expect(localStorage.getItem('auth_token')).toBe('dev-access')
    expect(localStorage.getItem('refresh_token')).toBe('dev-refresh')
    expect(localStorage.getItem('suspended_refresh_token')).toBeNull()
    expect(setupExpiryTimerMock).toHaveBeenCalledTimes(1)
    expect(clearBackStackMock).toHaveBeenCalledTimes(1)
    expect(routerPushMock).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })

  it('handles the PWA install prompt flow and falls back to alerting when no prompt is available', async () => {
    const alertMock = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const promptEvent = {
      preventDefault: vi.fn(),
      prompt: vi.fn(),
      userChoice: Promise.resolve({ outcome: 'accepted' }),
    }

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    ;(window as any).deferredPrompt = promptEvent
    window.dispatchEvent(new Event('pwa-install-ready'))
    await flushPromises()

    await findButtonByText(wrapper, 'نصب اپلیکیشن').trigger('click')
    await flushPromises()

    expect(promptEvent.prompt).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')

    window.dispatchEvent(new Event('appinstalled'))
    await flushPromises()
    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')

    ;(window as any).deferredPrompt = null
    const fallbackWrapper = mount(LoginView)
    await findButtonByText(fallbackWrapper, 'نصب اپلیکیشن').trigger('click')
    expect(alertMock).toHaveBeenCalled()

    fallbackWrapper.unmount()
    wrapper.unmount()
  })
})