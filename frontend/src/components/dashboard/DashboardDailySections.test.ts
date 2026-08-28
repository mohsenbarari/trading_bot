import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardDailySections from './DashboardDailySections.vue'

const componentSource = readFileSync(
  resolve(process.cwd(), 'src/components/dashboard/DashboardDailySections.vue'),
  'utf8',
)

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  routerPush: vi.fn(),
  wsHandlers: new Map<string, Array<(payload?: unknown) => void>>(),
  wsOn: vi.fn((event: string, callback: (payload?: unknown) => void) => {
    const handlers = mocks.wsHandlers.get(event) ?? []
    handlers.push(callback)
    mocks.wsHandlers.set(event, handlers)
  }),
  wsOff: vi.fn(),
}))

vi.mock('../../utils/auth', () => ({
  apiFetch: mocks.apiFetch,
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.routerPush }),
}))

vi.mock('../../composables/useWebSocket', () => ({
  useWebSocket: () => ({ on: mocks.wsOn, off: mocks.wsOff }),
}))

function emitRealtime(event: string, payload?: unknown) {
  for (const handler of mocks.wsHandlers.get(event) ?? []) handler(payload)
}

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
    trade_number: 123456,
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
    created_at: '1405/02/24 10:15',
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
    mocks.wsHandlers.clear()
    mocks.wsOn.mockClear()
    mocks.wsOff.mockClear()
  })

  it('renders coworkers and commodities as inset groups without outer card chrome', () => {
    expect(componentSource).toMatch(
      /\.dashboard-coworkers,\s*\.dashboard-commodities\s*\{[\s\S]*?border:\s*0;/,
    )
    expect(componentSource).toMatch(
      /\.dashboard-coworkers,\s*\.dashboard-commodities\s*\{[\s\S]*?border-radius:\s*var\(--ds-inset-group-radius/,
    )
  })

  it('keeps one semantic row per trade inside a keyboard-focusable horizontal scroller', () => {
    expect(componentSource.match(/<tr v-for="trade in trades"/g)).toHaveLength(1)
    expect(componentSource).toMatch(
      /\.dashboard-trades__scroller\s*\{[^}]*overflow-x:\s*auto;/s,
    )
    expect(componentSource).toMatch(
      /\.dashboard-trades__table th,[\s\S]*?white-space:\s*nowrap;/,
    )
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
    const tradeRows = wrapper.findAll('.dashboard-trades__table tbody tr')
    expect(tradeRows).toHaveLength(2)
    expect(tradeRows[0]!.findAll('th, td')).toHaveLength(11)
    expect(wrapper.findAll('.dashboard-trades__table thead th')).toHaveLength(11)
    expect(wrapper.get('.dashboard-trades__scroller').attributes()).toMatchObject({
      role: 'region',
      tabindex: '0',
    })
    expect(wrapper.get('.dashboard-trades__scroller').attributes('aria-label')).toBe('۲ معامله')
    expect(wrapper.text()).not.toContain('جدول را به چپ یا راست بکشید')
    expect(wrapper.text()).not.toContain('با پیمایش افقی')
    expect(wrapper.text()).toContain('شماره معامله')
    expect(wrapper.text()).toContain('۱۲۳۴۵۶')
    expect(wrapper.text()).not.toContain('۱۲۳٬۴۵۶')
    expect(wrapper.text()).toContain('سکه امامی')
    expect(wrapper.text()).toContain('فردایی')
    expect(wrapper.text()).toContain('۵۰٬۰۰۰٬۰۰۰ تومان')
    expect(wrapper.text()).toContain('۱۰۰٬۰۰۰٬۰۰۰ تومان')
    expect(wrapper.text()).toContain('همکار بازار')
    expect(wrapper.text()).not.toContain('شماره آفر')
    expect(wrapper.text()).not.toContain('مسیر معامله')
    expect(wrapper.text()).not.toContain('یادداشت آفر')
    expect(wrapper.text()).toContain('توضیحات')
    expect(wrapper.text()).toContain('تحویل در دفتر')
    expect(wrapper.text()).toContain('1405/02/24 10:15')
    expect(wrapper.text()).not.toContain('۷۸۴')
    expect(wrapper.text()).toContain('۱۲۵')
    expect(wrapper.text()).not.toContain('۱۲۴')
  })

  it('shows an authorized completed trade immediately and reconciles today history without a page refresh', async () => {
    const realtimeTrade = trade({ id: 91, trade_number: 777, offer_notes: null })
    mocks.apiFetch
      .mockResolvedValueOnce(responseOf(tradePage([])))
      .mockResolvedValueOnce(
        responseOf(tradePage([trade({ id: 91, trade_number: 777, offer_notes: 'توضیح نهایی' })])),
      )

    const wrapper = mount(DashboardDailySections, { props: { user: user() } })
    await flushPromises()
    expect(wrapper.text()).toContain('امروز معامله‌ای ثبت نشده است')

    emitRealtime('trade:created', realtimeTrade)
    await flushPromises()
    expect(wrapper.get('.dashboard-trades__table tbody th').text()).toBe('۷۷۷')

    await vi.advanceTimersByTimeAsync(80)
    await flushPromises()
    expect(requestedUrls()).toHaveLength(2)
    expect(wrapper.text()).toContain('توضیح نهایی')

    wrapper.unmount()
    expect(mocks.wsOff).toHaveBeenCalledWith('trade:created', expect.any(Function))
  })

  it('refreshes today history from the receipt-backed trade notification path', async () => {
    mocks.apiFetch
      .mockResolvedValueOnce(responseOf(tradePage([])))
      .mockResolvedValueOnce(responseOf(tradePage([trade({ id: 92, trade_number: 778 })])))

    const wrapper = mount(DashboardDailySections, { props: { user: user() } })
    await flushPromises()

    emitRealtime('message', { id: 501, category: 'trade', trade_number: 778 })
    await vi.advanceTimersByTimeAsync(80)
    await flushPromises()

    expect(requestedUrls()).toHaveLength(2)
    expect(wrapper.get('.dashboard-trades__table tbody th').text()).toBe('۷۷۸')

    wrapper.unmount()
    expect(mocks.wsOff).toHaveBeenCalledWith('message', expect.any(Function))
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

    expect(wrapper.get('.dashboard-trades__table tbody th').text()).toBe('۹۰۰')
    expect(wrapper.text()).not.toContain('۱۲۳')
  })
})
