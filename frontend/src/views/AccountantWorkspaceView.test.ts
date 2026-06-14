import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountantWorkspaceView from './AccountantWorkspaceView.vue'

const accountantWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routeState: {
    params: {} as Record<string, unknown>,
    query: {} as Record<string, unknown>,
  },
}))

vi.mock('vue-router', () => ({
  useRoute: () => accountantWorkspaceMocks.routeState,
  useRouter: () => ({
    push: accountantWorkspaceMocks.routerPushMock,
  }),
}))

vi.mock('../components/OwnerAccountantManagerModal.vue', () => ({
  default: {
    name: 'OwnerAccountantManagerModal',
    props: ['presentation', 'initialRelationId', 'initialPanel'],
    emits: ['close', 'open-relation', 'back-to-list'],
    template: `
      <section class="accountant-manager-stub">
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

describe('AccountantWorkspaceView.vue', () => {
  beforeEach(() => {
    accountantWorkspaceMocks.routerPushMock.mockReset()
    accountantWorkspaceMocks.routeState.params = {}
    accountantWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-level accountant workspace and forwards route state to the manager', () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { panel: 'pending' }

    const wrapper = mount(AccountantWorkspaceView)

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.text()).toContain('حسابداران')
    expect(wrapper.get('.stub-presentation').text()).toBe('workspace')
    expect(wrapper.get('.stub-relation').text()).toBe('11')
    expect(wrapper.get('.stub-panel').text()).toBe('pending')
  })

  it('routes relation selection, list back, detail back, and operations actions explicitly', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { section: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView)

    await wrapper.get('.stub-open-relation').trigger('click')
    await wrapper.get('.stub-back-list').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')
    await wrapper.get('.accountant-workspace-action').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-accountants-detail',
      params: { relationId: '42' },
      query: { section: 'sessions' },
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-accountants',
      query: { section: 'sessions' },
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(3, {
      name: 'operations-accountants',
      query: { section: 'sessions' },
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(4, {
      name: 'operations',
    })
  })

  it('returns to the operations index from the accountant list route', async () => {
    const wrapper = mount(AccountantWorkspaceView)

    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations',
    })
  })
})
