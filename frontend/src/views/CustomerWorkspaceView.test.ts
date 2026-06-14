import { mount } from '@vue/test-utils'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CustomerWorkspaceView from './CustomerWorkspaceView.vue'

const customerWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  fetchOwnerCustomerRelationsMock: vi.fn(),
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
    customerWorkspaceMocks.routeState.params = {}
    customerWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-native customer workspace without mounting the compatibility manager by default', async () => {
    const wrapper = mount(CustomerWorkspaceView)

    await flushPromises()

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.text()).toContain('مشتریان')
    expect(wrapper.text()).toContain('نمای کلی مشتریان')
    expect(wrapper.text()).toContain('مشتری تست')
    expect(wrapper.find('.customer-manager-stub').exists()).toBe(false)
  })

  it('opens the compatibility manager for create actions and forwards route state', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { section: 'stats' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()
    await wrapper.get('.customer-workspace-create').trigger('click')

    expect(wrapper.get('.stub-presentation').text()).toBe('workspace')
    expect(wrapper.get('.stub-relation').text()).toBe('11')
    expect(wrapper.get('.stub-panel').text()).toBe('create')
  })

  it('routes relation selection, manager events, detail back, and operations actions explicitly', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { section: 'stats' }

    const wrapper = mount(CustomerWorkspaceView)
    await flushPromises()

    await wrapper.get('.workspace-relation-list .ui-list-item').trigger('click')
    await wrapper.get('.workspace-detail-card .ui-button').trigger('click')

    await wrapper.get('.stub-open-relation').trigger('click')
    await wrapper.get('.stub-back-list').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')
    await wrapper.get('.customer-workspace-action').trigger('click')

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-customers-detail',
      params: { relationId: '12' },
      query: { section: 'stats' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-customers-detail',
      params: { relationId: '42' },
      query: { section: 'stats' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(3, {
      name: 'operations-customers',
      query: { section: 'stats' },
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
})
