import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import WebRegister from './WebRegister.vue'

const webRegisterMocks = vi.hoisted(() => ({
  route: { query: { token: 'invite-token' } },
  replace: vi.fn(),
  fetch: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => webRegisterMocks.route,
  useRouter: () => ({ replace: webRegisterMocks.replace }),
}))

describe('WebRegister.vue', () => {
  beforeEach(() => {
    webRegisterMocks.route.query.token = 'invite-token'
    delete (webRegisterMocks.route.query as any).registration_token
    webRegisterMocks.replace.mockReset()
    webRegisterMocks.fetch.mockReset()
    localStorage.clear()
    vi.stubGlobal('fetch', webRegisterMocks.fetch)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('shows an immediate error when the registration token is missing', async () => {
    webRegisterMocks.route.query.token = undefined as any

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('توکن دعوت یافت نشد.')
    expect(webRegisterMocks.fetch).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('تلاش مجدد')
  })

  it('completes the full invite validation, OTP, and registration flow', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        account_name: 'test_user',
        mobile_number: '09120000000',
        role: 'عادی',
        expires_at: '2026-07-14T10:00:00Z',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'کد تایید ارسال شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'کد تایید شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'access-1', refresh_token: 'refresh-1' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ can_connect_telegram: false, telegram_linked: false }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('نام کاربری:')
    expect(wrapper.text()).toContain('مهلت ثبت‌نام:')
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کد تایید ۵ رقمی را وارد کنید:')
    await wrapper.get('input.otp-input').setValue('12345')
    await wrapper.findAll('button').find((button) => button.text().includes('تایید کد'))!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس دقیق پستی:')
    expect(wrapper.text()).toContain('مهلت ثبت‌نام:')
    await wrapper.get('textarea.address-input').setValue('تهران، خیابان مثال، پلاک ۱۲۳')
    await wrapper.findAll('button').find((button) => button.text().includes('تکمیل ثبت‌نام'))!.trigger('click')
    await flushPromises()

    expect(localStorage.getItem('auth_token')).toBe('access-1')
    expect(localStorage.getItem('refresh_token')).toBe('refresh-1')
    expect(webRegisterMocks.replace).toHaveBeenCalledWith('/')
  })

  it('shows backend verification errors and local address validation errors', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ account_name: 'test_user', mobile_number: '09120000000', role: 'عادی' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'کد تایید ارسال شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'کد نادرست است' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }))

    const wrapper = mount(WebRegister)
    await flushPromises()

    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('input.otp-input').setValue('54321')
    await wrapper.findAll('button').find((button) => button.text().includes('تایید کد'))!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کد نادرست است')
    await wrapper.findAll('button').find((button) => button.text().includes('بازگشت به فرم'))!.trigger('click')
    expect(wrapper.text()).not.toContain('کد نادرست است')

    ;(wrapper.vm as any).step = 3
    await flushPromises()
    await wrapper.get('textarea.address-input').setValue('کوتاه')
    await (wrapper.vm as any).submitRegistration()
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس باید حداقل ۱۰ کاراکتر باشد.')
  })

  it('retries the exact failed OTP verification and preserves the entered code', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        account_name: 'test_user',
        mobile_number: '09120000000',
        role: 'عادی',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'کد تایید ارسال شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'کد تایید شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await wrapper.get('input.otp-input').setValue('13579')
    await wrapper.findAll('button').find((button) => button.text().includes('تایید کد'))!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ارتباط با سرور برقرار نشد')
    expect((wrapper.vm as any).otpCode).toBe('13579')
    await wrapper.findAll('button').find((button) => button.text().includes('تلاش مجدد'))!.trigger('click')
    await flushPromises()

    const verifyCalls = webRegisterMocks.fetch.mock.calls.filter(([url]) => url === '/api/auth/register-otp-verify')
    expect(verifyCalls).toHaveLength(2)
    expect(verifyCalls.map(([, options]) => JSON.parse(String(options?.body)))).toEqual([
      { token: 'invite-token', code: '13579' },
      { token: 'invite-token', code: '13579' },
    ])
    expect(wrapper.text()).toContain('آدرس دقیق پستی:')
  })

  it('does not advance or duplicate an OTP request without an authoritative receipt', async () => {
    let resolveOtp!: (response: Response) => void
    const pendingOtp = new Promise<Response>((resolve) => {
      resolveOtp = resolve
    })
    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        account_name: 'test_user',
        mobile_number: '09120000000',
        role: 'عادی',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockReturnValueOnce(pendingOtp)

    const wrapper = mount(WebRegister)
    await flushPromises()
    const first = (wrapper.vm as any).requestOtp()
    const duplicate = (wrapper.vm as any).requestOtp()

    expect(webRegisterMocks.fetch).toHaveBeenCalledTimes(2)
    resolveOtp(new Response(JSON.stringify({}), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await Promise.all([first, duplicate])
    await flushPromises()

    expect((wrapper.vm as any).step).toBe(1)
    expect(wrapper.text()).toContain('پاسخ ارسال کد تایید کامل نیست')
  })

  it('uses cause-neutral copy for a malformed successful OTP response', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        account_name: 'test_user',
        mobile_number: '09120000000',
        role: 'عادی',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response('{not-json', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const wrapper = mount(WebRegister)
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ارسال کد تایید اکنون ممکن نشد')
    expect(wrapper.text()).not.toContain('Unexpected')
  })

  it('bounds Telegram link creation and always releases its busy state', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        account_name: 'test_user',
        mobile_number: '09120000000',
        role: 'عادی',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockImplementationOnce((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
        options.signal?.addEventListener('abort', () => reject(options.signal?.reason), { once: true })
      }))

    const wrapper = mount(WebRegister)
    await flushPromises()
    ;(wrapper.vm as any).step = 4
    await flushPromises()
    vi.useFakeTimers()

    const connectButton = wrapper.findAll('button').find((button) => button.text().includes('اتصال به ربات تلگرام'))!
    void connectButton.trigger('click')
    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()

    expect(webRegisterMocks.fetch).toHaveBeenCalledWith('/api/auth/telegram-link-token', expect.objectContaining({
      method: 'POST',
      signal: expect.any(AbortSignal),
    }))
    expect(wrapper.text()).toContain('زمان انتظار برای دریافت پاسخ به پایان رسید')
    expect((wrapper.vm as any).telegramLinkBusy).toBe(false)
  })

  it('loads the registration session flow after login OTP verification', async () => {
    delete (webRegisterMocks.route.query as any).token
    ;(webRegisterMocks.route.query as any).registration_token = 'REG-123'

    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ account_name: 'test_user', mobile_number: '09120000000', role: 'عادی' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: 'access-2', refresh_token: 'refresh-2' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس دقیق پستی:')
    expect(wrapper.text()).toContain('test_user')
    await wrapper.get('textarea.address-input').setValue('تهران، خیابان مثال، پلاک ۹۹')
    await wrapper.findAll('button').find((button) => button.text().includes('تکمیل ثبت‌نام'))!.trigger('click')
    await flushPromises()

    expect(localStorage.getItem('auth_token')).toBe('access-2')
    expect(localStorage.getItem('refresh_token')).toBe('refresh-2')
  })

  it('routes a Telegram-completed invitation to OTP login before rendering a duplicate form', async () => {
    webRegisterMocks.fetch.mockResolvedValueOnce(new Response(JSON.stringify({
      valid: false,
      state: 'completed',
      bot_available: false,
      web_available: false,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(webRegisterMocks.replace).toHaveBeenCalledWith({ name: 'login', query: { registration: 'complete' } })
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.find('textarea.address-input').exists()).toBe(false)
  })

  it('rejects a pending contract when Web registration is unavailable', async () => {
    webRegisterMocks.fetch.mockResolvedValueOnce(new Response(JSON.stringify({
      token: 'telegram-only',
      valid: true,
      state: 'pending',
      bot_available: true,
      web_available: false,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('دعوت‌نامه نامعتبر یا منقضی شده است.')
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('تلاش مجدد')
  })
})
