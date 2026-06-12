import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const routerPushMock = vi.fn()
const setupExpiryTimerMock = vi.fn()
const apiFetchMock = vi.fn()
const pushBackStateMock = vi.fn()
const popBackStateMock = vi.fn()
const clearBackStackMock = vi.fn()
const originalMatchMedia = window.matchMedia
const originalDeferredPrompt = (window as any).deferredPrompt
const originalOTPCredential = (window as any).OTPCredential
const originalNavigatorCredentials = navigator.credentials
const originalNavigatorStandalone = (window.navigator as any).standalone
const originalNavigatorUserAgent = window.navigator.userAgent
const originalWindowLocation = window.location

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
    apiFetchMock.mockImplementation((...args: Parameters<typeof fetch>) => fetch(...args) as any)
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
    vi.unstubAllGlobals()
    window.matchMedia = originalMatchMedia
    ;(window as any).deferredPrompt = originalDeferredPrompt ?? null

    if (typeof originalOTPCredential === 'undefined') {
      Reflect.deleteProperty(window as any, 'OTPCredential')
    } else {
      Object.defineProperty(window, 'OTPCredential', { configurable: true, value: originalOTPCredential })
    }

    if (typeof originalNavigatorCredentials === 'undefined') {
      Reflect.deleteProperty(navigator, 'credentials')
    } else {
      Object.defineProperty(navigator, 'credentials', { configurable: true, value: originalNavigatorCredentials })
    }

    if (typeof originalNavigatorStandalone === 'undefined') {
      Reflect.deleteProperty(window.navigator as any, 'standalone')
    } else {
      Object.defineProperty(window.navigator, 'standalone', { configurable: true, value: originalNavigatorStandalone })
    }

    Object.defineProperty(window.navigator, 'userAgent', { configurable: true, value: originalNavigatorUserAgent })
    Object.defineProperty(window, 'location', { configurable: true, value: originalWindowLocation })
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
          expires_at: '2099-05-08T08:10:00.000Z',
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
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/login-requests/req-2/status')
    expect(wrapper.text()).toContain('درخواست ورود شما رد شد.')
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(true)
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
          makeJsonResponse({ id: 30, role: 'عادی', full_name: 'کاربر', account_name: 'user' }) as any,
        )
      }
      return fetch(url, options) as any
    })

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/login-requests/req-approved-poll/status')
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

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()

    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/login-requests/req-cancel-recovery/recovery', {
      method: 'POST',
    })
    expect(wrapper.text()).toContain('در حال بررسی توسط مدیریت')

    await findButtonByText(wrapper, 'انصراف از درخواست').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/login-requests/req-cancel-recovery/recovery/cancel', {
      method: 'POST',
    })
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

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    const hiddenInputs = wrapper.findAll('input[type="file"]')
    const clickSpies = hiddenInputs.map(input => vi.spyOn(input.element as HTMLInputElement, 'click').mockImplementation(() => {}))

    await findButtonByText(wrapper, 'گالری').trigger('click')
    await findButtonByText(wrapper, 'دوربین').trigger('click')
    await findButtonByText(wrapper, 'فایل').trigger('click')
    clickSpies.forEach(spy => expect(spy).toHaveBeenCalledTimes(1))

    await findButtonByText(wrapper, 'ارسال مدارک').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ابتدا تصویر یا فایل مدرک را انتخاب کنید')
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/api/sessions/login-requests/req-identity-validation/recovery/identity',
      expect.anything(),
    )

    clickSpies.forEach(spy => spy.mockRestore())
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

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()

    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/login-requests/req-recovery/recovery', {
      method: 'POST',
    })
    expect(wrapper.text()).toContain('ارسال مدرک احراز هویت')

    const recoveryFile = new File(['card'], 'card.jpg', { type: 'image/jpeg' })
    const recoveryInput = wrapper.find('input[type="file"][accept="image/*"]')
    Object.defineProperty(recoveryInput.element, 'files', {
      configurable: true,
      value: [recoveryFile],
    })
    await recoveryInput.trigger('change')
    await wrapper.get('textarea').setValue('کارت ملی')
    await findButtonByText(wrapper, 'ارسال مدارک').trigger('click')
    await flushPromises()

    const identityCall = fetchMock.mock.calls.find(([url]) => url === '/api/sessions/login-requests/req-recovery/recovery/identity')
    expect(identityCall).toBeTruthy()
    const identityBody = identityCall?.[1] && 'body' in identityCall[1] ? identityCall[1].body as FormData : null
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

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await findButtonByText(wrapper, 'به دستگاه قبلی دسترسی ندارم').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('درخواست شما تایید شد')
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

  it('hides the login install button for native install prompt browsers and shows manual fallback otherwise', async () => {
    Object.defineProperty(window.navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/124.0.0.0 Mobile Safari/537.36',
    })
    const promptEvent = {
      preventDefault: vi.fn(),
      prompt: vi.fn(),
      userChoice: Promise.resolve({ outcome: 'accepted' }),
    }

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')

    window.dispatchEvent(Object.assign(new Event('beforeinstallprompt'), promptEvent))
    await flushPromises()

    expect(promptEvent.preventDefault).toHaveBeenCalled()
    expect(promptEvent.prompt).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('نصب اپلیکیشن')

    wrapper.unmount()

    Object.defineProperty(window.navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0',
    })

    const fallbackWrapper = mount(LoginView)
    expect(fallbackWrapper.text()).toContain('نصب اپلیکیشن')

    await findButtonByText(fallbackWrapper, 'نصب اپلیکیشن').trigger('click')
    await flushPromises()

    expect(fallbackWrapper.text()).toContain('راهنمای نصب دستی')
    expect(fallbackWrapper.text()).toContain('Chrome یا Edge')

    fallbackWrapper.unmount()
  })

  it('surfaces request, resend, and verify error branches without leaving the current flow', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 500) as any)

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    expect(wrapper.text()).toContain('خطا در ارسال کد')

    fetchMock.mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('کد ارسال شده به 09123456789')

    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12')
    await wrapper.get('button.btn-primary').trigger('click')
    expect(wrapper.text()).toContain('کد احراز هویت نامعتبر است')

    fetchMock.mockResolvedValueOnce(makeJsonResponse({ detail: '' }, false, 401) as any)
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('1234')
    await wrapper.get('button.btn-primary').trigger('click')
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

  it('offers app cache recovery for network-like login errors', async () => {
    const replaceSpy = vi.fn()
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...originalWindowLocation,
        href: 'https://coin.362514.ir/login',
        replace: replaceSpy,
      },
    })
    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)
    const vm = wrapper.vm as any

    vm.error = 'Failed to fetch'
    await flushPromises()

    const recoveryButton = findButtonByText(wrapper, 'پاک‌سازی کش برنامه و بارگذاری مجدد')
    await recoveryButton.trigger('click')

    expect(replaceSpy).toHaveBeenCalledWith(expect.stringContaining('app_recovery='))

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

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(wrapper.text()).toContain('زمان انتظار تایید به پایان رسید')
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
    await flushPromises()
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
    await flushPromises()
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

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
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

    const errorWrapper = mount(LoginView)
    await errorWrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
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
    await findButtonByText(errorWrapper, 'ارسال مدارک').trigger('click')
    await flushPromises()
    expect(errorWrapper.text()).toContain('ارسال مدرک ممکن نشد')

    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(errorWrapper.text()).toContain('درخواست شما تایید شد')
    await findButtonByText(errorWrapper, 'ورود به سامانه').trigger('click')
    await flushPromises()
    expect(errorWrapper.text()).toContain('توکن ورود آماده نیست')
    errorWrapper.unmount()
  })

  it('initializes install and WebOTP browser hooks including abort cleanup', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(makeJsonResponse({ method: 'sms' }) as any)
      .mockResolvedValueOnce(makeJsonResponse({ access_token: 'otp-access', refresh_token: 'otp-refresh' }) as any)
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
    Object.defineProperty(window, 'OTPCredential', { configurable: true, value: function OTPCredential() {} })
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: {
        get: vi.fn(async () => ({ code: '12345' })),
      },
    })
    Object.defineProperty(window.navigator, 'standalone', {
      configurable: true,
      value: true,
    })
    Object.defineProperty(window.navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
    })

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)

    const deferredPrompt = {
      preventDefault: vi.fn(),
      prompt: vi.fn(),
      userChoice: Promise.resolve({ outcome: 'dismissed' }),
    }
    window.dispatchEvent(Object.assign(new Event('beforeinstallprompt'), deferredPrompt))
    await flushPromises()
    expect(wrapper.text()).not.toContain('برای نصب در iOS')

    await wrapper.get('input[type="tel"]').setValue('09123456789')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    await flushPromises()

    expect(navigator.credentials.get).toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/verify-otp', expect.objectContaining({ method: 'POST' }))

    wrapper.unmount()
    expect(abortSpy).toHaveBeenCalled()
  })

  it('covers helper branches for OTP reuse, resend failures, approval expiry polling, recovery status helpers, and dev-login errors', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)

    const LoginView = (await import('./LoginView.vue')).default
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

    expect(approvalVm.parseResponseError({ detail: 'پیام اختصاصی' }, 'fallback')).toBe('پیام اختصاصی')
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

  it('hydrates initial install state for iOS and covers WebOTP error plus step-abort cleanup', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.mocked(fetch)
    const consoleLogSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
    const abortSpy = vi.fn()

    class AbortControllerMock {
      signal = {}
      abort = abortSpy
    }

    vi.stubGlobal('AbortController', AbortControllerMock)
    Object.defineProperty(window, 'OTPCredential', { configurable: true, value: function OTPCredential() {} })
    Object.defineProperty(navigator, 'credentials', {
      configurable: true,
      value: {
        get: vi.fn(async () => {
          throw new Error('otp failed')
        }),
      },
    })
    Object.defineProperty(window.navigator, 'standalone', {
      configurable: true,
      value: false,
    })
    Object.defineProperty(window.navigator, 'userAgent', {
      configurable: true,
      value: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
    })

    ;(window as any).deferredPrompt = {
      prompt: vi.fn(),
      userChoice: Promise.resolve({ outcome: 'dismissed' }),
    }

    const LoginView = (await import('./LoginView.vue')).default
    const wrapper = mount(LoginView)
    const vm = wrapper.vm as any

    expect(wrapper.text()).toContain('نصب اپلیکیشن')
    await (wrapper.vm as any).$nextTick()
    expect(wrapper.text()).toContain('راهنمای نصب در آیفون')
    expect(wrapper.text()).toContain('سایت را در Safari باز کنید')
    expect(wrapper.text()).toContain('Add to Home Screen')
    expect(wrapper.text()).toContain('از آیکن Gold روی Home Screen وارد شوید')

    vm.step = 'otp'
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    expect(navigator.credentials.get).toHaveBeenCalled()
    expect(consoleLogSpy).toHaveBeenCalledWith('Web OTP Error:', expect.any(Error))

    vm.step = 'mobile'
    await flushPromises()
    expect(abortSpy).toHaveBeenCalled()

    wrapper.unmount()
    consoleLogSpy.mockRestore()
    fetchMock.mockReset()
  })
})
