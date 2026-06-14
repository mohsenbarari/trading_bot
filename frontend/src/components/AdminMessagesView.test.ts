import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const adminMessagesMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
  scrollIntoViewMock: vi.fn(),
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

type MarketMessageFixture = {
  id: number
  content: string
  is_active: boolean
  notified_recipients_count: number
  published_at: string
  created_at: string
  created_by_id: number
  created_by_name: string
  reused_from_id: null
}

type BroadcastMessageFixture = {
  id: number
  content: string
  target_groups: string[]
  recipient_count: number
  published_at: string
  created_at: string
  created_by_id: number
  created_by_name: string
}

describe('AdminMessagesView.vue', () => {
  beforeEach(() => {
    adminMessagesMocks.scrollIntoViewMock.mockReset()
    HTMLElement.prototype.scrollIntoView = adminMessagesMocks.scrollIntoViewMock
    const currentPublishedAt = '2026-05-29T08:00:00Z'
    let currentMarketMessage: MarketMessageFixture | null = {
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
    let marketHistory: MarketMessageFixture[] = [
      { ...currentMarketMessage },
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
    let broadcastHistory: BroadcastMessageFixture[] = [
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

  it('starts with only the two management buttons and opens the market panel on click', async () => {
    const AdminMessagesView = (await import('./AdminMessagesView.vue')).default
    const wrapper = mount(AdminMessagesView)
    await flushPromises()

    expect(wrapper.find('[data-test="message-mode-market"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="message-mode-chat"]').exists()).toBe(true)
    expect(wrapper.get('[data-test="message-mode-market"]').attributes('role')).toBe('tab')
    expect(wrapper.get('[data-test="message-mode-market"]').attributes('tabindex')).toBe('0')
    expect(wrapper.get('[data-test="message-mode-chat"]').attributes('tabindex')).toBe('-1')
    expect(wrapper.text()).toContain('ارسال پیام در بازار')
    expect(wrapper.find('[data-test="market-panel"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="broadcast-panel"]').exists()).toBe(false)

    await wrapper.get('[data-test="message-mode-market"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('پیام فعال بازار')
    expect(wrapper.get('[data-test="market-panel"]').attributes('role')).toBe('tabpanel')
    expect(wrapper.get('[data-test="market-panel"]').attributes('aria-labelledby')).toBe('admin-message-tab-market')
    expect(wrapper.text()).toContain('مشاهده همه پیام')
    expect(wrapper.find('[data-test="market-history-list"]').exists()).toBe(false)
    expect(wrapper.find('.history-header--market .history-badge').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('به‌صورت پیش‌فرض بسته است تا تمرکز روی پیام فعال و composer بماند.')
    expect(wrapper.text()).not.toContain('اگر از تاریخچه روی قلم بزنی، صفحه روی همین کادر می‌آید و متن برای ویرایش اینجا قرار می‌گیرد.')
    expect(wrapper.text()).not.toContain('کاراکتر')

    await wrapper.get('[data-test="market-history-help"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="market-history-help-note"]').text()).toContain('متن همان پیام به کادر پایین منتقل می‌شود')

    await wrapper.get('[data-test="market-composer-help"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="market-composer-help-note"]').text()).toContain('فقط یک پیام می‌تواند هم‌زمان در بازار پین باشد')
    expect(wrapper.get('[data-test="market-composer-help-note"]').text()).not.toContain('اگر روی آیکن مداد')

    await wrapper.get('[data-test="market-history-toggle"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="market-history-list"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('پیام قبلی بازار')

    await wrapper.get('[data-test="market-history-edit-11"]').trigger('click')
    await flushPromises()

    const marketTextarea = wrapper.get('[data-test="market-composer-input"]')
    expect((marketTextarea.element as HTMLTextAreaElement).value).toBe('پیام قبلی بازار')
    expect(adminMessagesMocks.scrollIntoViewMock).toHaveBeenCalled()
  })

  it('supports keyboard navigation between management message tabs', async () => {
    const AdminMessagesView = (await import('./AdminMessagesView.vue')).default
    const wrapper = mount(AdminMessagesView)
    await flushPromises()

    const marketTab = wrapper.get('[data-test="message-mode-market"]')
    const chatTab = wrapper.get('[data-test="message-mode-chat"]')

    await marketTab.trigger('keydown', { key: 'ArrowLeft' })
    await flushPromises()

    expect(chatTab.attributes('aria-selected')).toBe('true')
    expect(chatTab.attributes('tabindex')).toBe('0')
    expect(wrapper.get('[data-test="broadcast-panel"]').attributes('role')).toBe('tabpanel')
    expect(wrapper.get('[data-test="broadcast-panel"]').attributes('aria-labelledby')).toBe('admin-message-tab-chat')

    await chatTab.trigger('keydown', { key: 'Home' })
    await flushPromises()

    expect(marketTab.attributes('aria-selected')).toBe('true')
    expect(marketTab.attributes('tabindex')).toBe('0')
    expect(wrapper.find('[data-test="market-panel"]').exists()).toBe(true)
  })

  it('publishes market and chat management messages through their own tabs', async () => {
    const AdminMessagesView = (await import('./AdminMessagesView.vue')).default
    const wrapper = mount(AdminMessagesView)
    await flushPromises()

    await wrapper.get('[data-test="message-mode-market"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-test="market-history-toggle"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="market-history-edit-11"]').trigger('click')
    await flushPromises()

    await wrapper.findAll('.primary-action')[0]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('پیام بازار برای ۵ نفر اعلان شد')

    await wrapper.get('[data-test="message-mode-chat"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('پیام قبلی همگانی')

    const reuseButtons = wrapper.findAll('button').filter((button) => button.text().includes('استفاده مجدد'))
    await reuseButtons[0]!.trigger('click')
    await flushPromises()

    const textareas = wrapper.findAll('textarea')
    expect((textareas[0]!.element as HTMLTextAreaElement).value).toBe('پیام قبلی همگانی')

    await wrapper.findAll('.primary-action')[0]!.trigger('click')
    await flushPromises()

    const marketCall = adminMessagesMocks.apiFetchMock.mock.calls.find(([path]) => path === '/api/admin-messages/market')
    const broadcastCall = adminMessagesMocks.apiFetchMock.mock.calls.find(([path]) => path === '/api/admin-messages/broadcasts')
    expect(JSON.parse(String(marketCall?.[1]?.body))).toEqual({ content: 'پیام قبلی بازار' })
    expect(JSON.parse(String(broadcastCall?.[1]?.body))).toEqual({
      content: 'پیام قبلی همگانی',
      target_groups: ['users', 'customers'],
    })
    expect(wrapper.text()).toContain('پیام برای ۶ نفر ارسال شد')
  })

  it('clears the active market pin from the market lane', async () => {
    const AdminMessagesView = (await import('./AdminMessagesView.vue')).default
    const wrapper = mount(AdminMessagesView)
    await flushPromises()

    await wrapper.get('[data-test="message-mode-market"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-test="clear-market-pin"]').trigger('click')
    await flushPromises()

    const clearCall = adminMessagesMocks.apiFetchMock.mock.calls.find(
      ([path, init]) => path === '/api/admin-messages/market/current' && init?.method === 'DELETE',
    )
    expect(clearCall).toBeTruthy()
    expect(wrapper.text()).toContain('پین فعال بازار برداشته شد')
    expect(wrapper.text()).not.toContain('در حال حاضر هیچ پیام پین‌شده‌ای برای بازار فعال نیست')

    await wrapper.get('[data-test="market-empty-help"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-test="market-empty-help-note"]').text()).toContain('در حال حاضر هیچ پیام پین‌شده‌ای برای بازار فعال نیست')
  })
})
