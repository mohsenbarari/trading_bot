import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import LoginView from './LoginView.vue'

const {
  routerPushMock,
  routeMock,
  setupExpiryTimerMock,
  apiFetchMock,
  pushBackStateMock,
  popBackStateMock,
  clearBackStackMock,
} = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routeMock: { query: {} as Record<string, string> },
  setupExpiryTimerMock: vi.fn(),
  apiFetchMock: vi.fn(),
  pushBackStateMock: vi.fn(),
  popBackStateMock: vi.fn(),
  clearBackStackMock: vi.fn(),
}))
const originalOTPCredential = (window as any).OTPCredential
const originalNavigatorCredentials = navigator.credentials
const originalWindowLocation = window.location

vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
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

function makeFetchResponse(payload: unknown, ok = true, status = ok ? 200 : 400): Response {
  return makeJsonResponse(payload, ok, status) as unknown as Response
}

interface LoginViewTestVm {
  step:
    | 'mobile'
    | 'otp'
    | 'waiting_approval'
    | 'recovery_waiting'
    | 'recovery_identity'
    | 'recovery_submitted'
    | 'recovery_approved'
    | 'recovery_rejected'
    | 'recovery_expired'
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  if (!button) {
    throw new Error(`Button not found: ${text}`)
  }
  return button
}

async function requestOtpFromMobileStep(wrapper: ReturnType<typeof mount>) {
  await findButtonByText(wrapper, 'دریافت کد تأیید').trigger('click')
  await flushPromises()
}

describe('LoginView.vue', () => {
  beforeEach(() => {
    routerPushMock.mockReset()
    routeMock.query = {}
    setupExpiryTimerMock.mockReset()
    apiFetchMock.mockReset()
    pushBackStateMock.mockReset()
    popBackStateMock.mockReset()
    clearBackStackMock.mockReset()
    localStorage.clear()
    sessionStorage.clear()
    vi.stubGlobal('fetch', vi.fn())
    apiFetchMock.mockImplementation((...args: Parameters<typeof fetch>) => fetch(...args) as any)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
    if (typeof originalOTPCredential === 'undefined') {
      Reflect.deleteProperty(window as any, 'OTPCredential')
    } else {
      Object.defineProperty(window, 'OTPCredential', {
        configurable: true,
        value: originalOTPCredential,
      })
    }

    if (typeof originalNavigatorCredentials === 'undefined') {
      Reflect.deleteProperty(navigator, 'credentials')
    } else {
      Object.defineProperty(navigator, 'credentials', {
        configurable: true,
        value: originalNavigatorCredentials,
      })
    }

    Object.defineProperty(window, 'location', { configurable: true, value: originalWindowLocation })
  })

  it('keeps the primary action below the form so login reads as an installed app', () => {
    // Static import avoids SFC recompile; attachTo leaked the tree and is unused here.
    const wrapper = mount(LoginView)

    expect(wrapper.get('.ui-v2-auth-login-body').text()).toContain(
      'کد ابتدا در تلگرام و در صورت نیاز با پیامک می‌آید.',
    )
    expect(wrapper.get('.ui-v2-auth-login-actions').text()).toContain('دریافت کد تأیید')
    expect(wrapper.get('.ui-v2-public-header').text()).toContain('سامانه معاملات')
    expect(wrapper.text()).not.toContain('ورود سریع ۱ ساله')
    wrapper.unmount()
  })

  it('moves to the OTP step after a successful OTP request', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ method: 'telegram' }) as any)
    const wrapper = mount(LoginView, { attachTo: document.body })
    expect(wrapper.findAll('main')).toHaveLength(1)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/request-otp',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(pushBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('کد ارسال‌شده را وارد کنید.')
    expect(wrapper.get('.ui-v2-auth-login-meta bdi').text()).toBe('0912****789')
    expect(wrapper.html()).not.toContain('09123456789')
    expect(wrapper.text()).toContain('00:30')
    wrapper.unmount()
  }, 10000)

  it('shows and consumes the fixed local logout receipt after the hard login redirect', async () => {
    sessionStorage.setItem('stage4_local_logout_result_v1', 'local-only')
    const wrapper = mount(LoginView)

    expect(wrapper.get('[data-local-logout-notice]').text()).toContain(
      'اطلاعات ورود این دستگاه پاک شد',
    )
    expect(wrapper.get('[data-local-logout-notice]').text()).toContain('تأیید سرور دریافت نشد')
    expect(sessionStorage.getItem('stage4_local_logout_result_v1')).toBeNull()
    wrapper.unmount()
  })

  it('accepts the authoritative staging-log OTP delivery receipt', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse({
        detail: 'کد تایید در لاگ staging ثبت شد',
        method: 'log',
        expires_in: 120,
      }) as any,
    )
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09125555555')
    await requestOtpFromMobileStep(wrapper)

    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('کد ارسال‌شده را وارد کنید.')
    expect(wrapper.get('.ui-v2-auth-login-meta bdi').text()).toBe('0912****555')
    expect(wrapper.text()).toContain('02:00 تا ارسال مجدد')
    wrapper.unmount()
  })

  it('ignores an OTP request response after the submitted mobile context changes', async () => {
    const fetchMock = vi.mocked(fetch)
    let resolveRequest!: (response: unknown) => void
    fetchMock.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveRequest = resolve
      }) as any,
    )
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09121111111')
    void findButtonByText(wrapper, 'دریافت کد تأیید').trigger('click')
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await wrapper.get('input[type="tel"]').setValue('09122222222')
    resolveRequest(makeJsonResponse({ method: 'sms' }))
    await flushPromises()

    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)
    expect((wrapper.get('input[type="tel"]').element as HTMLInputElement).value).toBe('09122222222')
    expect(pushBackStateMock).not.toHaveBeenCalled()
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      mobile_number: '09121111111',
    })
    wrapper.unmount()
  })

  it('does not apply a pending verify response after edit-number invalidates the OTP context', async () => {
    const fetchMock = vi.mocked(fetch)
    let resolveVerify!: (response: unknown) => void
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveVerify = resolve
      }) as any,
    )
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123333333')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await findButtonByText(wrapper, 'ویرایش شماره').trigger('click')
    resolveVerify(
      makeJsonResponse({ access_token: 'stale-access', refresh_token: 'stale-refresh' }),
    )
    await flushPromises()

    expect(wrapper.find('input[type="tel"]').exists()).toBe(true)
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(routerPushMock).not.toHaveBeenCalled()
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

    const wrapper = mount(LoginView, { attachTo: document.body })

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/auth/verify-otp',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(wrapper.text()).toContain('در انتظار تأیید')
    expect(wrapper.text()).toContain('درخواست ورود به دستگاه اصلی شما ارسال شد')
    expect(wrapper.get('[data-auth-status-step]').attributes('tabindex')).toBe('-1')
    expect(document.activeElement).toBe(wrapper.get('[data-auth-status-step]').element)
    wrapper.unmount()
  })

  it('moves focus to every approval and recovery status transition', async () => {
    const wrapper = mount(LoginView, { attachTo: document.body })
    const vm = wrapper.vm as unknown as LoginViewTestVm

    for (const status of [
      'waiting_approval',
      'recovery_waiting',
      'recovery_identity',
      'recovery_submitted',
      'recovery_approved',
      'recovery_rejected',
      'recovery_expired',
    ]) {
      vm.step = status
      await flushPromises()
      const container = wrapper.get('[data-auth-status-step]')
      expect(container.attributes('tabindex')).toBe('-1')
      expect(document.activeElement).toBe(container.element)
    }

    wrapper.unmount()
  })

  it('routes invited users to registration completion after OTP verification', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'registration_required',
        }) as any,
      )

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()

    expect(clearBackStackMock).toHaveBeenCalled()
    expect(routerPushMock).toHaveBeenCalledWith({ name: 'web-register' })
    expect(String(routerPushMock.mock.calls[0]?.[0])).not.toMatch(/REG-|registration_token/)
    expect(wrapper.html()).not.toMatch(/REG-|registration_token/)
    expect(JSON.stringify(sessionStorage)).not.toMatch(/REG-|registration_token/)
    expect(JSON.stringify(localStorage)).not.toMatch(/REG-|registration_token/)
    expect(localStorage.getItem('auth_token')).toBeNull()
    wrapper.unmount()
  })

  it('keeps the OTP context and offers a real retry when registration navigation fails', async () => {
    routerPushMock
      .mockRejectedValueOnce(new Error('registration chunk unavailable'))
      .mockResolvedValueOnce(undefined)
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeFetchResponse({ method: 'sms' }))
      .mockResolvedValueOnce(makeFetchResponse({ status: 'registration_required' }))
      .mockResolvedValueOnce(makeFetchResponse({ status: 'registration_required' }))

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    const codeInput = wrapper.get('input[autocomplete="one-time-code"]')
    await codeInput.setValue('12345')
    await flushPromises()

    expect(wrapper.text()).toContain('ادامه ثبت‌نام اکنون ممکن نشد. دوباره تلاش کنید.')
    expect((codeInput.element as HTMLInputElement).value).toBe('12345')
    expect(clearBackStackMock).not.toHaveBeenCalled()

    await findButtonByText(wrapper, 'تأیید و ادامه').trigger('click')
    await flushPromises()

    expect(routerPushMock).toHaveBeenCalledTimes(2)
    expect(clearBackStackMock).toHaveBeenCalledTimes(1)
    expect((codeInput.element as HTMLInputElement).value).toBe('')
    expect(sessionStorage.getItem('login_otp_attempt_v1')).toBeNull()
    wrapper.unmount()
  })

  it('recovers a committed registration cookie when the verify response body is lost', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeFetchResponse({ method: 'sms' }))
      .mockRejectedValueOnce(new TypeError('response lost'))
      .mockResolvedValueOnce(
        makeFetchResponse({
          kind: 'registration',
          account_name: 'user1',
          mobile_number: '0912****789',
          role: 'عادی',
          progress: 'otp_verified',
          requires_otp: false,
        }),
      )

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('54321')
    await flushPromises()
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/auth/registration-context',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
    expect(routerPushMock).toHaveBeenCalledWith({ name: 'web-register' })
    expect(clearBackStackMock).toHaveBeenCalled()
    expect(sessionStorage.getItem('login_otp_attempt_v1')).toBeNull()
    expect(wrapper.html()).not.toContain('54321')
    wrapper.unmount()
  })

  it('fails closed when neither the verify response nor a registration cookie arrived', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeFetchResponse({ method: 'sms' }))
      .mockRejectedValueOnce(new TypeError('response lost'))
      .mockResolvedValueOnce(
        makeFetchResponse({ detail: 'registration context missing' }, false, 410),
      )

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('54321')
    await flushPromises()
    await flushPromises()

    expect(routerPushMock).not.toHaveBeenCalledWith({ name: 'web-register' })
    expect(wrapper.text()).toContain('ارتباط با سرور برقرار نشد.')
    expect((wrapper.vm as unknown as LoginViewTestVm).step).toBe('otp')
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
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(
          makeJsonResponse({
            id: 1,
            role: 'مدیر ارشد',
            full_name: 'محسن',
            account_name: 'mohsen',
          }) as any,
        )
      }
      return fetch(url, options) as any
    })

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
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

  it('retains the intended route and OTP state when authenticated navigation fails, then retries without replaying OTP', async () => {
    sessionStorage.setItem(
      'auth_intended_route_v1',
      JSON.stringify({
        version: 1,
        path: '/profile?tab=security',
        createdAt: Date.now(),
      }),
    )
    routerPushMock
      .mockResolvedValueOnce({ type: 4, to: '/profile?tab=security' })
      .mockResolvedValueOnce(undefined)
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeFetchResponse({ method: 'sms' })).mockResolvedValueOnce(
      makeFetchResponse({
        access_token: 'retry-access',
        refresh_token: 'retry-refresh',
      }),
    )
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(
          makeFetchResponse({
            id: 7,
            role: 'عادی',
            full_name: 'کاربر',
            account_name: 'retry-user',
          }),
        )
      }
      return fetch(url, options)
    })

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    const codeInput = wrapper.get('input[autocomplete="one-time-code"]')
    await codeInput.setValue('12345')
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('انتقال به صفحه بعد ممکن نشد')
    expect((codeInput.element as HTMLInputElement).value).toBe('12345')
    expect(wrapper.get('input[autocomplete="one-time-code"]').attributes('disabled')).toBeDefined()
    expect(findButtonByText(wrapper, 'ویرایش شماره').attributes('disabled')).toBeDefined()
    expect(findButtonByText(wrapper, 'ورود با حساب دیگر').exists()).toBe(true)
    expect(sessionStorage.getItem('auth_intended_route_v1')).not.toBeNull()
    expect(clearBackStackMock).not.toHaveBeenCalled()
    expect(setupExpiryTimerMock).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(2)

    await findButtonByText(wrapper, 'ادامه ورود').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(routerPushMock).toHaveBeenNthCalledWith(1, '/profile?tab=security')
    expect(routerPushMock).toHaveBeenNthCalledWith(2, '/profile?tab=security')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(sessionStorage.getItem('auth_intended_route_v1')).toBeNull()
    expect((codeInput.element as HTMLInputElement).value).toBe('')
    expect(clearBackStackMock).toHaveBeenCalledTimes(1)
    expect(setupExpiryTimerMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('requires an explicit logout before starting a different identity after navigation fails', async () => {
    routerPushMock.mockRejectedValueOnce(new Error('home unavailable'))
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeFetchResponse({ method: 'sms' }))
      .mockResolvedValueOnce(
        makeFetchResponse({ access_token: 'old-access', refresh_token: 'old-refresh' }),
      )
      .mockResolvedValueOnce(makeFetchResponse({ method: 'sms' }))
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(makeFetchResponse({ id: 8, role: 'عادی', account_name: 'old-user' }))
      }
      return fetch(url, options)
    })

    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09121111111')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await flushPromises()

    expect(localStorage.getItem('auth_token')).toBe('old-access')
    const physicalBackHandler = pushBackStateMock.mock.calls[0]?.[0] as (() => void) | undefined
    expect(physicalBackHandler).toBeTypeOf('function')
    physicalBackHandler?.()
    await flushPromises()
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    expect(localStorage.getItem('auth_token')).toBe('old-access')

    await findButtonByText(wrapper, 'ورود با حساب دیگر').trigger('click')
    await flushPromises()

    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(localStorage.getItem('refresh_token')).toBeNull()
    expect(localStorage.getItem('current_user_summary')).toBeNull()
    expect(wrapper.find('input[type="tel"]').exists()).toBe(true)

    await wrapper.get('input[type="tel"]').setValue('09122222222')
    await requestOtpFromMobileStep(wrapper)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(routerPushMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('shows validation errors, enters the OTP step on rate limiting, and lets the user go back to the mobile step', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T02:00:00.000Z'))
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse(
        {
          detail: 'کد قبلی هنوز معتبر است. لطفاً صبر کنید.',
          code: 'otp_active',
          otp_request_id: '23bc1f50-c3ed-49f7-8dc0-c736a968448c',
          method: 'sms',
          retry_after: 45,
          expires_in: 45,
          expires_at: '2026-07-12T02:00:45.000Z',
        },
        false,
        429,
      ) as any,
    )

    const wrapper = mount(LoginView)

    await wrapper.get('button.ui-button').trigger('click')
    expect(wrapper.text()).toContain('شماره موبایل معتبر نیست')

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/request-otp',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(wrapper.text()).toContain('کد ارسال‌شده را وارد کنید.')
    expect(wrapper.get('.ui-v2-auth-login-meta bdi').text()).toBe('0912****789')
    expect(wrapper.text()).toContain('00:45')

    vi.advanceTimersByTime(1000)
    await flushPromises()
    expect(wrapper.text()).toContain('00:44')

    await findButtonByText(wrapper, 'ویرایش شماره').trigger('click')
    expect(popBackStateMock).toHaveBeenCalledTimes(1)
    expect(wrapper.find('input[type="tel"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('waits for automatic SMS after Telegram delivery without exposing a manual resend', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(
        makeJsonResponse({
          otp_request_id: '6afdb938-2061-43a6-a35f-37d5db9d9e2c',
          method: 'telegram',
          expires_in: 120,
          expires_at: new Date(Date.now() + 120_000).toISOString(),
          sms_fallback_in: 40,
          sms_fallback_at: new Date(Date.now() + 40_000).toISOString(),
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-2',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(makeJsonResponse({ status: 'rejected' }) as any)

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)

    expect(wrapper.text()).toContain('کد ابتدا در تلگرام ارسال شد؛ 00:40 تا ارسال خودکار پیامک')

    vi.advanceTimersByTime(40000)
    await flushPromises()
    expect(wrapper.text()).not.toContain('ارسال مجدد کد')
    expect(wrapper.text()).toContain('ارسال خودکار همان کد از طریق پیامک فعال شد.')
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-2/status',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )
    expect(wrapper.text()).toContain('درخواست ورود شما رد شد.')
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps legacy Telegram delivery truthful and preserves manual SMS resend', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(
        makeJsonResponse({
          method: 'telegram',
          expires_in: 120,
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          method: 'sms',
          expires_in: 85,
        }) as any,
      )

    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)

    expect(wrapper.text()).toContain('00:30 تا ارسال مجدد')
    expect(wrapper.text()).not.toContain('ارسال خودکار پیامک')

    vi.advanceTimersByTime(30_000)
    await flushPromises()
    const resend = findButtonByText(wrapper, 'ارسال مجدد کد')
    expect(resend.exists()).toBe(true)
    await resend.trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/auth/resend-otp-sms',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(wrapper.text()).toContain('01:25 تا ارسال مجدد')
    wrapper.unmount()
  })

  it('uses absolute fallback deadlines after browser clock jumps and visibility changes', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T03:00:00.000Z'))
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse({
        otp_request_id: '53a42ed0-4165-41db-a98f-4e18b205bca1',
        method: 'telegram',
        expires_in: 120,
        expires_at: '2026-07-12T03:02:00.000Z',
        sms_fallback_in: 40,
        sms_fallback_at: '2026-07-12T03:00:40.000Z',
      }) as any,
    )

    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    expect(wrapper.text()).toContain('00:40 تا ارسال خودکار پیامک')

    vi.setSystemTime(new Date('2026-07-12T03:00:31.000Z'))
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(wrapper.text()).toContain('00:09 تا ارسال خودکار پیامک')

    vi.setSystemTime(new Date('2026-07-12T03:00:41.000Z'))
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(wrapper.text()).toContain('ارسال خودکار همان کد از طریق پیامک فعال شد.')
    expect(wrapper.text()).not.toContain('ارسال مجدد کد')
    wrapper.unmount()
  })

  it('restores an opaque active OTP request after refresh without persisting mobile PII', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T04:00:00.000Z'))
    sessionStorage.setItem(
      'login_otp_attempt_v1',
      JSON.stringify({
        requestId: '0d5c80cb-f5a6-40e5-b3cb-f71636d94625',
        method: 'telegram',
        expiresAt: '2026-07-12T04:02:00.000Z',
        smsFallbackAt: '2026-07-12T04:00:40.000Z',
      }),
    )
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ access_token: 'restored-access' }) as any)

    const wrapper = mount(LoginView)
    await flushPromises()
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('0912')
    expect(sessionStorage.getItem('login_otp_attempt_v1')).not.toContain('0912')

    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    const verifyCall = fetchMock.mock.calls.find(([url]) => url === '/api/auth/verify-otp')
    expect(verifyCall).toBeTruthy()
    expect(JSON.parse(String(verifyCall?.[1]?.body))).toMatchObject({
      otp_request_id: '0d5c80cb-f5a6-40e5-b3cb-f71636d94625',
      code: '12345',
    })
    expect(JSON.parse(String(verifyCall?.[1]?.body))).not.toHaveProperty('mobile_number')
    expect(sessionStorage.getItem('login_otp_attempt_v1')).toBeNull()
    wrapper.unmount()
  })

  it('fails closed for malformed or expired persisted OTP attempts', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T04:00:00.000Z'))
    sessionStorage.setItem('login_otp_attempt_v1', '{malformed-json')
    const malformedWrapper = mount(LoginView)
    await flushPromises()
    expect(sessionStorage.getItem('login_otp_attempt_v1')).toBeNull()
    expect(malformedWrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)
    malformedWrapper.unmount()

    sessionStorage.setItem(
      'login_otp_attempt_v1',
      JSON.stringify({
        requestId: '',
        method: 'sms',
        expiresAt: '2026-07-12T03:59:59.000Z',
      }),
    )
    const expiredWrapper = mount(LoginView)
    await flushPromises()
    expect(sessionStorage.getItem('login_otp_attempt_v1')).toBeNull()
    expect(expiredWrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)
    expiredWrapper.unmount()
  })

  it('keeps OTP timer and browser-storage failures bounded to client state', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-12T05:00:00.000Z'))
    sessionStorage.setItem(
      'login_otp_attempt_v1',
      JSON.stringify({
        requestId: 'restored-request',
        method: 'unknown',
        expiresAt: '2026-07-12T05:02:00.000Z',
      }),
    )
    const wrapper = mount(LoginView)
    await flushPromises()
    const vm = wrapper.vm as any

    expect(vm.lastMethod).toBeNull()
    expect(vm.smsFallbackAt).toBeNull()
    vm.startTimerUntil('not-a-date')
    expect(vm.countdown).toBe(0)
    expect(vm.otpDeliveryStatus).toBe('')
    vm.startTimerUntil(Date.now() + 10_000)
    vm.startTimerUntil(Date.now() + 20_000)

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('storage denied')
    })
    vm.otpRequestId = 'opaque-request'
    vm.otpExpiresAt = '2026-07-12T05:02:00.000Z'
    expect(() => vm.persistOtpAttempt()).not.toThrow()
    setItem.mockRestore()

    const removeItem = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('storage denied')
    })
    expect(() => vm.clearOtpAttempt()).not.toThrow()
    removeItem.mockRestore()
    wrapper.unmount()
  })

  it('uses nested OTP errors and keeps automatic Telegram fallback non-resendable', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(
        makeJsonResponse({ detail: { message: 'خطای ساخت‌یافته' } }, false, 400) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          otp_request_id: 'automatic-request',
          method: 'telegram',
          expires_at: new Date(Date.now() + 120_000).toISOString(),
          sms_fallback_at: new Date(Date.now() + 40_000).toISOString(),
        }) as any,
      )
    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    expect(wrapper.text()).toContain('خطای ساخت‌یافته')

    await requestOtpFromMobileStep(wrapper)
    const callsBeforeResend = fetchMock.mock.calls.length
    await (wrapper.vm as any).handleResend()
    expect(fetchMock).toHaveBeenCalledTimes(callsBeforeResend)
    wrapper.unmount()
  })

  it('uses bounded legacy recovery without deriving timing from localized 429 copy', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse({ detail: 'لطفاً ۴۵ ثانیه صبر کنید' }, false, 429) as any,
    )
    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)

    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('00:30 تا ارسال مجدد')
    expect(wrapper.text()).not.toContain('00:45')
    vi.advanceTimersByTime(30_000)
    await flushPromises()
    expect(findButtonByText(wrapper, 'ارسال مجدد کد').exists()).toBe(true)
    wrapper.unmount()
  })

  it('restores OTP entry from a structured flags-off active-code response', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse(
        {
          detail: 'کد قبلی هنوز معتبر است.',
          code: 'otp_active',
          delivery_contract: 'legacy',
          manual_sms_resend: true,
          legacy_sms_resend_at: new Date(Date.now() + 20_000).toISOString(),
          expires_in: 73,
          expires_at: new Date(Date.now() + 73_000).toISOString(),
        },
        false,
        429,
      ) as any,
    )
    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)

    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('00:20 تا ارسال مجدد')
    expect(wrapper.text()).not.toContain('ارسال خودکار پیامک')
    vi.advanceTimersByTime(20_000)
    await flushPromises()
    expect(findButtonByText(wrapper, 'ارسال مجدد کد').exists()).toBe(true)
    wrapper.unmount()
  })

  it('explains completed registration on the existing OTP login surface', async () => {
    routeMock.query = { registration: 'complete' }
    const wrapper = mount(LoginView)

    expect(wrapper.text()).toContain('ثبت‌نام قبلاً تکمیل شده است')
    expect(wrapper.text()).toContain('برای ورود به وب‌اپ، کد تأیید دریافت کنید.')
    expect(wrapper.find('input[type="tel"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('completes login when approval polling returns approved tokens', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-approved-poll',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approved',
          access_token: 'poll-access',
          refresh_token: 'poll-refresh',
        }) as any,
      )
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(
          makeJsonResponse({
            id: 30,
            role: 'عادی',
            full_name: 'کاربر',
            account_name: 'user',
          }) as any,
        )
      }
      return fetch(url, options) as any
    })

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-approved-poll/status',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )
    expect(localStorage.getItem('auth_token')).toBe('poll-access')
    expect(localStorage.getItem('refresh_token')).toBe('poll-refresh')
    expect(routerPushMock).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })

  it('handles recovery waiting cancellation and returns to the mobile step', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-cancel-recovery',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'pending_admin_review',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'pending_admin_review',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(makeJsonResponse({ status: 'cancelled' }) as any)

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()

    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-cancel-recovery/recovery',
      expect.objectContaining({
        method: 'POST',
        signal: expect.any(AbortSignal),
      }),
    )
    expect(wrapper.text()).toContain('در حال بررسی مدیریت')

    await findButtonByText(wrapper, 'انصراف از درخواست').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-cancel-recovery/recovery/cancel',
      expect.objectContaining({
        method: 'POST',
        signal: expect.any(AbortSignal),
      }),
    )
    expect(wrapper.text()).toContain('درخواست بازیابی لغو شد')
    expect(wrapper.find('input[type="tel"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('validates recovery identity uploads and opens every picker input', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-identity-validation',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'identity_verification_requested',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'identity_verification_requested',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    const hiddenInputs = wrapper.findAll('input[type="file"]')
    const clickSpies = hiddenInputs.map((input) =>
      vi.spyOn(input.element as HTMLInputElement, 'click').mockImplementation(() => {}),
    )

    await findButtonByText(wrapper, 'گالری').trigger('click')
    await findButtonByText(wrapper, 'دوربین').trigger('click')
    await findButtonByText(wrapper, 'فایل').trigger('click')
    clickSpies.forEach((spy) => expect(spy).toHaveBeenCalledTimes(1))

    await findButtonByText(wrapper, 'ارسال مدرک برای بررسی').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ابتدا تصویر یا فایل مدرک را انتخاب کنید')
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-identity-validation/recovery/identity',
      expect.anything(),
    )

    clickSpies.forEach((spy) => spy.mockRestore())
    wrapper.unmount()
  })

  it('starts the recovery flow from the waiting screen and submits identity material', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-recovery',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'identity_verification_requested',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'identity_verification_requested',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          detail: 'submitted',
          recovery: {
            status: 'identity_submitted',
            chat_action_expires_at: '2099-05-08T10:10:00.000Z',
          },
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'identity_submitted',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()

    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-recovery/recovery',
      expect.objectContaining({
        method: 'POST',
        signal: expect.any(AbortSignal),
      }),
    )
    expect(wrapper.text()).toContain('مدرک احراز هویت')

    const recoveryFile = new File(['card'], 'card.jpg', { type: 'image/jpeg' })
    const recoveryInput = wrapper.find('input[type="file"][accept="image/*"]')
    Object.defineProperty(recoveryInput.element, 'files', {
      configurable: true,
      value: [recoveryFile],
    })
    await recoveryInput.trigger('change')
    await wrapper.get('textarea').setValue('کارت ملی')
    await findButtonByText(wrapper, 'ارسال مدرک برای بررسی').trigger('click')
    await flushPromises()

    const identityCall = fetchMock.mock.calls.find(
      ([url]) => url === '/api/sessions/login-requests/req-recovery/recovery/identity',
    )
    expect(identityCall).toBeTruthy()
    const identityBody =
      identityCall?.[1] && 'body' in identityCall[1] ? (identityCall[1].body as FormData) : null
    expect(identityBody?.get('caption')).toBe('کارت ملی')
    expect(identityBody?.get('file')).toBe(recoveryFile)
    expect(wrapper.text()).toContain('مدرک ارسال شد')
    wrapper.unmount()
  })

  it('completes login from an approved recovery result', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-approved',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approved',
          access_token: 'recovery-access',
          refresh_token: 'recovery-refresh',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approved',
          access_token: 'recovery-access',
          refresh_token: 'recovery-refresh',
        }) as any,
      )
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(
          makeJsonResponse({
            id: 20,
            role: 'عادی',
            full_name: 'علی',
            account_name: 'ali',
          }) as any,
        )
      }
      return fetch(url, options) as any
    })

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('درخواست شما تأیید شد')
    await findButtonByText(wrapper, 'ورود به سامانه').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(localStorage.getItem('auth_token')).toBe('recovery-access')
    expect(localStorage.getItem('refresh_token')).toBe('recovery-refresh')
    expect(apiFetchMock).toHaveBeenCalledWith('/api/auth/me')
    expect(routerPushMock).toHaveBeenCalledWith('/')
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
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(
          makeJsonResponse({
            id: 10,
            role: 'مدیر ارشد',
            full_name: 'دولوپر',
            account_name: 'dev',
          }) as any,
        )
      }
      return fetch(url, options) as any
    })
    localStorage.setItem('suspended_refresh_token', 'stale-token')
    vi.stubEnv('VITE_STAGING_DEV_LOGIN', 'true')
    vi.resetModules()

    const LoginViewWithDevLogin = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginViewWithDevLogin)

    await findButtonByText(wrapper, 'ورود سریع ۱ ساله').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/dev-login',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        signal: expect.any(AbortSignal),
      }),
    )
    expect(localStorage.getItem('auth_token')).toBe('dev-access')
    expect(localStorage.getItem('refresh_token')).toBe('dev-refresh')
    expect(localStorage.getItem('suspended_refresh_token')).toBeNull()
    expect(setupExpiryTimerMock).toHaveBeenCalledTimes(1)
    expect(clearBackStackMock).toHaveBeenCalledTimes(1)
    expect(routerPushMock).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })

  it('shows developer quick-login on staging builds with public hostnames', async () => {
    vi.stubEnv('VITE_STAGING_DEV_LOGIN', 'true')
    vi.resetModules()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...originalWindowLocation,
        hostname: 'staging.362514.ir',
        href: 'http://staging.362514.ir/login',
      },
    })

    const LoginViewWithDevLogin = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginViewWithDevLogin)

    expect(wrapper.text()).toContain('ورود سریع ۱ ساله')

    wrapper.unmount()
  })

  it.each([
    'localhost',
    '127.0.0.1',
    '10.0.0.12',
    '172.16.4.8',
    '192.168.1.20',
  ])('hides developer quick-login on %s unless the staging flag is set', (hostname) => {
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...originalWindowLocation,
        hostname,
        href: `http://${hostname}/login`,
      },
    })

    const wrapper = mount(LoginView)
    expect(wrapper.text()).not.toContain('ورود سریع ۱ ساله')
    wrapper.unmount()
  })

  it('keeps public authentication free of local PWA install UI and event ownership', async () => {
    vi.useFakeTimers()
    const promptEvent = {
      preventDefault: vi.fn(),
      prompt: vi.fn(),
      userChoice: Promise.resolve({ outcome: 'accepted' }),
    }

    const wrapper = mount(LoginView)

    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')
    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()
    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')

    window.dispatchEvent(Object.assign(new Event('beforeinstallprompt'), promptEvent))
    await flushPromises()

    expect(promptEvent.preventDefault).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')
    expect(promptEvent.prompt).not.toHaveBeenCalled()
    expect(wrapper.find('[class*="install"]').exists()).toBe(false)

    wrapper.unmount()
  })

  it('surfaces request, resend, and verify error branches without leaving the current flow', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 500) as any)

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    expect(wrapper.text()).toContain('خطا در ارسال کد')

    fetchMock.mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
    await wrapper.get('button.ui-button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('کد ارسال‌شده را وارد کنید.')
    expect(wrapper.get('.ui-v2-auth-login-meta bdi').text()).toBe('0912****789')

    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12')
    await wrapper.get('button.ui-button').trigger('click')
    expect(wrapper.text()).toContain('کد احراز هویت نامعتبر است')

    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 401) as any)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('1234')
    await wrapper.get('button.ui-button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('کد نادرست است')

    await vi.advanceTimersByTimeAsync(120000)
    await flushPromises()
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 500) as any)
    await findButtonByText(wrapper, 'ارسال مجدد کد').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('خطا در ارسال کد')

    wrapper.unmount()
  })

  it('keeps the OTP draft in place when verification returns an invalid success payload', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ unexpected: true }) as any)

    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    const otpInput = wrapper.get('input[autocomplete="one-time-code"]')
    await otpInput.setValue('24680')
    await flushPromises()

    expect(wrapper.text()).toContain('پاسخ تأیید ورود کامل نیست')
    expect(
      (wrapper.get('input[autocomplete="one-time-code"]').element as HTMLInputElement).value,
    ).toBe('24680')
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(routerPushMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('uses cause-neutral copy for malformed successful OTP JSON', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError('Unexpected token at position 0')
      },
    } as any)
    const wrapper = mount(LoginView)
    await wrapper.get('input[type="tel"]').setValue('09124444444')
    await requestOtpFromMobileStep(wrapper)

    expect(wrapper.text()).toContain('خطا در ارسال کد')
    expect(wrapper.text()).not.toContain('Unexpected')
    expect((wrapper.get('input[type="tel"]').element as HTMLInputElement).value).toBe('09124444444')
    wrapper.unmount()
  })

  it('offers app cache recovery for network-like login errors', async () => {
    const replaceSpy = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...originalWindowLocation,
        href: 'https://coin.362514.ir/login?token=raw-secret&source=sms#otp',
        replace: replaceSpy,
      },
    })
    const wrapper = mount(LoginView)
    const vm = wrapper.vm as any

    vm.error = 'Failed to fetch'
    await flushPromises()

    const recoveryButton = findButtonByText(wrapper, 'پاک‌سازی کش برنامه و بارگذاری مجدد')
    await recoveryButton.trigger('click')

    expect(replaceSpy).toHaveBeenCalledWith(expect.stringMatching(/^\/login\?app_recovery=\d+$/))
    expect(replaceSpy.mock.calls[0]?.[0]).not.toContain('raw-secret')
    expect(replaceSpy.mock.calls[0]?.[0]).not.toContain('#otp')

    wrapper.unmount()
  })

  it('handles approval expiry plus recovery rejected and expired states', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-08T08:00:00.000Z'))
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-expiring',
          expires_at: '2026-05-08T08:00:01.000Z',
        }) as any,
      )

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(wrapper.text()).toContain('زمان انتظار تأیید به پایان رسید')
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
    wrapper.unmount()

    vi.setSystemTime(new Date('2026-05-08T09:00:00.000Z'))
    fetchMock.mockReset()
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-rejected',
          expires_at: '2026-05-08T09:05:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'rejected',
          chat_action_expires_at: '2026-05-08T10:00:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'rejected',
          chat_action_expires_at: '2026-05-08T10:00:00.000Z',
        }) as any,
      )

    const rejectedWrapper = mount(LoginView)
    await rejectedWrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(rejectedWrapper)
    await rejectedWrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(rejectedWrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(rejectedWrapper.text()).toContain('درخواست شما رد شد')
    await findButtonByText(rejectedWrapper, 'شروع دوباره').trigger('click')
    expect(rejectedWrapper.find('input[type="tel"]').exists()).toBe(true)
    rejectedWrapper.unmount()

    fetchMock.mockReset()
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-expired-recovery',
          expires_at: '2026-05-08T09:05:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'pending_admin_review',
          chat_action_expires_at: '2026-05-08T08:59:59.000Z',
        }) as any,
      )

    const expiredWrapper = mount(LoginView)
    await expiredWrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(expiredWrapper)
    await expiredWrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(expiredWrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(expiredWrapper.text()).toContain('مهلت درخواست به پایان رسید')
    expiredWrapper.unmount()
  })

  it('covers recovery request/cancel/identity failure branches and missing approved tokens', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-recovery-errors',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 500) as any)

    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('شروع مسیر بازیابی ممکن نشد')
    wrapper.unmount()

    fetchMock.mockReset()
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'approval_required',
          login_request_id: 'req-cancel-error',
          expires_at: '2099-05-08T08:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'identity_verification_requested',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(
        makeJsonResponse({
          status: 'identity_verification_requested',
          chat_action_expires_at: '2099-05-08T10:10:00.000Z',
        }) as any,
      )
      .mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 500) as any)
      .mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 500) as any)
      .mockResolvedValueOnce(makeJsonResponse({ status: 'approved' }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ status: 'approved' }) as any)

    const errorWrapper = mount(LoginView)
    await errorWrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(errorWrapper)
    await errorWrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(errorWrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    await findButtonByText(errorWrapper, 'انصراف از درخواست').trigger('click')
    await flushPromises()
    expect(errorWrapper.text()).toContain('لغو درخواست بازیابی ممکن نشد')

    const recoveryFile = new File(['card'], 'card.jpg', { type: 'image/jpeg' })
    const recoveryInput = errorWrapper.find('input[type="file"][accept="image/*"]')
    Object.defineProperty(recoveryInput.element, 'files', {
      configurable: true,
      value: [recoveryFile],
    })
    await recoveryInput.trigger('change')
    await findButtonByText(errorWrapper, 'ارسال مدرک برای بررسی').trigger('click')
    await flushPromises()
    expect(errorWrapper.text()).toContain('ارسال مدرک ممکن نشد')

    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(errorWrapper.text()).toContain('درخواست شما تأیید شد')
    await findButtonByText(errorWrapper, 'ورود به سامانه').trigger('click')
    await flushPromises()
    expect(errorWrapper.text()).toContain('دسترسی ورود هنوز آماده نیست')
    errorWrapper.unmount()
  })

  it('retries a bounded recovery-status request when approval arrives before its login token', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(
      makeJsonResponse({
        status: 'approved',
        access_token: 'retried-recovery-access',
        refresh_token: 'retried-recovery-refresh',
      }) as any,
    )
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(
          makeJsonResponse({
            id: 51,
            role: 'عادی',
            full_name: 'بازیابی',
            account_name: 'recovered',
          }) as any,
        )
      }
      return fetch(url, options) as any
    })

    const wrapper = mount(LoginView)
    const vm = wrapper.vm as any
    vm.loginRequestId = 'req-approved-without-token'
    vm.applyRecoveryStatus({ status: 'approved' })
    await flushPromises()

    await findButtonByText(wrapper, 'ورود به سامانه').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-approved-without-token/recovery/status',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    )
    expect(localStorage.getItem('auth_token')).toBe('retried-recovery-access')
    expect(localStorage.getItem('refresh_token')).toBe('retried-recovery-refresh')
    expect(routerPushMock).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })

  it('initializes WebOTP with abort cleanup without taking ownership of PWA installation', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(
        makeJsonResponse({ access_token: 'otp-access', refresh_token: 'otp-refresh' }) as any,
      )
    apiFetchMock.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/auth/me') {
        return Promise.resolve(
          makeJsonResponse({ id: 40, role: 'عادی', full_name: 'وب', account_name: 'web' }) as any,
        )
      }
      return fetch(url, options) as any
    })

    const abortSpy = vi.fn()
    class AbortControllerMock {
      signal = {}
      abort = abortSpy
    }
    vi.stubGlobal('AbortController', AbortControllerMock)
    Object.defineProperty(window, 'OTPCredential', {
      configurable: true,
      value: function OTPCredential() {},
    })
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: {
        get: vi.fn(async () => ({ code: '12345' })),
      },
    })
    const wrapper = mount(LoginView)

    const deferredPrompt = {
      preventDefault: vi.fn(),
      prompt: vi.fn(),
      userChoice: Promise.resolve({ outcome: 'dismissed' }),
    }
    window.dispatchEvent(Object.assign(new Event('beforeinstallprompt'), deferredPrompt))
    await flushPromises()
    expect(deferredPrompt.preventDefault).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await requestOtpFromMobileStep(wrapper)
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    await flushPromises()

    expect(navigator.credentials.get).toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/auth/verify-otp',
      expect.objectContaining({ method: 'POST' }),
    )

    wrapper.unmount()
    expect(abortSpy).toHaveBeenCalled()
  })

  it('covers helper branches for OTP reuse, resend failures, approval expiry polling, recovery status helpers, and dev-login errors', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)

    const wrapper = mount(LoginView)
    const vm = wrapper.vm as any

    vm.error = 'stale error'
    vm.goToOtpStep()
    const goBack = pushBackStateMock.mock.calls.at(-1)?.[0] as (() => void) | undefined
    expect(vm.step).toBe('otp')
    goBack?.()
    expect(vm.step).toBe('mobile')
    expect(vm.error).toBe('')

    vm.form.mobile = '09123456789'
    vm.countdown = 25
    await vm.requestOtp()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(vm.step).toBe('otp')

    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 500) as any)
    await vm.resendOtpSms()
    expect(vm.error).toBe('خطا در ارسال پیامک')

    const approvalWrapper = mount(LoginView)
    const approvalVm = approvalWrapper.vm as any
    approvalVm.step = 'waiting_approval'
    approvalVm.loginRequestId = 'req-poll-catch'
    approvalVm.approvalExpiresAt = '2099-05-08T08:10:00.000Z'
    fetchMock.mockRejectedValueOnce(new Error('poll failed'))
    approvalVm.startApprovalPolling()
    await vi.advanceTimersByTimeAsync(2001)
    await flushPromises()
    expect(approvalVm.loginRequestId).toBe('req-poll-catch')

    approvalVm.stopApprovalPolling(true)
    expect(approvalVm.loginRequestId).toBe('req-poll-catch')
    approvalVm.stopApprovalPolling()
    expect(approvalVm.loginRequestId).toBeNull()

    approvalVm.loginRequestId = 'req-recovery-keep'
    approvalVm.startRecoveryCountdown()
    expect(approvalVm.recoveryCountdown).toBe(7200)

    approvalVm.loginRequestId = 'req-recovery-expire'
    approvalVm.startRecoveryCountdown(new Date(Date.now() + 1000).toISOString())
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(approvalVm.step).toBe('recovery_expired')

    expect(approvalVm.parseResponseError({ detail: 'پیام اختصاصی' }, 'fallback')).toBe(
      'پیام اختصاصی',
    )
    expect(approvalVm.parseResponseError({ detail: '   ' }, 'fallback')).toBe('fallback')

    approvalVm.recoveryApprovedTokens = { access_token: 'stale', refresh_token: 'old' }
    approvalVm.applyRecoveryStatus({ status: 'approved' })
    expect(approvalVm.step).toBe('recovery_approved')
    expect(approvalVm.recoveryApprovedTokens).toBeNull()

    approvalVm.recoveryFile = new File(['identity'], 'card.jpg', { type: 'image/jpeg' })
    approvalVm.recoveryCaption = 'caption'
    approvalVm.form.code = '12345'
    approvalVm.applyRecoveryStatus({ status: 'cancelled' })
    expect(approvalVm.step).toBe('mobile')
    expect(approvalVm.form.code).toBe('')
    expect(approvalVm.error).toContain('درخواست بازیابی لغو شد')

    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 403) as any)
    await approvalVm.startDevLogin()
    expect(approvalVm.error).toBe('دسترسی مجاز نیست')

    approvalWrapper.unmount()
    wrapper.unmount()
  })

  it('keeps public auth free of install guidance while covering WebOTP error and step-abort cleanup', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    const consoleSpies = [
      vi.spyOn(console, 'log').mockImplementation(() => {}),
      vi.spyOn(console, 'info').mockImplementation(() => {}),
      vi.spyOn(console, 'warn').mockImplementation(() => {}),
      vi.spyOn(console, 'error').mockImplementation(() => {}),
    ]
    const abortSpy = vi.fn()

    class AbortControllerMock {
      signal = {}
      abort = abortSpy
    }

    vi.stubGlobal('AbortController', AbortControllerMock)
    Object.defineProperty(window, 'OTPCredential', {
      configurable: true,
      value: function OTPCredential() {},
    })
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: {
        get: vi.fn(async () => {
          throw new Error('OTP-SENTINEL-90817')
        }),
      },
    })
    const wrapper = mount(LoginView)
    const vm = wrapper.vm as any

    await vi.advanceTimersByTimeAsync(4000)
    await flushPromises()
    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')
    expect(wrapper.text()).not.toContain('راهنمای نصب در آیفون')
    expect(wrapper.find('[class*="install"]').exists()).toBe(false)

    vm.step = 'otp'
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    expect(navigator.credentials.get).toHaveBeenCalled()
    expect(JSON.stringify(consoleSpies.flatMap((spy) => spy.mock.calls))).not.toContain(
      'OTP-SENTINEL-90817',
    )

    vm.step = 'mobile'
    await flushPromises()
    expect(abortSpy).toHaveBeenCalled()

    wrapper.unmount()
    consoleSpies.forEach((spy) => spy.mockRestore())
    fetchMock.mockReset()
  })
})
