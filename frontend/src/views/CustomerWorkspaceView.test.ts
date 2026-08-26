import { readFileSync } from 'node:fs'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount as mountComponent } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  CustomerRelation,
  CustomerSessionSummary,
  CustomerTradeStats,
  CustomerTradeSummary,
  useOwnerCustomers,
} from '../composables/useOwnerCustomers'
import CustomerWorkspaceView from './CustomerWorkspaceView.vue'

const customerWorkspaceCss = readFileSync('src/styles/design-system-v2.stage5-customer.css', 'utf8')

enableAutoUnmount(afterEach)

function mount(
  component: typeof CustomerWorkspaceView,
  options: Parameters<typeof mountComponent>[1] = {},
) {
  return mountComponent(component, { attachTo: document.body, ...options })
}

function bodyDialog(selector: string) {
  const element = document.body.querySelector<HTMLElement>(selector)
  if (!element) throw new Error(`Expected ${selector} to be mounted in document.body.`)
  return new DOMWrapper(element)
}

function accountDeletionDialog() {
  return bodyDialog('.ui-v2-workspace-account-deletion-dialog')
}

function confirmDialog() {
  return bodyDialog('.ui-confirm-dialog')
}

function hasBodyDialog(selector: string) {
  return document.body.querySelector(selector) !== null
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function makeCustomerRelation(overrides: Partial<CustomerRelation> = {}): CustomerRelation {
  return {
    id: 13,
    owner_user_id: 1,
    customer_user_id: 33,
    customer_account_name: 'customer13',
    invitation_account_name: null,
    mobile_number: '09123333333',
    management_name: 'مشتری دوم',
    customer_tier: 'tier1',
    commission_rate: null,
    min_trade_quantity: null,
    max_trade_quantity: null,
    max_daily_trades: 2,
    max_daily_commodity_volume: null,
    status: 'active',
    registration_link: null,
    expires_at: null,
    activated_at: '2026-01-04T10:00:00Z',
    deleted_at: null,
    created_at: '2026-01-03T10:00:00Z',
    ...overrides,
  }
}

function makeTrade(id: number, counterpartyName: string): CustomerTradeSummary {
  return {
    id,
    trade_number: 1000 + id,
    trade_type: 'خرید',
    settlement_type: 'cash',
    commodity_name: 'تمام',
    quantity: 1,
    price: 100,
    status: 'completed',
    counterparty_name: counterpartyName,
    created_at: '2026-01-05T10:00:00Z',
  }
}

function makeStats(relationId: number, periodDays: number, tradeCount: number): CustomerTradeStats {
  return {
    relation_id: relationId,
    customer_user_id: relationId === 11 ? 22 : 33,
    period_days: periodDays,
    from_date: '2026-01-01T00:00:00Z',
    to_date: '2026-01-08T00:00:00Z',
    trade_count: tradeCount,
    total_quantity: tradeCount,
    commission_profit_toman: 0,
    commodities: [],
    profit_calculation_note: 'آزمون',
  }
}

type CustomerWorkspaceTestVm = {
  customerState: ReturnType<typeof useOwnerCustomers>
  activeRelation: CustomerRelation | null
  availableDetailTabOptions: Array<{ key: string; label: string }>
  createError: string
  createNotice: string
  detailSessions: CustomerSessionSummary[]
  detailSessionsError: string
  detailSessionsLoading: boolean
  detailStats: CustomerTradeStats | null
  detailStatsLoading: boolean
  detailTrades: CustomerTradeSummary[]
  detailTradesLoading: boolean
  isCreatePanelOpen: boolean
  isCreateSubmitting: boolean
  isLimitsReviewOpen: boolean
  limitsNotice: string
  listActionNotice: string
  relationFilter: string
  savedListScroll: number
  sessionNotice: string
  closeCreatePanel: () => void
  confirmDetailLimits: () => Promise<void>
  copyRegistrationLink: (
    relation: Pick<CustomerRelation, 'id'> & Partial<CustomerRelation>,
    surface?: 'bot' | 'web',
  ) => Promise<void>
  createRelation: () => Promise<void>
  handleBack: () => void
  handleConfirmAction: () => Promise<void>
  loadRelations: (force?: boolean) => Promise<void>
  openAccountDeletionDialog: (relation: CustomerRelation | null) => void
  openConfirmDialog: (
    kind: 'terminate-session' | 'cancel-invitation' | 'close-relation',
    relation: CustomerRelation | null,
    session?: CustomerSessionSummary | null,
  ) => void
  openCreatePanel: () => void
  saveDetailLimits: () => Promise<void>
  setStatsPeriod: (days: number) => void
}

function getCustomerWorkspaceVm(wrapper: { vm: unknown }): CustomerWorkspaceTestVm {
  return wrapper.vm as unknown as CustomerWorkspaceTestVm
}

const customerWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routerReplaceMock: vi.fn(),
  fetchOwnerCustomerRelationMock: vi.fn(),
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

vi.mock('vue-router', async () => {
  const { reactive } = await vi.importActual<typeof import('vue')>('vue')
  customerWorkspaceMocks.routeState = reactive(customerWorkspaceMocks.routeState)
  return {
    useRoute: () => customerWorkspaceMocks.routeState,
    useRouter: () => ({
      push: customerWorkspaceMocks.routerPushMock,
      replace: customerWorkspaceMocks.routerReplaceMock,
    }),
  }
})

vi.mock('../composables/useOwnerCustomers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../composables/useOwnerCustomers')>()
  return {
    ...actual,
    fetchOwnerCustomerRelation: customerWorkspaceMocks.fetchOwnerCustomerRelationMock,
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

describe('CustomerWorkspaceView.vue', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    document.documentElement.scrollTop = 0
    document.body.scrollTop = 0
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
    customerWorkspaceMocks.routerPushMock.mockReset()
    customerWorkspaceMocks.routerReplaceMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerRelationMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerSessionsMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerTradesMock.mockReset()
    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockReset()
    customerWorkspaceMocks.updateOwnerCustomerRelationMock.mockReset()
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockReset()
    customerWorkspaceMocks.terminateOwnerCustomerSessionMock.mockReset()
    customerWorkspaceMocks.fetchOwnerCustomerRelationMock.mockRejectedValue(
      Object.assign(new Error('رابطه مشتری پیدا نشد.'), { status: 404 }),
    )
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
        registration_link: null,
        web_short_link: 'https://example.test/i/CUST0012',
        expires_at: '2026-08-22T18:30:00Z',
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
      web_short_link: 'https://example.test/i/CUST0015',
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    })
    customerWorkspaceMocks.updateOwnerCustomerRelationMock.mockImplementation(
      async (relationId: number, payload: Record<string, unknown>) => ({
        id: relationId,
        owner_user_id: 1,
        customer_user_id: 22,
        customer_account_name: 'customer11',
        invitation_account_name: null,
        mobile_number: '09121111111',
        management_name: 'مشتری تست',
        customer_tier: (payload.customer_tier as string | undefined) ?? 'tier2',
        commission_rate: payload.commission_rate == null ? null : Number(payload.commission_rate),
        min_trade_quantity:
          payload.min_trade_quantity == null ? null : Number(payload.min_trade_quantity),
        max_trade_quantity:
          payload.max_trade_quantity == null ? null : Number(payload.max_trade_quantity),
        max_daily_trades:
          payload.max_daily_trades == null ? null : Number(payload.max_daily_trades),
        max_daily_commodity_volume:
          payload.max_daily_commodity_volume == null
            ? null
            : Number(payload.max_daily_commodity_volume),
        status: 'active',
        registration_link: null,
        expires_at: null,
        activated_at: '2026-01-02T10:00:00Z',
        deleted_at: null,
        created_at: '2026-01-01T10:00:00Z',
      }),
    )
    customerWorkspaceMocks.routeState.params = {}
    customerWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-native customer workspace without mounting the compatibility manager by default', async () => {
    const wrapper = mount(CustomerWorkspaceView)

    await flushPromises()

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.get('.ui-v2-workspace-adapter').attributes('data-ui-system')).toBe('v2')
    expect(wrapper.get('.ui-v2-workspace-customer-root').attributes('data-ui-system')).toBe('v2')
    expect(wrapper.find('.ui-v2-workspace-customer-layout').exists()).toBe(true)
    expect(wrapper.findAll('.ui-v2-workspace-section-adapter')).toHaveLength(1)
    expect(wrapper.text()).toContain('مشتریان')
    expect(wrapper.text()).toContain('لیست مشتریان')
    expect(wrapper.text()).toContain('مشتری تست')
    expect(wrapper.text()).toContain('۱ دعوت در انتظار اقدام')
    expect(wrapper.text()).toContain('مهلت ثبت‌نام:')
    expect(wrapper.text()).not.toContain('۲ رابطه')
    expect(wrapper.text()).not.toContain('۱ فعال')
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
    const dialog = document.querySelector('.ui-responsive-dialog')
    expect(dialog).not.toBeNull()
    expect(
      dialog?.closest('[data-ui-system="v2"]')?.classList.contains('ui-v2-workspace-customer-root'),
    ).toBe(true)
    expect(document.querySelectorAll('#customer-workspace-overlay-host')).toHaveLength(1)
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
      web_short_link: 'https://example.test/i/CUST0015',
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
    const vm = getCustomerWorkspaceVm(wrapper)
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
    Object.assign(vm.customerState.createForm, {
      management_name: 'مشتری جدید',
      mobile_number: '09123334444',
      customer_tier: 'tier1',
      commission_rate: '0.50',
    })
    await vm.createRelation()
    await flushPromises()
    expect(wrapper.text()).toContain('دعوت مشتری با موفقیت ثبت شد.')

    const relation = {
      id: 12,
      bot_registration_link: 'https://t.me/bot?start=customer12',
      web_short_link: 'https://example.test/i/CUST0012',
    }
    await vm.copyRegistrationLink(relation, 'bot')
    await vm.copyRegistrationLink(relation, 'web')
    expect(clipboardWrite).toHaveBeenNthCalledWith(1, relation.bot_registration_link)
    expect(clipboardWrite).toHaveBeenNthCalledWith(2, relation.web_short_link)
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
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([
      {
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
        registration_link: null,
        bot_registration_link: 'https://t.me/bot?start=dual-token',
        web_short_link: 'https://example.test/i/CUST0020',
        expires_at: null,
        activated_at: null,
        deleted_at: null,
        created_at: '2026-01-03T10:00:00Z',
      },
    ])

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const more = wrapper.get('.customer-pending-card [aria-label="اقدام‌های دعوت"]')
    await more.trigger('click')
    const overflowItems = wrapper.findAll('.customer-pending-card .ui-action-overflow__item')
    expect(overflowItems).toHaveLength(3)
    await overflowItems[0]!.trigger('click')
    await flushPromises()
    await more.trigger('click')
    const overflowAfterFirst = wrapper.findAll('.customer-pending-card .ui-action-overflow__item')
    expect(overflowAfterFirst[0]!.text()).toBe('کپی شد')
    await overflowAfterFirst[1]!.trigger('click')
    await flushPromises()
    expect(clipboardWrite).toHaveBeenNthCalledWith(1, 'https://t.me/bot?start=dual-token')
    expect(clipboardWrite).toHaveBeenNthCalledWith(2, 'https://example.test/i/CUST0020')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('keeps an in-flight create non-dismissible and reconciles its captured draft without erasing newer input', async () => {
    const pendingCreate = deferred<Record<string, unknown>>()
    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockReturnValueOnce(
      pendingCreate.promise,
    )
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await wrapper.get('.customer-workspace-create').trigger('click')
    const vm = getCustomerWorkspaceVm(wrapper)
    Object.assign(vm.customerState.createForm, {
      management_name: 'پیش‌نویس ارسال‌شده',
      mobile_number: '09123334444',
      customer_tier: 'tier1',
      commission_rate: '0.50',
      min_trade_quantity: '',
      max_trade_quantity: '',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })

    const createRequest = vm.createRelation()
    await flushPromises()
    await vm.createRelation()

    expect(customerWorkspaceMocks.createOwnerCustomerRelationMock).toHaveBeenCalledTimes(1)
    expect(customerWorkspaceMocks.createOwnerCustomerRelationMock).toHaveBeenCalledWith(
      expect.objectContaining({
        account_name: 'customer_09123334444',
        management_name: 'پیش‌نویس ارسال‌شده',
        mobile_number: '09123334444',
      }),
      { signal: expect.anything() },
    )
    expect(
      document.querySelector('.ui-v2-workspace-customer-create-fieldset')?.hasAttribute('disabled'),
    ).toBe(true)
    expect(document.querySelector('.ui-responsive-dialog__header .ui-button')).toBeNull()
    const createActionButtons = Array.from(
      document.querySelectorAll<HTMLButtonElement>(
        '.ui-v2-workspace-customer-create-actions .ui-button',
      ),
    )
    expect(createActionButtons).toHaveLength(2)
    expect(createActionButtons.every((button) => button.disabled)).toBe(true)
    expect(
      createActionButtons[0]?.closest('.ui-v2-workspace-overlay-body'),
    ).not.toBeNull()
    expect(document.querySelector('.ui-responsive-dialog__actions')).toBeNull()

    vm.closeCreatePanel()
    vm.handleBack()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    document.querySelector<HTMLElement>('.ui-responsive-dialog-backdrop')?.click()
    await flushPromises()
    expect(vm.isCreatePanelOpen).toBe(true)
    expect(customerWorkspaceMocks.routerPushMock).not.toHaveBeenCalled()

    vm.customerState.createForm.management_name = 'پیش‌نویس تازه'
    pendingCreate.resolve(
      makeCustomerRelation({
        id: 21,
        customer_user_id: null,
        customer_account_name: null,
        invitation_account_name: 'customer_09123334444',
        mobile_number: '09123334444',
        management_name: 'پیش‌نویس ارسال‌شده',
        status: 'pending',
        web_short_link: 'https://example.test/i/CUST0021',
        activated_at: null,
      }),
    )
    await createRequest
    await flushPromises()

    expect(vm.isCreateSubmitting).toBe(false)
    expect(vm.isCreatePanelOpen).toBe(true)
    expect(vm.customerState.createForm.management_name).toBe('پیش‌نویس تازه')
    expect(
      vm.customerState.relations.value.some((relation: { id: number }) => relation.id === 21),
    ).toBe(true)
    expect(wrapper.get('.ui-v2-workspace-customer-global-notice').text()).toContain(
      'دعوت مشتری با موفقیت ثبت شد.',
    )
    expect(
      document.querySelector('.ui-v2-workspace-customer-create-fieldset')?.hasAttribute('disabled'),
    ).toBe(false)

    vm.closeCreatePanel()
    await flushPromises()
    expect(vm.isCreatePanelOpen).toBe(false)
  })

  it('rejects an invalid create receipt while preserving the submitted draft and open recovery surface', async () => {
    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockResolvedValueOnce(
      makeCustomerRelation({
        id: 22,
        mobile_number: '09123334444',
        management_name: 'پاسخ نامعتبر',
      }),
    )
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    vm.openCreatePanel()
    Object.assign(vm.customerState.createForm, {
      management_name: 'دعوت باقی‌مانده',
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

    expect(vm.createError).toBe('پاسخ ایجاد مشتری معتبر نبود.')
    expect(vm.createNotice).toBe('')
    expect(vm.isCreatePanelOpen).toBe(true)
    expect(vm.customerState.createForm.management_name).toBe('دعوت باقی‌مانده')
    expect(
      vm.customerState.relations.value.some((relation: { id: number }) => relation.id === 22),
    ).toBe(false)
    expect(document.body.textContent).toContain('پاسخ ایجاد مشتری معتبر نبود.')
    expect(wrapper.find('.ui-v2-workspace-customer-global-notice').exists()).toBe(false)
  })

  it('carries only canonical list context into detail and back navigation', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = {
      q: 'مشتری',
      filter: 'active',
      scroll: '140',
      section: 'stats',
      tab: 'limits',
    }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    await wrapper.get('.workspace-relation-list .ui-list-item').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-customers-detail',
      params: { relationId: '11' },
      query: { q: 'مشتری', filter: 'active', scroll: '140' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-customers',
      query: { q: 'مشتری', filter: 'active', scroll: '140' },
    })
    expect(wrapper.find('.customer-workspace-action').exists()).toBe(false)
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledTimes(2)
  })

  it('returns to the operations index from the customer list route', async () => {
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations',
    })
  })

  it.each([
    { width: 899, showsMasterBesideDetail: false },
    { width: 900, showsMasterBesideDetail: true },
  ])(
    'uses the canonical mobile XOR boundary at $width pixels',
    async ({ width, showsMasterBesideDetail }) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
      customerWorkspaceMocks.routeState.params = { relationId: '11' }

      const detailWrapper = mount(CustomerWorkspaceView)
      await flushPromises()

      expect(detailWrapper.find('.customer-detail-section').exists()).toBe(true)
      expect(detailWrapper.find('.customer-list-section').exists()).toBe(showsMasterBesideDetail)
      detailWrapper.unmount()

      customerWorkspaceMocks.routeState.params = {}
      const listWrapper = mount(CustomerWorkspaceView)
      await flushPromises()

      expect(listWrapper.find('.customer-list-section').exists()).toBe(true)
      expect(listWrapper.find('.customer-detail-section').exists()).toBe(false)
      listWrapper.unmount()
    },
  )

  it.each([900, 1024, 1440])(
    'uses a full-width split shell for desktop master-detail at %i pixels',
    async (width) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
      customerWorkspaceMocks.routeState.params = { relationId: '11' }

      const wrapper = mount(CustomerWorkspaceView)
      await flushPromises()

      expect(wrapper.get('.ds-workspace').classes()).toContain('ds-workspace--split')
      expect(wrapper.get('.ui-workspace').classes()).not.toContain('ui-workspace--narrow')
      const shellBody = wrapper.get('.ui-v2-workspace-adapter__body')
      expect(shellBody.element.children).toHaveLength(1)
      expect(shellBody.find('.ui-v2-workspace-adapter__aside').exists()).toBe(false)
      expect(customerWorkspaceCss).toContain('@media (min-width: 900px)')
      expect(customerWorkspaceCss).toContain(
        '.ui-v2-workspace-customer-root.ui-v2-workspace-adapter\n    .ui-v2-workspace-adapter__body {\n    grid-template-columns: minmax(0, 1fr);',
      )
      expect(wrapper.find('.customer-list-section').exists()).toBe(true)
      expect(wrapper.find('.customer-detail-section').exists()).toBe(true)
      expect(wrapper.get('.ui-v2-workspace-customer-layout').classes()).toContain(
        'ui-v2-workspace-customer-layout--detail',
      )
    },
  )

  it('keeps workspace touch targets tokenized and removes back motion for reduced-motion users', () => {
    expect(customerWorkspaceCss).toMatch(
      /\.ui-v2-workspace-customer-detail-tabs\s*>\s*button,[\s\S]*\.ui-v2-workspace-customer-filter-chips\s*>\s*button\s*\{\s*min-block-size: var\(--ui-v2-size-target-min\);/,
    )
    expect(customerWorkspaceCss).toMatch(
      /\.ui-v2-workspace-customer-period-tab\s*\{[\s\S]*?min-block-size: var\(--ui-v2-size-target-min\);/,
    )
    expect(customerWorkspaceCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)[\s\S]*?\.ui-v2-workspace-adapter__back\s*\{\s*transition: none;/,
    )
  })

  it('loads route-native customer stats for the detail stats tab', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'stats' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock).toHaveBeenCalledWith(11, 7, {
      signal: expect.anything(),
    })
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

    expect(customerWorkspaceMocks.fetchOwnerCustomerTradesMock).toHaveBeenCalledWith(22, {
      limit: 20,
      signal: expect.anything(),
    })
    expect(wrapper.text()).toContain('محسن')
    expect(wrapper.text()).toContain('فردایی')
  })

  it('reviews before and after values plus the future-only consequence before saving customer limits', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 430 })
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'limits' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    const maxDailyTradesInput = wrapper.find('input[placeholder="مثلاً ۴"]')
    await maxDailyTradesInput.setValue('7')
    await wrapper.get('.customer-edit-form-card .ui-button--primary').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.updateOwnerCustomerRelationMock).not.toHaveBeenCalled()
    expect(wrapper.get('.customer-financial-review').text()).toContain('مرور تغییرات')
    expect(wrapper.get('.customer-financial-review').text()).toContain('قبل')
    expect(wrapper.get('.customer-financial-review').text()).toContain('بعد')
    expect(wrapper.get('.customer-financial-review').text()).toContain('اثر از این لحظه به بعد')
    expect(wrapper.get('.customer-financial-review').text()).toContain(
      'تاریخچه تکمیل‌شده عوض نمی‌شود',
    )
    const reviewTable = wrapper.get('table.ui-v2-workspace-customer-financial-table')
    expect(reviewTable.get('caption').text()).toContain('مقایسه تنظیمات مالی قبل و بعد')
    expect(reviewTable.findAll('thead th[scope="col"]')).toHaveLength(3)
    expect(reviewTable.findAll('tbody th[scope="row"]')).toHaveLength(1)
    expect(reviewTable.findAll('tbody td[data-label]')).toHaveLength(2)

    await wrapper.get('.customer-financial-review__actions .ui-button--primary').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.updateOwnerCustomerRelationMock).toHaveBeenCalledWith(11, {
      max_daily_trades: 7,
    })
    expect(wrapper.text()).toContain('تغییرات ذخیره شد')
  })

  it('keeps the financial draft and review error after a failed PATCH', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'limits' }
    customerWorkspaceMocks.updateOwnerCustomerRelationMock.mockRejectedValueOnce(
      new Error('ثبت مالی انجام نشد.'),
    )

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    await wrapper.get('input[placeholder="مثلاً ۴"]').setValue('9')
    await wrapper.get('.customer-edit-form-card .ui-button--primary').trigger('click')
    await wrapper.get('.customer-financial-review__actions .ui-button--primary').trigger('click')
    await flushPromises()

    expect(wrapper.get('.customer-financial-review').text()).toContain('ثبت مالی انجام نشد.')
    await wrapper.get('.customer-financial-review__actions .ui-button--secondary').trigger('click')
    expect((wrapper.get('input[placeholder="مثلاً ۴"]').element as HTMLInputElement).value).toBe(
      '9',
    )
    expect(wrapper.text()).toContain('ثبت مالی انجام نشد.')
  })

  it('surfaces invalid financial input beside the preserved draft before any PATCH', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'limits' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    const maxDailyTradesInput = wrapper.get('input[placeholder="مثلاً ۴"]')
    await maxDailyTradesInput.setValue('نامعتبر')
    await wrapper.get('.customer-edit-form-card .ui-button--primary').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.updateOwnerCustomerRelationMock).not.toHaveBeenCalled()
    expect(wrapper.find('.customer-financial-review').exists()).toBe(false)
    expect(wrapper.text()).toContain('مقدار «حداکثر تعداد روزانه» باید یک عدد معتبر باشد.')
    expect((maxDailyTradesInput.element as HTMLInputElement).value).toBe('نامعتبر')
  })

  it('keeps a failed direct-detail load distinct from not-found and retries beside the failure', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([])
    customerWorkspaceMocks.fetchOwnerCustomerRelationMock.mockRejectedValueOnce(
      new Error('ارتباط با سرور برقرار نشد.'),
    )

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    expect(wrapper.text()).toContain('دریافت پرونده مشتری ممکن نشد')
    expect(wrapper.text()).toContain('ارتباط با سرور برقرار نشد.')
    expect(wrapper.text()).not.toContain('مشتری پیدا نشد')
    expect(wrapper.text()).not.toContain('هنوز مشتری ثبت نشده است')

    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([])
    customerWorkspaceMocks.fetchOwnerCustomerRelationMock.mockResolvedValueOnce(
      makeCustomerRelation({
        id: 11,
        customer_user_id: 22,
        management_name: 'مشتری تست',
      }),
    )
    await wrapper.get('.customer-detail-retry').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerRelationsMock).toHaveBeenCalledTimes(2)
    expect(customerWorkspaceMocks.fetchOwnerCustomerRelationMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('مشتری تست')
    expect(wrapper.text()).not.toContain('دریافت پرونده مشتری ممکن نشد')
  })

  it('renders a true missing-detail state only after the direct detail endpoint returns 404', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '77' }
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([])

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerRelationMock).toHaveBeenCalledWith(77, {
      signal: expect.any(AbortSignal),
    })
    expect(wrapper.text()).toContain('مشتری پیدا نشد')
    expect(wrapper.text()).not.toContain('دریافت پرونده مشتری ممکن نشد')
  })

  it('retains the customer list, selected detail, search, filter, and unsaved draft across refresh failure and retry', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'limits' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    const snapshot = vm.customerState.relations.value.map((relation: Record<string, unknown>) => ({
      ...relation,
    }))

    await wrapper.get('input[aria-label="جستجوی مشتری"]').setValue('مشتری تست')
    const activeFilter = wrapper.findAll('.ui-filter-chip').find((chip) => chip.text() === 'فعال')
    await activeFilter!.trigger('click')
    await wrapper.get('input[placeholder="مثلاً ۴"]').setValue('7')

    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockRejectedValueOnce(
      new Error('نوسازی مشتریان ناموفق بود.'),
    )
    await vm.loadRelations()
    await flushPromises()

    expect(wrapper.text()).toContain('نوسازی مشتریان ناموفق بود.')
    expect(wrapper.text()).toContain('مشتری تست')
    expect(
      (wrapper.get('input[aria-label="جستجوی مشتری"]').element as HTMLInputElement).value,
    ).toBe('مشتری تست')
    expect(activeFilter!.attributes('aria-selected')).toBe('true')
    expect((wrapper.get('input[placeholder="مثلاً ۴"]').element as HTMLInputElement).value).toBe(
      '7',
    )
    expect(wrapper.text()).not.toContain('مشتری پیدا نشد')

    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce(snapshot)
    await wrapper.get('.customer-list-retry').trigger('click')
    await flushPromises()

    expect((wrapper.get('input[placeholder="مثلاً ۴"]').element as HTMLInputElement).value).toBe(
      '7',
    )
    expect(
      (wrapper.get('input[aria-label="جستجوی مشتری"]').element as HTMLInputElement).value,
    ).toBe('مشتری تست')
    expect(wrapper.text()).not.toContain('نوسازی مشتریان ناموفق بود.')
  })

  it('keeps retained refresh recovery visible beside the selected mobile detail', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 899 })
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    const snapshot = vm.customerState.relations.value.map((relation: Record<string, unknown>) => ({
      ...relation,
    }))

    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockRejectedValueOnce(
      new Error('نوسازی موبایل ناموفق بود.'),
    )
    await vm.loadRelations(true)
    await flushPromises()

    expect(wrapper.find('.customer-detail-section').exists()).toBe(true)
    expect(wrapper.find('.customer-list-section').exists()).toBe(false)
    expect(wrapper.text()).toContain('نوسازی پرونده مشتری ناموفق بود')
    expect(wrapper.text()).toContain('نوسازی موبایل ناموفق بود.')
    expect(wrapper.text()).toContain('مشتری تست')

    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce(snapshot)
    await wrapper.get('.customer-detail-refresh-retry').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerRelationsMock).toHaveBeenCalledTimes(3)
    expect(wrapper.text()).not.toContain('نوسازی موبایل ناموفق بود.')
    expect(wrapper.find('.customer-detail-section').exists()).toBe(true)
  })

  it('uses the strengthened account-deletion contract and preserves trade-history disclosure', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'danger' }
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockResolvedValueOnce({
      id: 11,
      status: 'deleted',
    })

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await wrapper.get('.ui-danger-zone .ui-button--danger').trigger('click')

    const dialog = accountDeletionDialog()
    expect(dialog.text()).toContain('حذف حساب مشتری تست')
    expect(dialog.text()).toContain('دعوت‌های در انتظار مرتبط لغو می‌شوند')
    expect(dialog.text()).toContain('سوابق معاملات حذف نمی‌شوند')
    expect(dialog.get('.ui-button--danger').attributes('disabled')).toBeDefined()

    await dialog.get('input:not([type="checkbox"])').setValue('مشتری تست')
    await dialog.get('input[type="checkbox"]').setValue(true)
    expect(dialog.get('.ui-button--danger').attributes('disabled')).toBeUndefined()
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.deleteOwnerCustomerRelationMock).toHaveBeenCalledWith(
      11,
      'delete-account',
      'حذف حساب مشتری ناموفق بود.',
    )
    expect(hasBodyDialog('.ui-v2-workspace-account-deletion-dialog')).toBe(false)
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-customers',
      query: {},
    })
  })

  it('keeps account deletion open on a failed receipt without exposing server detail or changing the relation', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'danger' }
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockRejectedValueOnce(
      Object.assign(new Error('raw-server-detail: customer_11'), { status: 403 }),
    )

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    await wrapper.get('.ui-danger-zone .ui-button--danger').trigger('click')
    const dialog = accountDeletionDialog()
    await dialog.get('input:not([type="checkbox"])').setValue('مشتری تست')
    await dialog.get('input[type="checkbox"]').setValue(true)
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    const retainedDialog = accountDeletionDialog()
    expect(retainedDialog.text()).toContain('حذف حساب انجام نشد. لطفاً دوباره تلاش کنید.')
    expect(retainedDialog.text()).not.toContain('raw-server-detail')
    expect(vm.customerState.relations.value.some((item: { id: number }) => item.id === 11)).toBe(true)
    expect(customerWorkspaceMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('returns from a deleted detail even when a concurrent refresh removes it before the receipt', async () => {
    const routeScroll = document.createElement('main')
    routeScroll.className = 'app-route-scroll'
    routeScroll.scrollTop = 256
    document.body.append(routeScroll)
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'danger' }
    const pendingDeletion = deferred<{ id: number; status: string }>()
    const pendingNavigation = deferred<void>()
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockReturnValueOnce(
      pendingDeletion.promise,
    )
    customerWorkspaceMocks.routerPushMock.mockReturnValueOnce(pendingNavigation.promise)

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    vm.openAccountDeletionDialog(vm.activeRelation)
    const deletionRequest = vm.handleConfirmAction()

    vm.customerState.relations.value = []
    await flushPromises()
    pendingDeletion.resolve({ id: 11, status: 'deleted' })
    await flushPromises()

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-customers',
      query: {},
    })
    expect(customerWorkspaceMocks.routerReplaceMock).not.toHaveBeenCalledWith({ query: {} })

    pendingNavigation.resolve()
    await deletionRequest
    await flushPromises()

    expect(routeScroll.scrollTop).toBe(0)
    expect(vm.listActionNotice).toBe('حساب «مشتری تست» حذف شد.')
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-customers',
      query: {},
    })
    routeScroll.remove()
  })

  it('closes an active relation without a live account through relation-only copy and receipt semantics', async () => {
    const safeError =
      'بستن رابطه تأیید نشد. اطلاعات نمایش‌داده‌شدهٔ رابطه در این صفحه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
    const rawServerDetail = 'raw-server-detail: customer relation=13'
    const relationOnly = makeCustomerRelation({
      customer_user_id: null,
      customer_account_name: null,
      invitation_account_name: 'reserved13',
    })
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([relationOnly])
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock
      .mockRejectedValueOnce(Object.assign(new Error(rawServerDetail), { status: 403 }))
      .mockResolvedValueOnce({ id: 13, status: 'deleted' })
    customerWorkspaceMocks.routeState.params = { relationId: '13' }
    customerWorkspaceMocks.routeState.query = { tab: 'danger' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)

    expect(vm.availableDetailTabOptions.map((option: { key: string }) => option.key)).toEqual([
      'profile',
      'danger',
    ])
    expect(wrapper.text()).toContain('بستن رابطه بدون حذف حساب')
    expect(hasBodyDialog('.ui-v2-workspace-account-deletion-dialog')).toBe(false)
    await wrapper.get('.ui-danger-zone .ui-button--danger').trigger('click')

    const dialog = confirmDialog()
    expect(dialog.text()).toContain('فقط رابطه «مشتری دوم» بسته شود؟')
    expect(dialog.text()).toContain('رزرو هویت مرتبط با این رابطه آزاد می‌شود')
    expect(dialog.text()).toContain('هیچ آبشار حذف حساب، نشست، پیشنهاد یا تاریخچه‌ای اجرا نمی‌شود')
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    expect(confirmDialog().get('[role="alert"]').text()).toBe(safeError)
    expect(document.body.textContent).not.toContain(rawServerDetail)
    expect(vm.customerState.relations.value.some((item: { id: number }) => item.id === 13)).toBe(
      true,
    )
    expect(customerWorkspaceMocks.routerPushMock).not.toHaveBeenCalled()
    expect(customerWorkspaceMocks.routerReplaceMock).not.toHaveBeenCalled()

    await confirmDialog().get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.deleteOwnerCustomerRelationMock).toHaveBeenCalledWith(
      13,
      'delete-relation',
      'بستن رابطه مشتری ناموفق بود.',
    )
    expect(vm.customerState.relations.value).toEqual([])
    expect(vm.listActionNotice).toBe('رابطه «مشتری دوم» بدون آبشار حذف حساب بسته شد.')
  })

  it('revokes only the pending relation, invitation, and identity reservation', async () => {
    const pending = makeCustomerRelation({
      id: 14,
      customer_user_id: null,
      customer_account_name: null,
      invitation_account_name: 'pending14',
      management_name: 'دعوت چهاردهم',
      status: 'pending',
      activated_at: null,
      expires_at: '2026-08-22T18:30:00Z',
    })
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([pending])
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockResolvedValueOnce({
      id: 14,
      status: 'revoked',
    })
    customerWorkspaceMocks.routeState.params = { relationId: '14' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    await wrapper.get('.customer-pending-detail .ui-button--danger').trigger('click')

    const dialog = confirmDialog()
    expect(dialog.text()).toContain('رابطه در انتظار و دعوت «دعوت چهاردهم» لغو شوند؟')
    expect(dialog.text()).toContain('لینک دعوت و رزرو هویت این دعوت لغو می‌شوند')
    expect(dialog.text()).toContain('هیچ آبشار حذف حساب فعالی اجرا نمی‌شود')
    expect(hasBodyDialog('.ui-v2-workspace-account-deletion-dialog')).toBe(false)
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(customerWorkspaceMocks.deleteOwnerCustomerRelationMock).toHaveBeenCalledWith(
      14,
      'cancel-pending',
      'لغو رابطه در انتظار و دعوت مشتری ناموفق بود.',
    )
    expect(vm.customerState.relations.value).toEqual([])
    expect(vm.listActionNotice).toContain(
      'رابطه در انتظار، دعوت و رزرو هویت «دعوت چهاردهم» لغو شدند',
    )
    expect(vm.listActionNotice).toContain('هیچ حساب فعالی حذف نشد')
  })

  it('keeps a sensitive session confirmation in context, suppresses duplicates, and exposes only a safe error before the expected receipt', async () => {
    const safeSessionTerminationMessage =
      'پایان نشست تأیید نشد. اطلاعات نمایش‌داده‌شدهٔ نشست در این صفحه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'sessions' }
    const pendingTermination = deferred<never>()
    customerWorkspaceMocks.terminateOwnerCustomerSessionMock.mockReturnValueOnce(
      pendingTermination.promise,
    )

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await wrapper.get('.customer-session-actions .ui-button').trigger('click')
    const vm = getCustomerWorkspaceVm(wrapper)

    const firstAttempt = vm.handleConfirmAction()
    const duplicateAttempt = vm.handleConfirmAction()
    expect(customerWorkspaceMocks.terminateOwnerCustomerSessionMock).toHaveBeenCalledTimes(1)
    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    const pushCallsBeforeFailure = customerWorkspaceMocks.routerPushMock.mock.calls.length
    const replaceCallsBeforeFailure = customerWorkspaceMocks.routerReplaceMock.mock.calls.length

    pendingTermination.reject(
      Object.assign(new Error('raw-server-detail: customer_11 / Chrome'), { status: 403 }),
    )
    await Promise.all([firstAttempt, duplicateAttempt])
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    expect(confirmDialog().text()).toContain(safeSessionTerminationMessage)
    expect(confirmDialog().text()).not.toContain('raw-server-detail')
    expect(confirmDialog().text()).not.toContain('customer_11')
    expect(vm.detailSessionsError).toBe('')
    expect(wrapper.text()).toContain('Chrome')
    expect(vm.activeRelation?.id).toBe(11)
    expect(vm.detailSessions.map((session: { id: string }) => session.id)).toContain('session-1')
    expect(customerWorkspaceMocks.routerPushMock.mock.calls).toHaveLength(pushCallsBeforeFailure)
    expect(customerWorkspaceMocks.routerReplaceMock.mock.calls).toHaveLength(
      replaceCallsBeforeFailure,
    )

    customerWorkspaceMocks.terminateOwnerCustomerSessionMock.mockRejectedValueOnce(
      new TypeError('network failure: session-1'),
    )
    await vm.handleConfirmAction()
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    expect(confirmDialog().text()).toContain(safeSessionTerminationMessage)
    expect(confirmDialog().text()).not.toContain('network failure')
    expect(confirmDialog().text()).not.toContain('session-1')
    expect(vm.activeRelation?.id).toBe(11)
    expect(vm.detailSessions.map((session: { id: string }) => session.id)).toContain('session-1')
    expect(customerWorkspaceMocks.routerPushMock.mock.calls).toHaveLength(pushCallsBeforeFailure)
    expect(customerWorkspaceMocks.routerReplaceMock.mock.calls).toHaveLength(
      replaceCallsBeforeFailure,
    )

    customerWorkspaceMocks.terminateOwnerCustomerSessionMock.mockResolvedValueOnce({
      detail: 'raw-receipt-detail',
      terminated_session_id: 'different-session',
      promoted_primary_session_id: null,
    })
    await vm.handleConfirmAction()
    await flushPromises()
    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    expect(confirmDialog().text()).toContain(safeSessionTerminationMessage)
    expect(confirmDialog().text()).not.toContain('raw-receipt-detail')
    expect(confirmDialog().text()).not.toContain('different-session')
    expect(wrapper.text()).toContain('Chrome')
    expect(vm.activeRelation?.id).toBe(11)
    expect(vm.detailSessions.map((session: { id: string }) => session.id)).toContain('session-1')
    expect(customerWorkspaceMocks.routerPushMock.mock.calls).toHaveLength(pushCallsBeforeFailure)
    expect(customerWorkspaceMocks.routerReplaceMock.mock.calls).toHaveLength(
      replaceCallsBeforeFailure,
    )

    customerWorkspaceMocks.terminateOwnerCustomerSessionMock.mockResolvedValueOnce({
      detail: 'done',
      terminated_session_id: 'session-1',
      promoted_primary_session_id: null,
    })
    await vm.handleConfirmAction()
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(false)
    expect(wrapper.find('.customer-session-actions').exists()).toBe(false)
    expect(wrapper.text()).toContain('نشست «Chrome» پایان یافت.')
    expect(wrapper.text()).not.toContain('iran')
    expect(customerWorkspaceMocks.fetchOwnerCustomerSessionsMock).toHaveBeenCalledTimes(1)
  })

  it('aborts a stale relation refresh so create reconciliation cannot be erased', async () => {
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    const staleSnapshot = vm.customerState.relations.value.map(
      (relation: Record<string, unknown>) => ({ ...relation }),
    )
    const staleRefresh = deferred<Array<Record<string, unknown>>>()
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockReturnValueOnce(staleRefresh.promise)
    const refreshRequest = vm.loadRelations(true)
    await flushPromises()
    const staleSignal =
      customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mock.calls.at(-1)?.[0].signal

    customerWorkspaceMocks.createOwnerCustomerRelationMock.mockResolvedValueOnce(
      makeCustomerRelation({
        id: 31,
        customer_user_id: null,
        customer_account_name: null,
        invitation_account_name: 'customer_09120000031',
        mobile_number: '09120000031',
        management_name: 'دعوت تازه',
        status: 'pending',
        web_short_link: 'https://example.test/i/CUST0031',
        activated_at: null,
      }),
    )
    Object.assign(vm.customerState.createForm, {
      management_name: 'دعوت تازه',
      mobile_number: '09120000031',
      customer_tier: 'tier1',
      commission_rate: '0.50',
      min_trade_quantity: '',
      max_trade_quantity: '',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })
    await vm.createRelation()

    expect(staleSignal.aborted).toBe(true)
    staleRefresh.resolve(staleSnapshot)
    await refreshRequest
    await flushPromises()
    expect(
      vm.customerState.relations.value.some((relation: { id: number }) => relation.id === 31),
    ).toBe(true)
    expect(vm.createNotice).toContain('دعوت مشتری با موفقیت ثبت شد.')
  })

  it('aborts a stale relation refresh so PATCH reconciliation cannot be reverted', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'limits' }
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    const staleSnapshot = vm.customerState.relations.value.map(
      (relation: Record<string, unknown>) => ({ ...relation }),
    )
    const staleRefresh = deferred<Array<Record<string, unknown>>>()
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockReturnValueOnce(staleRefresh.promise)
    const refreshRequest = vm.loadRelations(true)
    await flushPromises()
    const staleSignal =
      customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mock.calls.at(-1)?.[0].signal

    await wrapper.get('input[placeholder="مثلاً ۴"]').setValue('9')
    await vm.saveDetailLimits()
    await vm.confirmDetailLimits()

    expect(staleSignal.aborted).toBe(true)
    expect(
      vm.customerState.relations.value.find((relation: { id: number }) => relation.id === 11)
        .max_daily_trades,
    ).toBe(9)
    staleRefresh.resolve(staleSnapshot)
    await refreshRequest
    await flushPromises()
    expect(
      vm.customerState.relations.value.find((relation: { id: number }) => relation.id === 11)
        .max_daily_trades,
    ).toBe(9)
  })

  it('aborts a stale relation refresh so DELETE reconciliation cannot resurrect a removed customer', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'danger' }
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockResolvedValueOnce({
      id: 11,
      status: 'deleted',
    })
    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const vm = getCustomerWorkspaceVm(wrapper)
    const staleSnapshot = vm.customerState.relations.value.map(
      (relation: Record<string, unknown>) => ({ ...relation }),
    )
    const staleRefresh = deferred<Array<Record<string, unknown>>>()
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockReturnValueOnce(staleRefresh.promise)
    const refreshRequest = vm.loadRelations(true)
    await flushPromises()
    const staleSignal =
      customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mock.calls.at(-1)?.[0].signal

    vm.openAccountDeletionDialog(vm.activeRelation)
    await vm.handleConfirmAction()

    expect(staleSignal.aborted).toBe(true)
    expect(
      vm.customerState.relations.value.some((relation: { id: number }) => relation.id === 11),
    ).toBe(false)
    staleRefresh.resolve(staleSnapshot)
    await refreshRequest
    await flushPromises()
    expect(
      vm.customerState.relations.value.some((relation: { id: number }) => relation.id === 11),
    ).toBe(false)
  })

  it('aborts and ignores stale trades, stats, and sessions responses', async () => {
    const staleTrades = deferred<ReturnType<typeof makeTrade>[]>()
    const currentTrades = deferred<ReturnType<typeof makeTrade>[]>()
    customerWorkspaceMocks.fetchOwnerCustomerTradesMock
      .mockReset()
      .mockReturnValueOnce(staleTrades.promise)
      .mockReturnValueOnce(currentTrades.promise)
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'trades' }

    const tradesWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const tradesVm = getCustomerWorkspaceVm(tradesWrapper)
    tradesVm.customerState.relations.value.push(makeCustomerRelation())
    customerWorkspaceMocks.routeState.params = { relationId: '13' }
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerTradesMock).toHaveBeenCalledTimes(2)
    expect(
      customerWorkspaceMocks.fetchOwnerCustomerTradesMock.mock.calls[0]?.[1].signal.aborted,
    ).toBe(true)
    currentTrades.resolve([makeTrade(2, 'پاسخ جاری')])
    await flushPromises()
    staleTrades.resolve([makeTrade(1, 'پاسخ قدیمی')])
    await flushPromises()
    expect(
      tradesVm.detailTrades.map((trade: { counterparty_name: string }) => trade.counterparty_name),
    ).toEqual(['پاسخ جاری'])
    expect(tradesVm.detailTradesLoading).toBe(false)
    tradesWrapper.unmount()

    const staleStats = deferred<ReturnType<typeof makeStats>>()
    const currentStats = deferred<ReturnType<typeof makeStats>>()
    customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock
      .mockReset()
      .mockReturnValueOnce(staleStats.promise)
      .mockReturnValueOnce(currentStats.promise)
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'stats' }

    const statsWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const statsVm = getCustomerWorkspaceVm(statsWrapper)
    statsVm.setStatsPeriod(30)
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock).toHaveBeenCalledTimes(2)
    expect(
      customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock.mock.calls[0]?.[2].signal.aborted,
    ).toBe(true)
    currentStats.resolve(makeStats(11, 30, 30))
    await flushPromises()
    staleStats.resolve(makeStats(11, 7, 7))
    await flushPromises()
    expect(statsVm.detailStats.period_days).toBe(30)
    expect(statsVm.detailStats.trade_count).toBe(30)
    expect(statsVm.detailStatsLoading).toBe(false)
    statsWrapper.unmount()

    const staleSessions = deferred<Array<Record<string, unknown>>>()
    const currentSessions = deferred<Array<Record<string, unknown>>>()
    customerWorkspaceMocks.fetchOwnerCustomerSessionsMock
      .mockReset()
      .mockReturnValueOnce(staleSessions.promise)
      .mockReturnValueOnce(currentSessions.promise)
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'sessions' }

    const sessionsWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const sessionsVm = getCustomerWorkspaceVm(sessionsWrapper)
    sessionsVm.customerState.relations.value.push(makeCustomerRelation())
    customerWorkspaceMocks.routeState.params = { relationId: '13' }
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerSessionsMock).toHaveBeenCalledTimes(2)
    expect(
      customerWorkspaceMocks.fetchOwnerCustomerSessionsMock.mock.calls[0]?.[1].signal.aborted,
    ).toBe(true)
    currentSessions.resolve([
      {
        id: 'session-current',
        device_name: 'Firefox',
        device_ip: null,
        platform: 'web',
        home_server: 'germany',
        is_primary: true,
        is_active: true,
        created_at: null,
        last_active_at: null,
      },
    ])
    await flushPromises()
    staleSessions.resolve([
      {
        id: 'session-stale',
        device_name: 'Chrome قدیمی',
        device_ip: null,
        platform: 'web',
        home_server: 'iran',
        is_primary: true,
        is_active: true,
        created_at: null,
        last_active_at: null,
      },
    ])
    await flushPromises()
    expect(sessionsVm.detailSessions.map((session: { id: string }) => session.id)).toEqual([
      'session-current',
    ])
    expect(sessionsVm.detailSessionsLoading).toBe(false)
  })

  it('caches successful empty detail resources until an explicit refresh', async () => {
    customerWorkspaceMocks.fetchOwnerCustomerTradesMock.mockResolvedValue([])
    customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock.mockResolvedValue(null)
    customerWorkspaceMocks.fetchOwnerCustomerSessionsMock.mockResolvedValue([])
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'trades' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    expect(customerWorkspaceMocks.fetchOwnerCustomerTradesMock).toHaveBeenCalledTimes(1)
    customerWorkspaceMocks.routeState.query = { tab: 'profile' }
    await flushPromises()
    customerWorkspaceMocks.routeState.query = { tab: 'trades' }
    await flushPromises()
    expect(customerWorkspaceMocks.fetchOwnerCustomerTradesMock).toHaveBeenCalledTimes(1)
    await wrapper.get('.customer-detail-toolbar .ui-button--secondary').trigger('click')
    await flushPromises()
    expect(customerWorkspaceMocks.fetchOwnerCustomerTradesMock).toHaveBeenCalledTimes(2)

    customerWorkspaceMocks.routeState.query = { tab: 'stats' }
    await flushPromises()
    expect(customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock).toHaveBeenCalledTimes(1)
    customerWorkspaceMocks.routeState.query = { tab: 'profile' }
    await flushPromises()
    customerWorkspaceMocks.routeState.query = { tab: 'stats' }
    await flushPromises()
    expect(customerWorkspaceMocks.fetchOwnerCustomerTradeStatsMock).toHaveBeenCalledTimes(1)

    customerWorkspaceMocks.routeState.query = { tab: 'sessions' }
    await flushPromises()
    expect(customerWorkspaceMocks.fetchOwnerCustomerSessionsMock).toHaveBeenCalledTimes(1)
    customerWorkspaceMocks.routeState.query = { tab: 'profile' }
    await flushPromises()
    customerWorkspaceMocks.routeState.query = { tab: 'sessions' }
    await flushPromises()
    expect(customerWorkspaceMocks.fetchOwnerCustomerSessionsMock).toHaveBeenCalledTimes(1)
  })

  it('gates stale limit, session, and delete completions to their captured relation', async () => {
    const pendingLimits = deferred<Record<string, unknown>>()
    customerWorkspaceMocks.updateOwnerCustomerRelationMock.mockReturnValueOnce(
      pendingLimits.promise,
    )
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'limits' }

    const limitsWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const limitsVm = getCustomerWorkspaceVm(limitsWrapper)
    limitsVm.customerState.relations.value.push(makeCustomerRelation())
    await limitsWrapper.get('input[placeholder="مثلاً ۴"]').setValue('9')
    await limitsVm.saveDetailLimits()
    const limitsRequest = limitsVm.confirmDetailLimits()
    customerWorkspaceMocks.routeState.params = { relationId: '13' }
    await flushPromises()
    pendingLimits.resolve({
      ...limitsVm.customerState.relations.value.find(
        (relation: { id: number }) => relation.id === 11,
      ),
      max_daily_trades: 9,
    })
    await limitsRequest
    await flushPromises()

    expect(limitsVm.activeRelation.id).toBe(13)
    expect(limitsVm.customerState.detailEditForm.max_daily_trades).toBe('2')
    expect(limitsVm.limitsNotice).toBe('')
    expect(limitsVm.isLimitsReviewOpen).toBe(false)
    limitsWrapper.unmount()

    const pendingTermination = deferred<Record<string, unknown>>()
    customerWorkspaceMocks.terminateOwnerCustomerSessionMock.mockReturnValueOnce(
      pendingTermination.promise,
    )
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'sessions' }

    const sessionsWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const sessionsVm = getCustomerWorkspaceVm(sessionsWrapper)
    sessionsVm.customerState.relations.value.push(makeCustomerRelation())
    sessionsVm.openConfirmDialog(
      'terminate-session',
      sessionsVm.activeRelation,
      sessionsVm.detailSessions[0],
    )
    const terminationRequest = sessionsVm.handleConfirmAction()
    customerWorkspaceMocks.routeState.params = { relationId: '13' }
    await flushPromises()
    pendingTermination.resolve({
      detail: 'done',
      terminated_session_id: 'session-1',
      promoted_primary_session_id: null,
    })
    await terminationRequest
    await flushPromises()

    expect(sessionsVm.activeRelation.id).toBe(13)
    expect(sessionsVm.sessionNotice).toBe('')
    expect(sessionsVm.detailSessions.map((session: { id: string }) => session.id)).toContain(
      'session-1',
    )
    sessionsWrapper.unmount()

    const pendingDelete = deferred<Record<string, unknown>>()
    customerWorkspaceMocks.deleteOwnerCustomerRelationMock.mockReturnValueOnce(
      pendingDelete.promise,
    )
    customerWorkspaceMocks.routerPushMock.mockClear()
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { tab: 'danger' }

    const deleteWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const deleteVm = getCustomerWorkspaceVm(deleteWrapper)
    deleteVm.customerState.relations.value.push(makeCustomerRelation())
    deleteVm.openAccountDeletionDialog(deleteVm.activeRelation)
    const deleteRequest = deleteVm.handleConfirmAction()
    customerWorkspaceMocks.routeState.params = { relationId: '13' }
    await flushPromises()
    pendingDelete.resolve({ id: 11, status: 'deleted' })
    await deleteRequest
    await flushPromises()

    expect(deleteVm.activeRelation.id).toBe(13)
    expect(deleteVm.listActionNotice).toBe('')
    expect(customerWorkspaceMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('keeps pending actions scoped and makes terminal relations read-only', async () => {
    const terminalRelation = makeCustomerRelation({ status: 'expired' })
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([])
    customerWorkspaceMocks.fetchOwnerCustomerRelationMock.mockResolvedValueOnce(terminalRelation)
    customerWorkspaceMocks.routeState.params = { relationId: '13' }
    customerWorkspaceMocks.routeState.query = { tab: 'danger' }

    const terminalWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const terminalVm = getCustomerWorkspaceVm(terminalWrapper)

    expect(terminalWrapper.text()).toContain('رابطه فقط خواندنی است')
    expect(
      terminalVm.availableDetailTabOptions.map((option: { key: string }) => option.key),
    ).toEqual(['profile', 'trades', 'stats'])
    expect(terminalWrapper.find('.ui-danger-zone').exists()).toBe(false)
    expect(terminalWrapper.find('.customer-edit-form-card').exists()).toBe(false)
    expect(terminalWrapper.find('.customer-session-actions').exists()).toBe(false)
    expect(terminalWrapper.text()).toContain('پرونده‌های مشتریان')
    expect(terminalWrapper.text()).not.toContain('مشتریان قابل مدیریت')
    expect(customerWorkspaceMocks.routerReplaceMock).toHaveBeenCalledWith({ query: {} })
    expect(customerWorkspaceMocks.fetchOwnerCustomerRelationMock).toHaveBeenCalledWith(13, {
      signal: expect.any(AbortSignal),
    })
    terminalWrapper.unmount()

    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockResolvedValueOnce([
      makeCustomerRelation({
        id: 14,
        customer_user_id: null,
        customer_account_name: null,
        invitation_account_name: 'pending14',
        status: 'pending',
        expires_at: '2026-08-22T18:30:00Z',
      }),
    ])
    customerWorkspaceMocks.routeState.params = { relationId: '14' }
    customerWorkspaceMocks.routeState.query = { tab: 'sessions' }

    const pendingWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    const pendingVm = getCustomerWorkspaceVm(pendingWrapper)

    expect(pendingWrapper.find('.customer-pending-detail').exists()).toBe(true)
    expect(pendingWrapper.find('.customer-pending-detail .ui-button--danger').text()).toContain(
      'لغو دعوت',
    )
    expect(pendingVm.availableDetailTabOptions).toEqual([])
    expect(pendingWrapper.find('.ui-danger-zone').exists()).toBe(false)
  })

  it('canonicalizes list context bidirectionally across local edits and browser history', async () => {
    customerWorkspaceMocks.routeState.query = {
      q: '  مشتری  ',
      filter: 'invalid-filter',
      scroll: '12.4',
      panel: 'legacy',
      section: 'legacy',
      tab: 'limits',
      unknown: 'drop-me',
    }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    expect(customerWorkspaceMocks.routerReplaceMock).toHaveBeenCalledWith({
      query: { q: 'مشتری', scroll: '12' },
    })
    expect(
      (wrapper.get('input[aria-label="جستجوی مشتری"]').element as HTMLInputElement).value,
    ).toBe('مشتری')

    await wrapper.get('input[aria-label="جستجوی مشتری"]').setValue('علی')
    const activeFilter = wrapper.findAll('.ui-filter-chip').find((chip) => chip.text() === 'فعال')
    await activeFilter!.trigger('click')
    await flushPromises()
    expect(customerWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
      query: { q: 'علی', filter: 'active', scroll: '12' },
    })

    customerWorkspaceMocks.routeState.query = { q: 'بازگشت', filter: 'pending', scroll: '33' }
    await flushPromises()

    expect(
      (wrapper.get('input[aria-label="جستجوی مشتری"]').element as HTMLInputElement).value,
    ).toBe('بازگشت')
    expect(getCustomerWorkspaceVm(wrapper).relationFilter).toBe('pending')
    expect(getCustomerWorkspaceVm(wrapper).savedListScroll).toBe(33)
  })

  it('migrates valid legacy Customer context into native canonical filter, tab, scroll, and create UI', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = {
      panel: 'pending',
      section: 'sessions',
      filter: 'obsolete',
      listScroll: '33',
    }

    const detailWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(getCustomerWorkspaceVm(detailWrapper).relationFilter).toBe('pending')
    expect(
      detailWrapper
        .findAll('.ui-tabs__tab')
        .find((tab) => tab.text() === 'نشست‌ها')
        ?.attributes('aria-selected'),
    ).toBe('true')
    expect(customerWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
      query: { filter: 'pending', scroll: '33', tab: 'sessions' },
    })
    expect(detailWrapper.find('.customer-manager-stub').exists()).toBe(false)
    detailWrapper.unmount()

    customerWorkspaceMocks.routeState.params = {}
    customerWorkspaceMocks.routeState.query = { panel: 'create' }
    const createWrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    expect(document.querySelector('.ui-v2-workspace-customer-create-panel')).not.toBeNull()
    expect(customerWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({ query: {} })
    expect(createWrapper.find('.customer-manager-stub').exists()).toBe(false)
  })

  it('restores list scroll only after the asynchronous relation list is rendered', async () => {
    const pendingRelations = deferred<Array<Record<string, unknown>>>()
    customerWorkspaceMocks.fetchOwnerCustomerRelationsMock.mockReturnValueOnce(
      pendingRelations.promise,
    )
    customerWorkspaceMocks.routeState.query = { scroll: '64' }
    const routeScroll = document.createElement('main')
    routeScroll.className = 'app-route-scroll'
    document.body.append(routeScroll)

    const wrapper = mount(CustomerWorkspaceView, { attachTo: routeScroll })
    expect(wrapper.find('.workspace-relation-list').exists()).toBe(false)
    pendingRelations.resolve([makeCustomerRelation()])
    await flushPromises()
    await flushPromises()

    expect(routeScroll.scrollTop).toBe(64)
    expect((wrapper.get('.workspace-relation-list').element as HTMLElement).scrollTop).toBe(0)

    customerWorkspaceMocks.routeState.query = {}
    await flushPromises()
    await flushPromises()
    expect(routeScroll.scrollTop).toBe(0)

    routeScroll.scrollTop = 64
    wrapper.unmount()
    const remountedWrapper = mount(CustomerWorkspaceView, { attachTo: routeScroll })
    await flushPromises()
    await flushPromises()
    expect(routeScroll.scrollTop).toBe(0)
  })

  it('persists actual route-container scroll on the list route without overwriting detail context', async () => {
    const routeScroll = document.createElement('main')
    routeScroll.className = 'app-route-scroll'
    document.body.append(routeScroll)
    const wrapper = mount(CustomerWorkspaceView, { attachTo: routeScroll })
    await flushPromises()

    routeScroll.scrollTop = 72
    routeScroll.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(customerWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
      query: { scroll: '72' },
    })

    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { scroll: '72', tab: 'profile' }
    await flushPromises()
    routeScroll.scrollTop = 144
    routeScroll.dispatchEvent(new Event('scroll'))
    await flushPromises()

    expect(getCustomerWorkspaceVm(wrapper).savedListScroll).toBe(72)
  })

  it('restores canonical list scroll inside a desktop detail master while leaving the mobile XOR list absent', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 })
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = {
      scroll: '96',
      tab: 'sessions',
      panel: 'legacy',
    }

    const desktopWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(customerWorkspaceMocks.routerReplaceMock).toHaveBeenCalledWith({
      query: { scroll: '96', tab: 'sessions' },
    })
    expect(
      (desktopWrapper.get('.ui-v2-workspace-customer-relation-list').element as HTMLElement)
        .scrollTop,
    ).toBe(96)
    expect(document.documentElement.scrollTop).toBe(0)
    desktopWrapper.unmount()

    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 899 })
    customerWorkspaceMocks.routeState.query = { scroll: '96', tab: 'sessions' }
    const mobileWrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(mobileWrapper.find('.ui-v2-workspace-customer-relation-list').exists()).toBe(false)
    expect(mobileWrapper.find('.customer-detail-section').exists()).toBe(true)
    expect(document.documentElement.scrollTop).toBe(0)
  })

  it('shows recovery detail for an invalid raw route instead of falling back to the mobile list', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 899 })
    customerWorkspaceMocks.routeState.params = { relationId: 'not-a-relation' }
    customerWorkspaceMocks.routeState.query = { tab: 'stats' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    expect(wrapper.find('.customer-detail-section').exists()).toBe(true)
    expect(wrapper.text()).toContain('مشتری پیدا نشد')
    expect(wrapper.find('.customer-list-section').exists()).toBe(false)
    await wrapper.get('.customer-detail-section .ui-button--secondary').trigger('click')
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-customers',
      query: {},
    })
  })
})
