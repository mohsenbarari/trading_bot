import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CustomerWorkspaceView from './CustomerWorkspaceView.vue'

const customerWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
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
    customerWorkspaceMocks.routeState.params = {}
    customerWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-level customer workspace and forwards route state to the manager', () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { panel: 'stats' }

    const wrapper = mount(CustomerWorkspaceView)

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.text()).toContain('مشتریان')
    expect(wrapper.get('.stub-presentation').text()).toBe('workspace')
    expect(wrapper.get('.stub-relation').text()).toBe('11')
    expect(wrapper.get('.stub-panel').text()).toBe('stats')
  })

  it('routes relation selection, list back, detail back, and operations actions explicitly', async () => {
    customerWorkspaceMocks.routeState.params = { relationId: '11' }
    customerWorkspaceMocks.routeState.query = { section: 'stats' }

    const wrapper = mount(CustomerWorkspaceView)

    await wrapper.get('.stub-open-relation').trigger('click')
    await wrapper.get('.stub-back-list').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')
    await wrapper.get('.customer-workspace-action').trigger('click')

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-customers-detail',
      params: { relationId: '42' },
      query: { section: 'stats' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-customers',
      query: { section: 'stats' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(3, {
      name: 'operations-customers',
      query: { section: 'stats' },
    })
    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(4, {
      name: 'operations',
    })
  })

  it('returns to the operations index from the customer list route', async () => {
    const wrapper = mount(CustomerWorkspaceView)

    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(customerWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations',
    })
  })
})
