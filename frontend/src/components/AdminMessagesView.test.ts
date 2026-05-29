import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const adminMessagesMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: adminMessagesMocks.apiFetchMock,
}))

function responseOf(data: unknown) {
  return {
    ok: true,
    json: async () => data,
  }
}

describe('AdminMessagesView.vue', () => {
  beforeEach(() => {
    adminMessagesMocks.apiFetchMock.mockReset()
    adminMessagesMocks.apiFetchMock.mockImplementation(async (path: string) => {
      if (path.startsWith('/api/admin-messages/market/history')) {
        return responseOf([
          {
            id: 1,
            content: 'پیام قبلی بازار',
            is_active: true,
            notified_recipients_count: 3,
            published_at: '2026-05-29T08:00:00Z',
          },
        ])
      }
      if (path.startsWith('/api/admin-messages/broadcasts/history')) {
        return responseOf([
          {
            id: 2,
            content: 'پیام قبلی همگانی',
            target_groups: ['users', 'customers'],
            recipient_count: 4,
            published_at: '2026-05-29T09:00:00Z',
          },
        ])
      }
      if (path === '/api/admin-messages/market') {
        return responseOf({ notified_recipients_count: 5 })
      }
      if (path === '/api/admin-messages/broadcasts') {
        return responseOf({ recipient_count: 6 })
      }
      return responseOf(null)
    })
  })

  it('loads histories, reuses old messages, and publishes new management messages', async () => {
    const AdminMessagesView = (await import('./AdminMessagesView.vue')).default
    const wrapper = mount(AdminMessagesView)
    await flushPromises()

    expect(wrapper.text()).toContain('پیام قبلی بازار')
    expect(wrapper.text()).toContain('پیام قبلی همگانی')

    const reuseButtons = wrapper.findAll('button').filter((button) => button.text().includes('استفاده مجدد'))
    await reuseButtons[0]!.trigger('click')
    await reuseButtons[1]!.trigger('click')

    const textareas = wrapper.findAll('textarea')
    expect((textareas[0]!.element as HTMLTextAreaElement).value).toBe('پیام قبلی بازار')
    expect((textareas[1]!.element as HTMLTextAreaElement).value).toBe('پیام قبلی همگانی')

    await wrapper.findAll('.primary-action')[0]!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.primary-action')[1]!.trigger('click')
    await flushPromises()

    const marketCall = adminMessagesMocks.apiFetchMock.mock.calls.find(([path]) => path === '/api/admin-messages/market')
    const broadcastCall = adminMessagesMocks.apiFetchMock.mock.calls.find(([path]) => path === '/api/admin-messages/broadcasts')
    expect(JSON.parse(String(marketCall?.[1]?.body))).toEqual({ content: 'پیام قبلی بازار' })
    expect(JSON.parse(String(broadcastCall?.[1]?.body))).toEqual({
      content: 'پیام قبلی همگانی',
      target_groups: ['users', 'customers'],
    })
    expect(wrapper.text()).toContain('پیام بازار برای ۵ نفر اعلان شد')
    expect(wrapper.text()).toContain('پیام برای ۶ نفر ارسال شد')
  })
})
