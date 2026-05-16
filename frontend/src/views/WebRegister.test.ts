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

    const wrapper = mount(WebRegister)
    await flushPromises()

    expect(wrapper.text()).toContain('نام کاربری:')
    await wrapper.get('button.btn.primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کد تایید ۵ رقمی را وارد کنید:')
    await wrapper.get('input.otp-input').setValue('12345')
    await wrapper.get('button.btn.primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس دقیق پستی:')
    await wrapper.get('textarea.address-input').setValue('تهران، خیابان مثال، پلاک ۱۲۳')
    await wrapper.get('button.btn.primary').trigger('click')
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

    await wrapper.get('button.btn.primary').trigger('click')
    await flushPromises()
    await wrapper.get('input.otp-input').setValue('54321')
    await wrapper.get('button.btn.primary').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('کد نادرست است')
    await wrapper.get('button.btn.secondary').trigger('click')
    expect(wrapper.text()).not.toContain('کد نادرست است')

    ;(wrapper.vm as any).step = 3
    await flushPromises()
    await wrapper.get('textarea.address-input').setValue('کوتاه')
    await (wrapper.vm as any).submitRegistration()
    await flushPromises()

    expect(wrapper.text()).toContain('آدرس باید حداقل ۱۰ کاراکتر باشد.')
  })
})