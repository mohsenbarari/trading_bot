import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WebRegister from './WebRegister.vue'
import {
  REGISTRATION_HANDOFF_KIND,
  clearRegistrationHandoff,
  readRegistrationHandoff,
  writeRegistrationHandoff,
} from '../utils/registrationHandoff'

const webRegisterMocks = vi.hoisted(() => ({
  route: { query: { token: 'invite-token' } as Record<string, string | undefined> },
  replace: vi.fn(),
  fetch: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => webRegisterMocks.route,
  useRouter: () => ({ replace: webRegisterMocks.replace }),
}))

function makeRegistrationContextResponse(overrides: Record<string, unknown> = {}, status = 200) {
  return new Response(
    JSON.stringify({
      account_name: 'test_user',
      mobile_number: '0912****000',
      role: 'عادی',
      expires_at: '2026-07-14T10:00:00Z',
      kind: 'invitation',
      progress: 'context_ready',
      requires_otp: true,
      ...overrides,
    }),
    { status, headers: { 'Content-Type': 'application/json' } },
  )
}

describe('WebRegister.vue', () => {
  beforeEach(() => {
    webRegisterMocks.route.query = { token: 'invite-token' }
    webRegisterMocks.replace.mockReset()
    webRegisterMocks.fetch.mockReset()
    clearRegistrationHandoff()
    localStorage.clear()
    sessionStorage.clear()
    vi.stubGlobal('fetch', webRegisterMocks.fetch)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('fails safely after refresh when both handoff memory and cookie context are missing', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.fetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'expired' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('جلسه ثبت‌نام نامعتبر یا منقضی شده است.')
    expect(wrapper.text()).not.toMatch(/توکن|registration_token|invite-token/i)
    expect(webRegisterMocks.fetch).toHaveBeenCalledWith(
      '/api/auth/registration-context',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
    expect(wrapper.text()).not.toContain('تلاش مجدد')
    expect(wrapper.text()).toContain('بازگشت به ورود')
    expect(webRegisterMocks.fetch).toHaveBeenCalledTimes(1)
  })

  it('scrubs a fragment-only legacy bearer before loading registration context', async () => {
    delete webRegisterMocks.route.query.token
    const bearer = 'REG-fragment-only-secret'
    window.history.replaceState(
      null,
      '',
      `/register#/legacy/registration_token%253D${encodeURIComponent(bearer)}`,
    )
    const replaceStateSpy = vi.spyOn(window.history, 'replaceState')
    sessionStorage.setItem('web_registration_progress_v1', 'stale-progress')
    webRegisterMocks.fetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'expired' }), {
        status: 410,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(window.location.hash).toBe('')
    expect(window.location.href).not.toContain(bearer)
    expect(replaceStateSpy).toHaveBeenCalledWith(null, '', '/register')
    expect(sessionStorage.getItem('web_registration_progress_v1')).toBeNull()
    expect(webRegisterMocks.replace).toHaveBeenCalledWith({
      name: 'web-register',
      query: {},
      hash: '',
    })
    expect(wrapper.html()).not.toContain(bearer)
    expect(JSON.stringify(localStorage)).not.toContain(bearer)
    expect(JSON.stringify(sessionStorage)).not.toContain(bearer)

    wrapper.unmount()
    replaceStateSpy.mockRestore()
    window.history.replaceState(null, '', '/')
  })

  it('stops before context loading when router-state sanitization fails', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.route.query.registration_token = 'REG-router-secret'
    window.history.replaceState(null, '', '/register?registration_token=REG-router-secret')
    webRegisterMocks.replace.mockResolvedValueOnce({ type: 4 })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(window.location.href).not.toContain('REG-router-secret')
    expect(webRegisterMocks.fetch).not.toHaveBeenCalled()
    expect(wrapper.html()).not.toContain('REG-router-secret')
    expect(JSON.stringify(sessionStorage)).not.toContain('REG-router-secret')
    expect(JSON.stringify(localStorage)).not.toContain('REG-router-secret')

    wrapper.unmount()
    consoleError.mockRestore()
    window.history.replaceState(null, '', '/')
  })

  it('routes an authenticated step-four refresh home when the cleared context is absent', async () => {
    delete webRegisterMocks.route.query.token
    localStorage.setItem('auth_token', 'existing-auth-session')
    localStorage.setItem('refresh_token', 'existing-refresh-session')
    webRegisterMocks.fetch
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'context cleared after completion' }), {
          status: 410,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: 77,
            account_name: 'completed_user',
            role: 'عادی',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(webRegisterMocks.fetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/auth/registration-context',
      '/api/auth/me',
    ])
    expect(webRegisterMocks.replace).toHaveBeenCalledWith('/')
    expect(wrapper.text()).not.toContain('جلسه ثبت‌نام نامعتبر یا منقضی شده است.')
    expect(wrapper.html()).not.toContain('existing-auth-session')
  })

  it('routes an authenticated step-four refresh home when a retained completion marker exists', async () => {
    delete webRegisterMocks.route.query.token
    localStorage.setItem('auth_token', 'retained-complete-session')
    localStorage.setItem('refresh_token', 'retained-complete-refresh')
    webRegisterMocks.fetch
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: 'registration_complete' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 77, account_name: 'completed_user' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(webRegisterMocks.fetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/auth/registration-context',
      '/api/auth/me',
      '/api/auth/registration-context/clear',
    ])
    expect(webRegisterMocks.replace).toHaveBeenCalledWith('/')
    expect(webRegisterMocks.replace).not.toHaveBeenCalledWith({
      name: 'login',
      query: { registration: 'complete' },
    })
    expect(wrapper.html()).not.toContain('retained-complete-session')
  })

  it('retains a completion marker when authenticated recovery navigation fails', async () => {
    delete webRegisterMocks.route.query.token
    localStorage.setItem('auth_token', 'retained-complete-session')
    webRegisterMocks.replace.mockRejectedValueOnce(new Error('navigation failed'))
    webRegisterMocks.fetch
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: 'registration_complete' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: 77, account_name: 'completed_user' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('بررسی اطلاعات ثبت‌نام اکنون ممکن نشد.')
    expect(
      webRegisterMocks.fetch.mock.calls.some(
        ([url]) => url === '/api/auth/registration-context/clear',
      ),
    ).toBe(false)
    expect(webRegisterMocks.replace).toHaveBeenCalledTimes(1)
  })

  it('loads a primary invitation handoff without putting its secret back in the route', async () => {
    delete webRegisterMocks.route.query.token
    expect(
      writeRegistrationHandoff({
        kind: REGISTRATION_HANDOFF_KIND.INVITATION,
        token: 'INV-session',
      }),
    ).toBe(true)
    webRegisterMocks.fetch.mockResolvedValueOnce(
      makeRegistrationContextResponse({ account_name: 'session_user' }),
    )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(webRegisterMocks.fetch).toHaveBeenCalledWith(
      '/api/auth/registration-context/exchange',
      expect.objectContaining({
        method: 'POST',
        credentials: 'same-origin',
      }),
    )
    expect(JSON.parse(String(webRegisterMocks.fetch.mock.calls[0]?.[1]?.body))).toEqual({
      kind: 'invitation',
      token: 'INV-session',
      exchange_id: expect.any(String),
    })
    expect(webRegisterMocks.replace).not.toHaveBeenCalledWith(
      expect.objectContaining({
        query: expect.objectContaining({ token: expect.anything() }),
      }),
    )
    expect(wrapper.text()).toContain('session_user')
    expect(readRegistrationHandoff()).toBeNull()
  })

  it('retries an ambiguous initial exchange with the same id after checking the cookie first', async () => {
    webRegisterMocks.fetch
      .mockRejectedValueOnce(new TypeError('exchange response lost'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'context missing' }), {
          status: 410,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(makeRegistrationContextResponse())

    const wrapper = mount(WebRegister)
    await flushPromises()
    expect(wrapper.text()).toContain('تلاش مجدد')

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تلاش مجدد'))!
      .trigger('click')
    await flushPromises()

    const urls = webRegisterMocks.fetch.mock.calls.map(([url]) => url)
    expect(urls).toEqual([
      '/api/auth/registration-context/exchange',
      '/api/auth/registration-context',
      '/api/auth/registration-context/exchange',
    ])
    const exchangeBodies = webRegisterMocks.fetch.mock.calls
      .filter(([url]) => url === '/api/auth/registration-context/exchange')
      .map(([, options]) => JSON.parse(String(options?.body)))
    expect(exchangeBodies).toHaveLength(2)
    expect(exchangeBodies[0]).toEqual(exchangeBodies[1])
    expect(exchangeBodies[0].token).toBe('invite-token')
    expect(wrapper.text()).toContain('test_user')
    expect(wrapper.html()).not.toContain('invite-token')
  })

  it('migrates and scrubs a legacy registration-token query before loading context', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.route.query.registration_token = 'REG-legacy'
    webRegisterMocks.route.query.source = 'otp'
    window.history.replaceState(null, '', '/register?registration_token=REG-legacy&source=otp#safe')
    webRegisterMocks.fetch.mockResolvedValueOnce(
      makeRegistrationContextResponse({
        account_name: 'legacy_user',
        kind: 'registration',
        progress: 'otp_verified',
        requires_otp: false,
      }),
    )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(webRegisterMocks.replace).toHaveBeenCalledWith({
      name: 'web-register',
      query: { source: 'otp' },
      hash: '#safe',
    })
    expect(window.location.search).toBe('?source=otp')
    expect(window.location.hash).toBe('#safe')
    expect(readRegistrationHandoff()).toBeNull()
    expect(webRegisterMocks.fetch).toHaveBeenCalledWith(
      '/api/auth/registration-context/exchange',
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
      }),
    )
    expect(JSON.parse(String(webRegisterMocks.fetch.mock.calls[0]?.[1]?.body))).toEqual({
      kind: 'registration',
      token: 'REG-legacy',
      exchange_id: expect.any(String),
    })
    expect(String(webRegisterMocks.fetch.mock.calls[0]?.[0])).not.toContain('REG-legacy')
    expect(wrapper.text()).toContain('legacy_user')
    expect(wrapper.html()).not.toContain('REG-legacy')
    expect(JSON.stringify(sessionStorage)).not.toContain('REG-legacy')
    expect(JSON.stringify(localStorage)).not.toContain('REG-legacy')
    wrapper.unmount()
    window.history.replaceState(null, '', '/')
  })

  it('completes the full invite validation, OTP, and registration flow', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تایید ارسال شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تایید شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ access_token: 'access-1', refresh_token: 'refresh-1' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ can_connect_telegram: false, telegram_linked: false }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('نام حساب')
    expect(wrapper.text()).toContain('مهلت ثبت‌نام')
    expect(wrapper.text()).toContain('مرحله ۱ از ۳')
    expect(wrapper.find('[data-ui-pwa]').exists()).toBe(false)
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کد تأیید پنج‌رقمی')
    expect(wrapper.text()).toContain('مرحله ۲ از ۳')
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('12345')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تأیید و ادامه'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('نشانی دقیق پستی')
    expect(wrapper.text()).toContain('مهلت ثبت‌نام')
    expect(wrapper.text()).toContain('مرحله ۳ از ۳')
    await wrapper
      .get('textarea[autocomplete="street-address"]')
      .setValue('تهران، خیابان مثال، پلاک ۱۲۳')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تکمیل ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(localStorage.getItem('auth_token')).toBe('access-1')
    expect(localStorage.getItem('refresh_token')).toBe('refresh-1')
    expect(readRegistrationHandoff()).toBeNull()
    expect(JSON.stringify(webRegisterMocks.fetch.mock.calls.slice(1, 4))).not.toContain(
      'invite-token',
    )
    expect(webRegisterMocks.replace).toHaveBeenCalledWith('/')
    expect(webRegisterMocks.fetch.mock.calls.map(([url]) => url).slice(-2)).toEqual([
      '/api/auth/me',
      '/api/auth/registration-context/clear',
    ])
  })

  it('retains the authoritative completion marker when ordinary Home navigation fails', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.replace.mockResolvedValueOnce({ type: 4, to: '/' })
    webRegisterMocks.fetch
      .mockResolvedValueOnce(
        makeRegistrationContextResponse({
          kind: 'registration',
          progress: 'otp_verified',
          requires_otp: false,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ access_token: 'access-nav', refresh_token: 'refresh-nav' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ can_connect_telegram: false, telegram_linked: false }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper
      .get('textarea[autocomplete="street-address"]')
      .setValue('تهران، خیابان مثال، پلاک ۱۲۳')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تکمیل ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('تکمیل ثبت‌نام اکنون ممکن نشد.')
    expect(localStorage.getItem('auth_token')).toBe('access-nav')
    expect(
      webRegisterMocks.fetch.mock.calls.some(
        ([url]) => url === '/api/auth/registration-context/clear',
      ),
    ).toBe(false)
    expect(wrapper.get('textarea[autocomplete="street-address"]').element).toHaveProperty(
      'value',
      'تهران، خیابان مثال، پلاک ۱۲۳',
    )
  })

  it('shows backend verification errors and local address validation errors', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تایید ارسال شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد نادرست است' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister, { attachTo: document.body })
    await flushPromises()

    await wrapper.get('button').trigger('click')
    await flushPromises()
    const otpInput = wrapper.get('input[autocomplete="one-time-code"]')
    await otpInput.setValue('54321')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تأیید و ادامه'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کد نادرست است')
    expect((otpInput.element as HTMLInputElement).value).toBe('54321')
    expect(document.activeElement).toBe(otpInput.element)
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('بازگشت به فرم'))!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('کد نادرست است')
    expect((otpInput.element as HTMLInputElement).value).toBe('54321')
    ;(wrapper.vm as unknown as { step: number }).step = 3
    await flushPromises()
    const addressInput = wrapper.get('textarea[autocomplete="street-address"]')
    await addressInput.setValue('کوتاه')
    await (
      wrapper.vm as unknown as { submitRegistration: () => Promise<void> }
    ).submitRegistration()
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس باید حداقل ۱۰ کاراکتر باشد.')
    expect((addressInput.element as HTMLTextAreaElement).value).toBe('کوتاه')
    expect(document.activeElement).toBe(addressInput.element)
    wrapper.unmount()
  })

  it('retries the exact failed OTP verification and preserves the entered code', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تایید ارسال شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تایید شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('13579')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تأیید و ادامه'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ارتباط با سرور برقرار نشد')
    expect((wrapper.vm as unknown as { otpCode: string }).otpCode).toBe('13579')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تلاش مجدد'))!
      .trigger('click')
    await flushPromises()

    const verifyCalls = webRegisterMocks.fetch.mock.calls.filter(
      ([url]) => url === '/api/auth/registration-context/otp/verify',
    )
    expect(verifyCalls).toHaveLength(2)
    expect(verifyCalls.map(([, options]) => JSON.parse(String(options?.body)))).toEqual([
      { code: '13579' },
      { code: '13579' },
    ])
    expect(wrapper.text()).toContain('نشانی دقیق پستی')
  })

  it('does not advance or duplicate an OTP request without an authoritative receipt', async () => {
    let resolveOtp!: (response: Response) => void
    const pendingOtp = new Promise<Response>((resolve) => {
      resolveOtp = resolve
    })
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockReturnValueOnce(pendingOtp)

    const wrapper = mount(WebRegister)
    await flushPromises()
    const registrationView = wrapper.vm as unknown as {
      requestOtp: () => Promise<void>
      step: number
    }
    const first = registrationView.requestOtp()
    const duplicate = registrationView.requestOtp()

    expect(webRegisterMocks.fetch).toHaveBeenCalledTimes(2)
    resolveOtp(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await Promise.all([first, duplicate])
    await flushPromises()

    expect(registrationView.step).toBe(1)
    expect(wrapper.text()).toContain('پاسخ ارسال کد تایید کامل نیست')
  })

  it('uses cause-neutral copy for a malformed successful OTP response', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockResolvedValueOnce(
        new Response('{not-json', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ارسال کد تایید اکنون ممکن نشد')
    expect(wrapper.text()).not.toContain('Unexpected')
  })

  it('turns a terminal cookie-context failure into a login escape instead of a form loop', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'context expired' }), {
          status: 410,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('دریافت کد تأیید'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('جلسه ثبت‌نام نامعتبر یا منقضی شده است.')
    expect(wrapper.text()).toContain('بازگشت به ورود')
    expect(wrapper.text()).not.toContain('بازگشت به فرم')
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)
  })

  it('bounds Telegram link creation and always releases its busy state', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockImplementationOnce(
        (_url: string, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener('abort', () => reject(options.signal?.reason), {
              once: true,
            })
          }),
      )

    const wrapper = mount(WebRegister, { attachTo: document.body })
    await flushPromises()
    ;(wrapper.vm as unknown as { step: number }).step = 4
    await flushPromises()
    expect(
      wrapper.get('[aria-labelledby="registration-complete-title"]').attributes('tabindex'),
    ).toBe('-1')
    expect(document.activeElement).toBe(
      wrapper.get('[aria-labelledby="registration-complete-title"]').element,
    )
    vi.useFakeTimers()

    const connectButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('اتصال به ربات تلگرام'))!
    void connectButton.trigger('click')
    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()

    expect(webRegisterMocks.fetch).toHaveBeenCalledWith(
      '/api/auth/telegram-link-token',
      expect.objectContaining({
        method: 'POST',
        signal: expect.any(AbortSignal),
      }),
    )
    expect(wrapper.text()).toContain('زمان انتظار برای دریافت پاسخ به پایان رسید')
    expect((wrapper.vm as unknown as { telegramLinkBusy: boolean }).telegramLinkBusy).toBe(false)
    wrapper.unmount()
  })

  it('keeps the optional Telegram step retryable when Home navigation fails', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.fetch.mockResolvedValueOnce(makeRegistrationContextResponse())
    webRegisterMocks.replace.mockRejectedValueOnce(new Error('home unavailable'))

    const wrapper = mount(WebRegister)
    await flushPromises()
    ;(wrapper.vm as unknown as { step: number }).step = 4
    await flushPromises()

    const skipButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('فعلاً رد می‌کنم'))!
    await skipButton.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('بازگشت به خانه اکنون ممکن نشد. دوباره تلاش کنید.')
    expect(skipButton.attributes('disabled')).toBeUndefined()
    expect(webRegisterMocks.replace).toHaveBeenCalledTimes(1)
  })

  it('loads the registration session flow after login OTP verification', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.route.query.registration_token = 'REG-123'

    webRegisterMocks.fetch
      .mockResolvedValueOnce(
        makeRegistrationContextResponse({
          kind: 'registration',
          progress: 'otp_verified',
          requires_otp: false,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ access_token: 'access-2', refresh_token: 'refresh-2' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ can_connect_telegram: false, telegram_linked: false }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('نشانی دقیق پستی')
    expect(wrapper.find('.ui-v2-auth-progress').exists()).toBe(false)
    expect(wrapper.text()).toContain('test_user')
    await wrapper
      .get('textarea[autocomplete="street-address"]')
      .setValue('تهران، خیابان مثال، پلاک ۹۹')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تکمیل ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(localStorage.getItem('auth_token')).toBe('access-2')
    expect(localStorage.getItem('refresh_token')).toBe('refresh-2')
  })

  it('escapes to login when a lost completion response is recovered by the terminal marker', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.fetch
      .mockResolvedValueOnce(
        makeRegistrationContextResponse({
          kind: 'registration',
          progress: 'otp_verified',
          requires_otp: false,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: 'registration_complete' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper
      .get('textarea[autocomplete="street-address"]')
      .setValue('تهران، خیابان مثال، پلاک ۵۵')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تکمیل ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(webRegisterMocks.replace).toHaveBeenCalledWith({
      name: 'login',
      query: { registration: 'complete' },
    })
    expect(webRegisterMocks.fetch).toHaveBeenCalledWith(
      '/api/auth/registration-context/clear',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
    expect(localStorage.getItem('auth_token')).toBeNull()
    expect(wrapper.text()).not.toContain('توکن')
  })

  it('retains the completion marker until login navigation succeeds', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.replace
      .mockResolvedValueOnce({ type: 4, to: { name: 'login' } })
      .mockResolvedValueOnce(undefined)
    webRegisterMocks.fetch
      .mockResolvedValueOnce(
        makeRegistrationContextResponse({
          kind: 'registration',
          progress: 'otp_verified',
          requires_otp: false,
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: 'registration_complete' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: 'registration_complete' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper
      .get('textarea[autocomplete="street-address"]')
      .setValue('تهران، خیابان مثال، پلاک ۶۶')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تکمیل ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('تکمیل ثبت‌نام اکنون ممکن نشد.')
    expect(
      webRegisterMocks.fetch.mock.calls.some(
        ([url]) => url === '/api/auth/registration-context/clear',
      ),
    ).toBe(false)

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تلاش مجدد'))!
      .trigger('click')
    await flushPromises()

    expect(webRegisterMocks.replace).toHaveBeenCalledTimes(2)
    expect(webRegisterMocks.fetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/auth/registration-context',
      '/api/auth/registration-context/complete',
      '/api/auth/registration-context/complete',
      '/api/auth/registration-context/clear',
    ])
    expect(localStorage.getItem('auth_token')).toBeNull()
  })

  it('fails closed on a terminal exchange response without rendering registration fields', async () => {
    webRegisterMocks.fetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'دعوت‌نامه قبلاً استفاده شده است' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('جلسه ثبت‌نام نامعتبر یا منقضی شده است.')
    expect(wrapper.text()).toContain('بازگشت به ورود')
    expect(wrapper.find('textarea[autocomplete="street-address"]').exists()).toBe(false)
  })

  it('continues Back/Forward or refresh from cookie progress without a raw handoff', async () => {
    delete webRegisterMocks.route.query.token
    webRegisterMocks.fetch.mockResolvedValueOnce(
      makeRegistrationContextResponse({ progress: 'otp_requested' }),
    )

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(webRegisterMocks.fetch).toHaveBeenCalledWith(
      '/api/auth/registration-context',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
    expect(wrapper.text()).toContain('کد تأیید پنج‌رقمی')
    expect(wrapper.text()).toContain('مرحله ۲ از ۳')
    expect(JSON.stringify(webRegisterMocks.fetch.mock.calls)).not.toMatch(
      /INV-|REG-|registration_token/,
    )
  })

  it('requires exactly five ASCII digits and returns focus without discarding the code', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تأیید ارسال شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister, { attachTo: document.body })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('دریافت کد تأیید'))!
      .trigger('click')
    await flushPromises()

    const otpInput = wrapper.get('input[autocomplete="one-time-code"]')
    await otpInput.setValue('12a45')
    expect(
      wrapper
        .findAll('button')
        .find((button) => button.text().includes('تأیید و ادامه'))!
        .attributes('disabled'),
    ).toBeDefined()

    await (wrapper.vm as unknown as { verifyOtp: () => Promise<void> }).verifyOtp()
    await flushPromises()

    expect(wrapper.text()).toContain('کد تأیید باید دقیقاً پنج رقم باشد.')
    expect((otpInput.element as HTMLInputElement).value).toBe('12a45')
    expect(document.activeElement).toBe(otpInput.element)
    expect(webRegisterMocks.fetch).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('keeps invitation drafts when moving back between the three required steps', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(makeRegistrationContextResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تأیید ارسال شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'کد تأیید شد' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('دریافت کد تأیید'))!
      .trigger('click')
    await flushPromises()
    await wrapper.get('input[autocomplete="one-time-code"]').setValue('24680')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تأیید و ادامه'))!
      .trigger('click')
    await flushPromises()
    await wrapper
      .get('textarea[autocomplete="street-address"]')
      .setValue('تهران، خیابان مثال، پلاک ۲۴')

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('بازگشت به کد تأیید'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('مرحله ۲ از ۳')
    expect(
      (wrapper.get('input[autocomplete="one-time-code"]').element as HTMLInputElement).value,
    ).toBe('24680')
    ;(wrapper.vm as unknown as { step: number }).step = 3
    await flushPromises()
    expect(
      (wrapper.get('textarea[autocomplete="street-address"]').element as HTMLTextAreaElement).value,
    ).toBe('تهران، خیابان مثال، پلاک ۲۴')
  })
})
