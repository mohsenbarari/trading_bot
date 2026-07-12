import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

  it('shows an immediate error when the registration token is missing', async () => {
    webRegisterMocks.route.query.token = undefined as any

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('توکن دعوت یافت نشد.')
    expect(webRegisterMocks.fetch).not.toHaveBeenCalled()
  })

  it('completes the full invite validation, OTP, and registration flow', async () => {
    webRegisterMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ account_name: 'test_user', mobile_number: '09120000000', role: 'عادی' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
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
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کد تایید ۵ رقمی را وارد کنید:')
    await wrapper.get('input.otp-input').setValue('12345')
    await wrapper.findAll('button').find((button) => button.text().includes('تایید کد'))!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس دقیق پستی:')
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
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
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
    await wrapper.findAll('button').find((button) => button.text().includes('تلاش مجدد'))!.trigger('click')
    expect(wrapper.text()).not.toContain('کد نادرست است')

    ;(wrapper.vm as any).step = 3
    await flushPromises()
    await wrapper.get('textarea.address-input').setValue('کوتاه')
    await (wrapper.vm as any).submitRegistration()
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس باید حداقل ۱۰ کاراکتر باشد.')
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
  })
})
