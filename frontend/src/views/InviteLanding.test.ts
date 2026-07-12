import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import InviteLanding from './InviteLanding.vue'

const inviteLandingMocks = vi.hoisted(() => ({
  route: { params: { code: 'abc123' } },
  push: vi.fn(),
  replace: vi.fn(),
  fetch: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => inviteLandingMocks.route,
  useRouter: () => ({ push: inviteLandingMocks.push, replace: inviteLandingMocks.replace }),
}))

describe('InviteLanding.vue', () => {
  beforeEach(() => {
    inviteLandingMocks.route.params.code = 'abc123'
    inviteLandingMocks.push.mockReset()
    inviteLandingMocks.replace.mockReset()
    inviteLandingMocks.fetch.mockReset()
    vi.stubGlobal('fetch', inviteLandingMocks.fetch)
  })

  it('loads the invitation and config, renders both registration actions, and routes web registration', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        token: 'token-123',
        expires_at: '2026-07-14T10:00:00Z',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ bot_username: 'mbmtrading1_bot' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(1, '/api/invitations/lookup/abc123')
    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(2, '/api/config')
    expect(wrapper.text()).toContain('شما به سامانه معاملاتی دعوت شده‌اید.')
    expect(wrapper.text()).toContain('مهلت ثبت‌نام:')
    expect(wrapper.get('a.telegram-btn').attributes('href')).toBe('https://t.me/mbmtrading1_bot?start=token-123')

    await wrapper.findAll('button').find((button) => button.text().includes('ثبت‌نام از طریق وب'))!.trigger('click')
    expect(inviteLandingMocks.push).toHaveBeenCalledWith('/register?token=token-123')
  })

  it('shows a friendly error when invitation lookup fails', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(new Response(null, { status: 404 }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.text()).toContain('دعوت‌نامه نامعتبر یا منقضی شده است.')
    expect(wrapper.find('.actions').exists()).toBe(false)
  })

  it('renders only the Web path when the v2 contract marks Telegram unavailable', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(new Response(JSON.stringify({
      token: 'accountant-token',
      valid: true,
      state: 'pending',
      bot_available: false,
      web_available: true,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(inviteLandingMocks.fetch).toHaveBeenCalledTimes(1)
    expect(wrapper.find('a.telegram-btn').exists()).toBe(false)
    expect(wrapper.text()).toContain('ثبت‌نام از طریق وب')
  })

  it('routes a completed invitation to OTP login without showing registration actions', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(new Response(JSON.stringify({
      valid: false,
      state: 'completed',
      bot_available: false,
      web_available: false,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(inviteLandingMocks.replace).toHaveBeenCalledWith({ name: 'login', query: { registration: 'complete' } })
    expect(wrapper.find('.actions').exists()).toBe(false)
    expect(inviteLandingMocks.fetch).toHaveBeenCalledTimes(1)
  })

  it('keeps Web registration available when Telegram config cannot be loaded', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        token: 'token-123',
        valid: true,
        state: 'pending',
        bot_available: true,
        web_available: true,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.find('a.telegram-btn').exists()).toBe(false)
    expect(wrapper.text()).toContain('ثبت‌نام از طریق وب')
    expect(wrapper.text()).not.toContain('دعوت‌نامه قابل استفاده نیست')
  })

  it('disables only Telegram when config has no bot username', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({
        token: 'token-123',
        valid: true,
        state: 'pending',
        bot_available: true,
        web_available: true,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.find('a.telegram-btn').exists()).toBe(false)
    expect(wrapper.text()).toContain('ثبت‌نام از طریق وب')
  })

  it('renders the bounded terminal message for an expired invitation', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(new Response(JSON.stringify({
      valid: false,
      state: 'expired',
      bot_available: false,
      web_available: false,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.text()).toContain('مهلت ثبت‌نام پایان یافته است. لطفاً دعوت‌نامه جدید دریافت کنید.')
    expect(wrapper.find('.actions').exists()).toBe(false)
  })

  it('fails closed when a pending response omits its token', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(new Response(JSON.stringify({
      valid: true,
      state: 'pending',
      bot_available: false,
      web_available: true,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.text()).toContain('دعوت‌نامه نامعتبر یا منقضی شده است.')
    expect(wrapper.find('.actions').exists()).toBe(false)
  })
})
