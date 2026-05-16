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
    setupPasswordMocks.replace.mockReset()
    setupPasswordMocks.apiFetch.mockReset()
  })

  it('shows a validation error when the password does not meet the security rules', async () => {
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('weak')
    await inputs[1]!.setValue('weak')
    await wrapper.get('form').trigger('submit.prevent')

    expect(wrapper.text()).toContain('الزامات امنیتی رمز عبور رعایت نشده است')
    expect(setupPasswordMocks.apiFetch).not.toHaveBeenCalled()
  })

  it('shows an error when the password confirmation does not match', async () => {
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1?')
    await wrapper.get('form').trigger('submit.prevent')

    expect(wrapper.text()).toContain('رمز عبور و تکرار آن یکسان نیستند')
    expect(setupPasswordMocks.apiFetch).not.toHaveBeenCalled()
  })

  it('submits a valid password and redirects to the app root', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValue(new Response(null, { status: 200 }))
    const wrapper = mount(SetupPassword)
    const inputs = wrapper.findAll('input')

    await inputs[0]!.setValue('StrongPass1!')
    await inputs[1]!.setValue('StrongPass1!')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(setupPasswordMocks.apiFetch).toHaveBeenCalledWith('/api/auth/setup-password', {
      method: 'POST',
      body: JSON.stringify({ password: 'StrongPass1!' }),
    })
    expect(setupPasswordMocks.replace).toHaveBeenCalledWith('/')
  })

  it('surfaces API detail errors and resets loading state after a failed submit', async () => {
    setupPasswordMocks.apiFetch.mockResolvedValue(new Response(JSON.stringify({ detail: 'server rejected password' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    }))
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
})