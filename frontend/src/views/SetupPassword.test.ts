import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SetupPassword from './SetupPassword.vue'

const setupPasswordMocks = vi.hoisted(() => ({
  replace: vi.fn(),
  apiFetch: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ replace: setupPasswordMocks.replace }),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: setupPasswordMocks.apiFetch,
}))

describe('SetupPassword.vue', () => {
  beforeEach(() => {
    setupPasswordMocks.replace.mockReset().mockResolvedValue(undefined)
    setupPasswordMocks.apiFetch.mockReset()
  })

  it('shows a validation error when the password does not meet the security rules', async () => {
    const wrapper = mount(SetupPassword, { attachTo: document.body })
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('weak')
    await inputs[1]!.setValue('weak')
    await wrapper.get('form').trigger('submit.prevent')

    expect(wrapper.text()).toContain('الزامات امنیتی رمز عبور رعایت نشده است')
    expect(setupPasswordMocks.apiFetch).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(inputs[0]!.element)
    wrapper.unmount()
  })

  it('shows an error when the password confirmation does not match', async () => {
    const wrapper = mount(SetupPassword, { attachTo: document.body })
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1?')
    await wrapper.get('form').trigger('submit.prevent')

    expect(wrapper.text()).toContain('رمز عبور و تکرار آن یکسان نیستند')
    expect(setupPasswordMocks.apiFetch).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(inputs[1]!.element)
    wrapper.unmount()
  })

  it('uses password-manager metadata and exposes neutral then explicit rule states', async () => {
    const wrapper = mount(SetupPassword, { attachTo: document.body })
    const inputs = wrapper.findAll('input')

    expect(inputs[0]!.attributes('autocomplete')).toBe('new-password')
    expect(inputs[1]!.attributes('autocomplete')).toBe('new-password')
    expect(wrapper.text()).toContain('حداقل ۸ کاراکتر — بررسی‌نشده')

    await inputs[0]!.setValue('StrongPass1!')
    expect(wrapper.text()).toContain('حداقل ۸ کاراکتر — تأیید')
    expect(wrapper.text()).not.toContain('نیازمند اصلاح')

    wrapper.unmount()
  })

  it('submits a valid password and redirects to the app root', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'رمز عبور با موفقیت ثبت شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(setupPasswordMocks.apiFetch).toHaveBeenCalledWith(
      '/api/auth/setup-password',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ password: 'StrongPass1!' }),
        retryNetwork: false,
        trackConnectionState: false,
        signal: expect.any(AbortSignal),
      }),
    )
    expect(setupPasswordMocks.replace).toHaveBeenCalledWith('/')
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('')
  })

  it('retries a rejected Home transition without submitting the accepted password again', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'رمز عبور با موفقیت ثبت شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    setupPasswordMocks.replace.mockRejectedValueOnce(new Error('router internals leaked'))
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.findAll('.ui-v2-auth-password-toggle')[0]!.trigger('click')
    expect(inputs[0]!.attributes('type')).toBe('text')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('رمز عبور ثبت شد، اما ورود به سامانه اکنون ممکن نشد')
    expect(wrapper.text()).not.toContain('router internals')
    expect(wrapper.get('button[type="submit"]').text()).toContain('تلاش دوباره برای ورود')
    expect(inputs[0]!.attributes('type')).toBe('password')
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('StrongPass1!')

    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(setupPasswordMocks.apiFetch).toHaveBeenCalledTimes(1)
    expect(setupPasswordMocks.replace).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('ورود به سامانه اکنون ممکن نشد')
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('')
  })

  it('treats a resolved NavigationFailure as retryable without duplicating the POST', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'رمز عبور با موفقیت ثبت شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    setupPasswordMocks.replace.mockResolvedValueOnce({ type: 4, to: '/' })
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('رمز عبور ثبت شد، اما ورود به سامانه اکنون ممکن نشد')
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('StrongPass1!')

    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(setupPasswordMocks.apiFetch).toHaveBeenCalledTimes(1)
    expect(setupPasswordMocks.replace).toHaveBeenCalledTimes(2)
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('')
  })

  it('surfaces API detail errors and resets loading state after a failed submit', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'server rejected password' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('server rejected password')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    expect(setupPasswordMocks.replace).not.toHaveBeenCalled()
  })

  it('preserves both password fields and clears busy after a network failure', async () => {
    setupPasswordMocks.apiFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('ارتباط با سرور برقرار نشد')
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect(wrapper.get('button[type="submit"]').attributes('disabled')).toBeUndefined()
    expect(setupPasswordMocks.replace).not.toHaveBeenCalled()
  })

  it('requires the authoritative success receipt before redirecting', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('پاسخ ثبت رمز عبور کامل نیست')
    expect(setupPasswordMocks.replace).not.toHaveBeenCalled()
  })

  it('uses cause-neutral copy for malformed successful JSON and preserves the draft', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValueOnce(
      new Response('{not-json', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')
    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('ثبت رمز عبور اکنون ممکن نشد')
    expect(wrapper.text()).not.toContain('Unexpected')
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect(setupPasswordMocks.replace).not.toHaveBeenCalled()
  })

  it('keeps method failures cause-neutral without exposing backend metadata', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'Method Not Allowed' }), {
        status: 405,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('ثبت رمز عبور اکنون ممکن نشد')
    expect(wrapper.text()).not.toMatch(/405|Method|API|مسیر/u)
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('StrongPass1!')
    expect(setupPasswordMocks.replace).not.toHaveBeenCalled()
  })

  it('guards duplicate password submissions while the first request is pending', async () => {
    let resolveRequest!: (response: Response) => void
    setupPasswordMocks.apiFetch.mockReturnValueOnce(
      new Promise<Response>((resolve) => {
        resolveRequest = resolve
      }),
    )
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')
    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')

    const passwordView = wrapper.vm as unknown as { submitPassword: () => Promise<void> }
    const first = passwordView.submitPassword()
    const duplicate = passwordView.submitPassword()
    expect(setupPasswordMocks.apiFetch).toHaveBeenCalledTimes(1)

    resolveRequest(
      new Response(JSON.stringify({ detail: 'رمز عبور با موفقیت ثبت شد' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await Promise.all([first, duplicate])
    await flushPromises()

    expect(setupPasswordMocks.replace).toHaveBeenCalledTimes(1)
  })
})
