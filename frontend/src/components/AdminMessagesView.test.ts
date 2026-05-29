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
    const currentPublishedAt = '2026-05-29T08:00:00Z'
    let currentMarketMessage: Record<string, unknown> | null = {
      id: 1,
      content: 'پیام فعال بازار',
      is_active: true,
      notified_recipients_count: 3,
      published_at: currentPublishedAt,
      created_at: currentPublishedAt,
      created_by_id: 99,
      created_by_name: 'admin',
      reused_from_id: null,
    }
    let marketHistory = [
      { ...(currentMarketMessage as Record<string, unknown>) },
      {
        id: 11,
        content: 'پیام قبلی بازار',
        is_active: false,
        notified_recipients_count: 1,
        published_at: '2026-05-28T08:00:00Z',
        created_at: '2026-05-28T08:00:00Z',
        created_by_id: 99,
        created_by_name: 'admin',
        reused_from_id: null,
      },
    ]
    let broadcastHistory = [
      {
        id: 2,
        content: 'پیام قبلی همگانی',
        target_groups: ['users', 'customers'],
        recipient_count: 4,
        published_at: '2026-05-29T09:00:00Z',
        created_at: '2026-05-29T09:00:00Z',
        created_by_id: 99,
        created_by_name: 'admin',
      },
    ]

    adminMessagesMocks.apiFetchMock.mockReset()
    adminMessagesMocks.apiFetchMock.mockImplementation(async (path: string, init?: RequestInit) => {
      const method = init?.method || 'GET'
      if (path === '/api/admin-messages/market/current' && method === 'GET') {
        return responseOf(currentMarketMessage)
      }
      if (path.startsWith('/api/admin-messages/market/history')) {
        return responseOf(marketHistory)
      }
      if (path.startsWith('/api/admin-messages/broadcasts/history')) {
        return responseOf(broadcastHistory)
      }
      if (path === '/api/admin-messages/market' && method === 'POST') {
        const payload = JSON.parse(String(init?.body || '{}'))
        currentMarketMessage = {
          id: 99,
          content: payload.content,
          is_active: true,
          notified_recipients_count: 5,
          published_at: '2026-05-29T10:30:00Z',
          created_at: '2026-05-29T10:30:00Z',
          created_by_id: 99,
          created_by_name: 'admin',
          reused_from_id: null,
        }
        marketHistory = [
          currentMarketMessage,
          ...marketHistory.map((message) => ({ ...message, is_active: false })),
        ]
        return responseOf(currentMarketMessage)
      }
      if (path === '/api/admin-messages/market/current' && method === 'DELETE') {
        const cleared = currentMarketMessage ? { ...currentMarketMessage, is_active: false } : null
        currentMarketMessage = null
        marketHistory = marketHistory.map((message) => ({
          ...message,
          is_active: false,
        }))
        return responseOf(cleared)
      }
      if (path === '/api/admin-messages/broadcasts' && method === 'POST') {
        const payload = JSON.parse(String(init?.body || '{}'))
        const created = {
          id: 100,
          content: payload.content,
          target_groups: payload.target_groups,
          recipient_count: 6,
          delivered_user_ids: [10, 11, 12, 13, 14, 15],
          published_at: '2026-05-29T10:31:00Z',
          created_at: '2026-05-29T10:31:00Z',
          created_by_id: 99,
          created_by_name: 'admin',
        }
        broadcastHistory = [created, ...broadcastHistory]
        return responseOf(created)
      }
      return responseOf(null)
    })
  })

  it('renders split market and messenger lanes, reuses history, and publishes new management messages', async () => {
    const AdminMessagesView = (await import('./AdminMessagesView.vue')).default
    const wrapper = mount(AdminMessagesView)
    await flushPromises()

    expect(wrapper.find('[data-test="market-lane"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="broadcast-lane"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('پیام فعال بازار')
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

  it('clears the active market pin from the market lane', async () => {
    const AdminMessagesView = (await import('./AdminMessagesView.vue')).default
    const wrapper = mount(AdminMessagesView)
    await flushPromises()

    await wrapper.get('[data-test="clear-market-pin"]').trigger('click')
    await flushPromises()

    const clearCall = adminMessagesMocks.apiFetchMock.mock.calls.find(
      ([path, init]) => path === '/api/admin-messages/market/current' && init?.method === 'DELETE',
    )
    expect(clearCall).toBeTruthy()
    expect(wrapper.text()).toContain('پین فعال بازار برداشته شد')
    expect(wrapper.text()).toContain('در حال حاضر هیچ پیام پین‌شده‌ای برای بازار فعال نیست')
  })
})
