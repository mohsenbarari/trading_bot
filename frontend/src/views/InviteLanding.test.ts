import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import InviteLanding from './InviteLanding.vue'

const inviteLandingMocks = vi.hoisted(() => ({
  route: { params: { code: 'abc123' } },
  push: vi.fn(),
  fetch: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => inviteLandingMocks.route,
  useRouter: () => ({ push: inviteLandingMocks.push }),
}))

describe('InviteLanding.vue', () => {
  beforeEach(() => {
    inviteLandingMocks.route.params.code = 'abc123'
    inviteLandingMocks.push.mockReset()
    inviteLandingMocks.fetch.mockReset()
    vi.stubGlobal('fetch', inviteLandingMocks.fetch)
  })

  it('loads the invitation and config, renders both registration actions, and routes web registration', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(new Response(JSON.stringify({ token: 'token-123' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ bot_username: 'mbmtrading1_bot' }), { status: 200, headers: { 'Content-Type': 'application/json' } }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(1, '/api/invitations/lookup/abc123')
    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(2, '/api/config')
    expect(wrapper.text()).toContain('شما به سامانه معاملاتی دعوت شده‌اید.')
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
})
