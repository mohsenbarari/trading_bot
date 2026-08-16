import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardDailySections from './DashboardDailySections.vue'

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  routerPush: vi.fn(),
}))

vi.mock('../../utils/auth', () => ({
  apiFetch: mocks.apiFetch,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}))

function responseOf(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

function tradePage(items: unknown[], nextCursor: string | null = null) {
  return {
    items,
    next_cursor: nextCursor,
    has_more: Boolean(nextCursor),
    page_size: 100,
  }
}

function user(overrides: Record<string, unknown> = {}) {
  return {
    id: 7,
    role: 'owner',
    full_name: 'مالک پروژه',
    account_name: 'owner7',
    account_status: 'active',
    is_accountant: false,
    is_customer: false,
    ...overrides,
  }
}

function trade(overrides: Record<string, unknown> = {}) {
  return {
    id: 81,
    trade_number: 123,
    offer_id: 44,
    trade_type: 'sell',
    settlement_type: 'tomorrow',
    commodity_id: 3,
    commodity_name: 'سکه امامی',
    quantity: 2,
    price: 50_000_000,
    status: 'completed',
    offer_user_id: 7,
    offer_user_name: 'مالک پروژه',
    responder_user_id: 9,
    responder_user_name: 'همکار بازار',
    counterparty_name: 'همکار بازار',
    customer_context_visible: true,
    customer_context_management_name: 'مشتری ویژه',
    customer_context_tier: 'tier1',
    trade_path_summary: 'مسیر مستقیم وب',
    offer_notes: 'تحویل در دفتر',
    created_at: '۱۴۰۵/۰۲/۲۴ ۱۰:۱۵',
    ...overrides,
  }
}

function requestedUrls() {
  return mocks.apiFetch.mock.calls.map(([url]) => String(url))
}

describe('DashboardDailySections.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-14T09:00:00Z'))
    mocks.apiFetch.mockReset()
    mocks.routerPush.mockReset()
  })

  it('loads only the effective user trades for Iran today and shows their complete user-facing facts', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(
        responseOf(tradePage([
          trade(),
          trade({ id: 82, trade_number: 124, offer_user_id: 30, responder_user_id: 31 }),
          { id: 'malformed' },
        ], 'signed cursor')),
      )
      .mockResolvedValueOnce(
        responseOf(tradePage([
          trade({ id: 83, trade_number: 125, offer_id: 45 }),
        ])),
      )

    const wrapper = mount(DashboardDailySections, { props: { user: user() } })
    await flushPromises()

    expect(requestedUrls()).toEqual([
      '/api/trades/my/page?from_date=2026-05-14&to_date=2026-05-14&limit=100',
      '/api/trades/my/page?from_date=2026-05-14&to_date=2026-05-14&limit=100&cursor=signed+cursor',
    ])
    expect(wrapper.findAll('.dashboard-trade-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('معاملهٔ ۱۲۳')
    expect(wrapper.text()).toContain('سکه امامی')
    expect(wrapper.text()).toContain('فردایی')
    expect(wrapper.text()).toContain('۵۰٬۰۰۰٬۰۰۰ تومان')
    expect(wrapper.text()).toContain('۱۰۰٬۰۰۰٬۰۰۰ تومان')
    expect(wrapper.text()).toContain('همکار بازار')
    expect(wrapper.text()).toContain('شماره آفر')
    expect(wrapper.text()).toContain('مشتری ویژه')
    expect(wrapper.text()).toContain('مسیر مستقیم وب')
    expect(wrapper.text()).toContain('تحویل در دفتر')
    expect(wrapper.text()).toContain('معاملهٔ ۱۲۵')
    expect(wrapper.text()).not.toContain('۱۲۴')
  })

  it('keeps coworkers and commodities collapsed, then lazily loads and navigates without exposing phone data', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(responseOf(tradePage([])))
      .mockResolvedValueOnce(
        responseOf([
          { id: 7, account_name: 'owner7', mobile_number: '09120000000' },
          { id: 9, account_name: 'coworker9', mobile_number: '09121112222' },
        ]),
      )
      .mockResolvedValueOnce(
        responseOf([
          {
            id: 3,
            name: 'سکه امامی',
            aliases: [
              { id: 1, alias: 'امامی', commodity_id: 3 },
              { id: 2, alias: 'طرح جدید', commodity_id: 3 },
            ],
          },
        ]),
      )

    const wrapper = mount(DashboardDailySections, { props: { user: user() } })
    await flushPromises()

    expect(wrapper.get('.dashboard-coworkers .ui-disclosure__toggle').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('.dashboard-commodities .ui-disclosure__toggle').attributes('aria-expanded')).toBe('false')
    expect(requestedUrls()).toHaveLength(1)

    await wrapper.get('.dashboard-coworkers .ui-disclosure__toggle').trigger('click')
    await flushPromises()
    expect(requestedUrls()[1]).toBe('/api/users-public/7/project-users?limit=25&offset=0')
    expect(wrapper.text()).toContain('coworker9')
    expect(wrapper.text()).not.toContain('09121112222')

    await wrapper.get('.dashboard-coworker-list .ui-list-item').trigger('click')
    expect(mocks.routerPush).toHaveBeenCalledWith({
      name: 'public-profile',
      params: { id: 9 },
      query: { account_name: 'coworker9' },
    })

    await wrapper.get('.dashboard-commodities .ui-disclosure__toggle').trigger('click')
    await flushPromises()
    expect(requestedUrls()[2]).toBe('/api/commodities/')
    expect(wrapper.text()).toContain('سکه امامی')
    expect(wrapper.text()).toContain('امامی')
    expect(wrapper.text()).toContain('طرح جدید')
  })

  it('shows the coworkers disclosure without calling its forbidden endpoint for customer accounts', async () => {
    mocks.apiFetch.mockResolvedValueOnce(responseOf(tradePage([])))
    const wrapper = mount(DashboardDailySections, {
      props: { user: user({ is_customer: true, customer_tier: 'tier1' }) },
    })
    await flushPromises()

    await wrapper.get('.dashboard-coworkers .ui-disclosure__toggle').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('مطابق حریم خصوصی محدود است')
    expect(requestedUrls().some((url) => url.includes('project-users'))).toBe(false)
  })

  it('drops an older in-flight trade response when the effective identity changes', async () => {
    let resolveFirst!: (value: ReturnType<typeof responseOf>) => void
    let resolveSecond!: (value: ReturnType<typeof responseOf>) => void
    mocks.apiFetch
      .mockImplementationOnce(
        () => new Promise((resolve) => { resolveFirst = resolve }),
      )
      .mockImplementationOnce(
        () => new Promise((resolve) => { resolveSecond = resolve }),
      )

    const wrapper = mount(DashboardDailySections, { props: { user: user() } })
    await flushPromises()
    await wrapper.setProps({
      user: user({ id: 20, full_name: 'مالک دوم', account_name: 'owner20' }),
    })
    await flushPromises()

    resolveSecond(responseOf(tradePage([trade({ id: 90, trade_number: 900, offer_user_id: 20 })])))
    await flushPromises()
    resolveFirst(responseOf(tradePage([trade({ id: 81, trade_number: 123, offer_user_id: 7 })])))
    await flushPromises()

    expect(wrapper.text()).toContain('معاملهٔ ۹۰۰')
    expect(wrapper.text()).not.toContain('معاملهٔ ۱۲۳')
  })
})
