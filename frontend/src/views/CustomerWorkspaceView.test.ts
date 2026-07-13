import { mount } from '@vue/test-utils'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CustomerWorkspaceView from './CustomerWorkspaceView.vue'

const customerWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  fetchOwnerCustomerRelationsMock: vi.fn(),
  fetchOwnerCustomerSessionsMock: vi.fn(),
  fetchOwnerCustomerTradeStatsMock: vi.fn(),
  fetchOwnerCustomerTradesMock: vi.fn(),
  createOwnerCustomerRelationMock: vi.fn(),
  updateOwnerCustomerRelationMock: vi.fn(),
  deleteOwnerCustomerRelationMock: vi.fn(),
  terminateOwnerCustomerSessionMock: vi.fn(),
  routeState: {
    params: {} as Record<string, unknown>,
    query: {} as Record<string, unknown>,
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => customerWorkspaceMocks.routeState,
  useRouter: () => ({
    push: customerWorkspaceMocks.routerPushMock,
  }),
}))

vi.mock('../composables/useOwnerCustomers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../composables/useOwnerCustomers')>()
  return {
    ...actual,
    fetchOwnerCustomerRelations: customerWorkspaceMocks.fetchOwnerCustomerRelationsMock,
    fetchOwnerCustomerSessions: customerWorkspaceMocks.fetchOwnerCustomerSessionsMock,
    fetchOwnerCustomerTradeStats: customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock,
    fetchOwnerCustomerTrades: customerWorkspaceMocks.fetchOwnerCustomerTradesMock,
    createOwnerCustomerRelation: customerWorkspaceMocks.createOwnerCustomerRelationMock,
    updateOwnerCustomerRelation: customerWorkspaceMocks.updateOwnerCustomerRelationMock,
    deleteOwnerCustomerRelation: customerWorkspaceMocks.deleteOwnerCustomerRelationMock,
    terminateOwnerCustomerSession: customerWorkspaceMocks.terminateOwnerCustomerSessionMock,
  }
})

vi.mock('../components/OwnerCustomerManagerModal.vue', () => ({
  default: {
    name: 'OwnerCustomerManagerModal',
    props: ['presentation', 'initialRelationId', 'initialPanel'],
    emits: ['close', 'open-relation', 'back-to-list'],
    template: `
      <section class="customer-manager-stub">
        <span class="stub-presentation">{{ presentation }}</span>
        <span class="stub-relation">{{ initialRelationId }}</span>
        <span class="stub-panel">{{ initialPanel }}</span>
        <button class="stub-open-relation" @click="$emit('open-relation', 42)">open</button>
        <button class="stub-back-list" @click="$emit('back-to-list')">list</button>
        <button class="stub-close" @click="$emit('close')">close</button>
      </section>
    `,
  },
}))

describe('CustomerWorkspaceView.vue', () => {
  beforeEach(() => {
    customerWorkspaceMocks.routerPushMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerSessionsMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerTradesMock.mockReset()
    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockReset()
    customerWorkspaceMocks.updateOwnerCustomerRelationMock.mockReset()
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockReset()
    customerWorkspaceMocks.terminateOwnerCustomerSessionMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValue([
      {
        id: 11,
        owner_user_id: 1,
        customer_user_id: 22,
        customer_account_name: 'customer11',
        invitation_account_name: null,
        mobile_number: '09121111111',
        management_name: 'مشتری تست',
        customer_tier: 'tier2',
        commission_rate: 0.5,
        min_trade_quantity: null,
        max_trade_quantity: null,
        max_daily_trades: null,
        max_daily_commodity_volume: null,
        status: 'active',
        invitation_token: null,
        registration_link: null,
        expires_at: null,
        activated_at: '2026-01-02T10:00:00Z',
        deleted_at: null,
        created_at: '2026-01-01T10:00:00Z',
      },
      {
        id: 12,
        owner_user_id: 1,
        customer_user_id: null,
        customer_account_name: null,
        invitation_account_name: 'دعوت pending',
        mobile_number: '09122222222',
        management_name: 'دعوت مشتری',
        customer_tier: 'tier1',
        commission_rate: null,
        min_trade_quantity: null,
        max_trade_quantity: null,
        max_daily_trades: null,
        max_daily_commodity_volume: null,
        status: 'pending',
        invitation_token: 'token',
        registration_link: null,
        expires_at: null,
        activated_at: null,
        deleted_at: null,
        created_at: '2026-01-02T10:00:00Z',
      },
    ])
    customerWorkspaceMocks.fetchOwnerCustomerSessionsMock.mockResolvedValue([
      {
        id: 'session-1',
        device_name: 'Chrome',
        device_ip: null,
        platform: 'web',
        home_server: 'iran',
        is_primary: true,
        is_active: true,
        created_at: '2026-01-01T10:00:00Z',
        last_active_at: '2026-01-02T10:00:00Z',
      },
    ])
    customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock.mockResolvedValue({
      relation_id: 11,
      customer_user_id: 22,
      period_days: 7,
      from_date: '2026-01-01T00:00:00Z',
      to_date: '2026-01-08T00:00:00Z',
      trade_count: 3,
      total_quantity: 23,
      commission_profit_toman: 18_400_000,
      commodities: [{ commodity_id: 1, commodity_name: 'ربع', total_quantity: 23 }],
      profit_calculation_note: 'بر اساس نرخ تاریخی هر معامله',
    })
    customerWorkspaceMocks.fetchOwnerCustomerTradesMock.mockResolvedValue([
      {
        id: 1,
        trade_number: 1001,
        trade_type: 'خرید',
        settlement_type: 'tomorrow',
        commodity_name: 'ربع',
        quantity: 23,
        price: 50800,
        status: 'completed',
        counterparty_name: 'محسن',
        created_at: '2026-01-02T10:00:00Z',
      },
    ])
    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockResolvedValue({
      id: 15,
      owner_user_id: 1,
      customer_user_id: null,
      customer_account_name: null,
      invitation_account_name: 'customer_09123334444',
      mobile_number: '09123334444',
      management_name: 'مشتری جدید',
      customer_tier: 'tier2',
      commission_rate: 0.6,
      min_trade_quantity: null,
      max_trade_quantity: null,
      max_daily_trades: null,
      max_daily_commodity_volume: null,
      status: 'pending',
      invitation_token: 'new-token',
      registration_link: 'https://example.test/invite/new-token',
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    })
    customerWorkspaceMocks.updateOwnerCustomerRelationMock.mockImplementation(async (relationId: number, payload: Record<string, unknown>) => ({
      id: relationId,
      owner_user_id: 1,
      customer_user_id: 22,
      customer_account_name: 'customer11',
      invitation_account_name: null,
      mobile_number: '09121111111',
      management_name: 'مشتری تست',
      customer_tier: (payload.customer_tier as string | undefined) ?? 'tier2',
      commission_rate: payload.commission_rate == null ? null : Number(payload.commission_rate),
      min_trade_quantity: payload.min_trade_quantity == null ? null : Number(payload.min_trade_quantity),
      max_trade_quantity: payload.max_trade_quantity == null ? null : Number(payload.max_trade_quantity),
      max_daily_trades: payload.max_daily_trades == null ? null : Number(payload.max_daily_trades),
      max_daily_commodity_volume: payload.max_daily_commodity_volume == null ? null : Number(payload.max_daily_commodity_volume),
      status: 'active',
      invitation_token: '',
      registration_link: null,
      expires_at: null,
      activated_at: '2026-01-02T10:00:00Z',
      deleted_at: null,
      created_at: '2026-01-01T10:00:00Z',
    }))
    customerWorkspaceMocks.routeState.params = {}
    customerWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-native customer workspace without mounting the compatibility manager by default', async () => {
    const wrapper = mount(CustomerWorkspaceView)

    await flushPromises()

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.text()).toContain('مشتریان')
    expect(wrapper.text()).toContain('لیست مشتریان')
    expect(wrapper.text()).toContain('مشتری تست')
    expect(wrapper.find('.customer-manager-stub').exists()).toBe(false)
  })

  it('opens the route-native create dialog instead of the compatibility manager', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { section: 'stats', tab: 'limits' }

    const wrapper = mount(CustomerWorkspaceView, { attachTo: document.body })
    await flushPromises()
    await wrapper.get('.customer-workspace-create').trigger('click')

    expect(document.body.textContent).toContain('افزودن مشتری')
    expect(document.body.textContent).toContain('ثبت دعوت مشتری')
    expect(wrapper.find('.customer-manager-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('creates customer invitations and copies both pending registration surfaces', async () => {
    vi.useFakeTimers()
    const clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: clipboardWrite },
    })
    const created = {
      id: 15,
      owner_user_id: 1,
      customer_user_id: null,
      customer_account_name: null,
      invitation_account_name: 'customer_09123334444',
      mobile_number: '09123334444',
      management_name: 'مشتری جدید',
      customer_tier: 'tier1',
      commission_rate: null,
      min_trade_quantity: null,
      max_trade_quantity: null,
      max_daily_trades: null,
      max_daily_commodity_volume: null,
      status: 'pending',
      invitation_token: 'new-token',
      registration_link: 'https://example.test/invite/new-token',
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    }
    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockResolvedValueOnce({
      ...created,
      sms_status: 'disabled',
    })

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = wrapper.vm as any
    Object.assign(vm.customerState.createForm, {
      management_name: 'مشتری جدید',
      mobile_number: '09123334444',
      customer_tier: 'tier1',
      commission_rate: '0.50',
      min_trade_quantity: '',
      max_trade_quantity: '',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })

    await vm.createRelation()
    await flushPromises()
    expect(customerWorkspaceMocks.createOwnerCustomerRelationMock).toHaveBeenCalled()
    expect(wrapper.text()).toContain('پیامک دعوت ارسال نشد')

    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockResolvedValueOnce({
      ...created,
      id: 16,
      sms_status: null,
    })
    await vm.createRelation()
    await flushPromises()
    expect(wrapper.text()).toContain('دعوت مشتری با موفقیت ثبت شد.')

    const relation = {
      id: 12,
      bot_registration_link: 'https://t.me/bot?start=customer12',
      web_registration_link: 'https://example.test/invite/customer12',
    }
    await vm.copyRegistrationLink(relation, 'bot')
    await vm.copyRegistrationLink(relation, 'web')
    expect(clipboardWrite).toHaveBeenNthCalledWith(1, relation.bot_registration_link)
    expect(clipboardWrite).toHaveBeenNthCalledWith(2, relation.web_registration_link)
    await vi.advanceTimersByTimeAsync(1800)

    await vm.copyRegistrationLink({ id: 13 }, 'web')
    expect(clipboardWrite).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('uses both rendered copy controls for a dual-surface pending invitation', async () => {
    vi.useFakeTimers()
    const clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: clipboardWrite },
    })
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([{
      id: 20,
      owner_user_id: 1,
      customer_user_id: null,
      customer_account_name: null,
      invitation_account_name: 'dual-surface',
      mobile_number: '09120000020',
      management_name: 'دعوت دو مسیره',
      customer_tier: 'tier1',
      commission_rate: null,
      status: 'pending',
      invitation_token: 'dual-token',
      registration_link: null,
      bot_registration_link: 'https://t.me/bot?start=dual-token',
      web_registration_link: 'https://example.test/invite/dual-token',
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    }])

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const buttons = wrapper.findAll('.customer-pending-card .ui-button--secondary')
    expect(buttons).toHaveLength(2)
    await buttons[0]!.trigger('click')
    await flushPromises()
    expect(buttons[0]!.text()).toBe('کپی شد')
    await buttons[1]!.trigger('click')
    await flushPromises()
    expect(buttons[1]!.text()).toBe('کپی شد')
    expect(clipboardWrite).toHaveBeenNthCalledWith(1, 'https://t.me/bot?start=dual-token')
    expect(clipboardWrite).toHaveBeenNthCalledWith(2, 'https://example.test/invite/dual-token')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('routes relation selection, detail navigation, list back, and operations actions explicitly', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { section: 'stats', tab: 'limits' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    await wrapper.get('.workspace-relation-list .ui-list-item').trigger('click')
    await wrapper.get('.customer-selection-card .ui-button').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')
    await wrapper.get('.customer-workspace-action').trigger('click')

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-customers-detail',
      params: { relationId: '11' },
      query: { section: 'stats', tab: 'limits' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-customers-detail',
      params: { relationId: '11' },
      query: { section: 'stats', tab: 'limits' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(3, {
      name: 'operations-customers',
      query: { section: 'stats', tab: 'limits' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(4, {
      name: 'operations',
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledTimes(4)
  })

  it('returns to the operations index from the customer list route', async () => {
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations',
    })
  })

  it('loads route-native customer stats for the detail stats tab', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'stats' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock).toHaveBeenCalledWith(11, 7)
    expect(wrapper.text()).toContain('تعداد معاملات')
    expect(wrapper.text()).toContain('۱۸٫۴ میلیون تومان')
    expect(wrapper.text()).toContain('ربع')
  })

  it('shows settlement type in the route-native customer trade history', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'trades' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerTradesMock).toHaveBeenCalledWith(22, { limit: 20 })
    expect(wrapper.text()).toContain('محسن')
    expect(wrapper.text()).toContain('فردایی')
  })

  it('saves customer limits through the route-native detail form', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'limits' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    const maxDailyTradesInput = wrapper.find('input[placeholder="مثلاً ۴"]')
    await maxDailyTradesInput.setValue('7')
    await wrapper.get('.customer-edit-form-card .ui-button--primary').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.updateOwnerCustomerRelationMock).toHaveBeenCalledWith(11, {
      commission_rate: 0.5,
      max_daily_trades: 7,
    })
    expect(wrapper.text()).toContain('تغییرات ذخیره شد')
  })
})
