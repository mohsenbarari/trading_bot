import { mount } from '@vue/test-utils'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountantWorkspaceView from './AccountantWorkspaceView.vue'

const accountantWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  fetchOwnerAccountantRelationsMock: vi.fn(),
  fetchOwnerAccountantSessionsMock: vi.fn(),
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

vi.mock('../composables/useOwnerAccountants', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../composables/useOwnerAccountants')>()
  return {
    ...actual,
    fetchOwnerAccountantRelations: accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock,
    fetchOwnerAccountantSessions: accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock,
  }
})

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
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockReset()
    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockReset()
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockResolvedValue([
      {
        id: 11,
        owner_user_id: 1,
        accountant_user_id: 22,
        accountant_account_name: 'accountant11',
        global_account_name: 'accountant11',
        relation_display_name: 'حسابدار تست',
        duty_description: 'ثبت معاملات',
        mobile_number: '09121111111',
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
        accountant_user_id: null,
        accountant_account_name: null,
        global_account_name: null,
        relation_display_name: 'دعوت حسابدار',
        duty_description: null,
        mobile_number: '09122222222',
        status: 'pending',
        invitation_token: 'token',
        registration_link: null,
        expires_at: null,
        activated_at: null,
        deleted_at: null,
        created_at: '2026-01-02T10:00:00Z',
      },
    ])
    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockResolvedValue([
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
    accountantWorkspaceMocks.routeState.params = {}
    accountantWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-native accountant workspace without mounting the compatibility manager by default', async () => {
    const wrapper = mount(AccountantWorkspaceView)

    await flushPromises()

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.text()).toContain('حسابداران')
    expect(wrapper.text()).toContain('نمای کلی حسابداران')
    expect(wrapper.text()).toContain('حسابدار تست')
    expect(wrapper.find('.accountant-manager-stub').exists()).toBe(false)
  })

  it('opens the compatibility manager for create actions and forwards route state', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { section: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await wrapper.get('.accountant-workspace-create').trigger('click')

    expect(wrapper.get('.stub-presentation').text()).toBe('workspace')
    expect(wrapper.get('.stub-relation').text()).toBe('11')
    expect(wrapper.get('.stub-panel').text()).toBe('create')
  })

  it('routes relation selection, manager events, detail back, and operations actions explicitly', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { section: 'sessions', tab: 'duty' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    await wrapper.get('.workspace-relation-list .ui-list-item').trigger('click')
    await wrapper.get('.accountant-detail-list .ui-button').trigger('click')

    await wrapper.get('.stub-open-relation').trigger('click')
    await wrapper.get('.stub-back-list').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')
    await wrapper.get('.accountant-workspace-action').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-accountants-detail',
      params: { relationId: '12' },
      query: { section: 'sessions', tab: 'duty' },
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-accountants-detail',
      params: { relationId: '42' },
      query: { section: 'sessions', tab: 'duty' },
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(3, {
      name: 'operations-accountants',
      query: { section: 'sessions', tab: 'duty' },
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(4, {
      name: 'operations',
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledTimes(4)
  })

  it('returns to the operations index from the accountant list route', async () => {
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations',
    })
  })

  it('loads route-native accountant sessions for the detail sessions tab', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock).toHaveBeenCalledWith(11)
    expect(wrapper.text()).toContain('نشست‌های فعال حسابدار')
    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).toContain('اصلی')
  })
})
