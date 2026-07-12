import { mount } from '@vue/test-utils'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountantWorkspaceView from './AccountantWorkspaceView.vue'

const accountantWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  fetchOwnerAccountantRelationsMock: vi.fn(),
  fetchOwnerAccountantSessionsMock: vi.fn(),
  createOwnerAccountantRelationMock: vi.fn(),
  updateOwnerAccountantRelationMock: vi.fn(),
  deleteOwnerAccountantRelationMock: vi.fn(),
  terminateOwnerAccountantSessionMock: vi.fn(),
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
    createOwnerAccountantRelation: accountantWorkspaceMocks.createOwnerAccountantRelationMock,
    updateOwnerAccountantRelation: accountantWorkspaceMocks.updateOwnerAccountantRelationMock,
    deleteOwnerAccountantRelation: accountantWorkspaceMocks.deleteOwnerAccountantRelationMock,
    terminateOwnerAccountantSession: accountantWorkspaceMocks.terminateOwnerAccountantSessionMock,
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
    accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockReset()
    accountantWorkspaceMocks.updateOwnerAccountantRelationMock.mockReset()
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockReset()
    accountantWorkspaceMocks.terminateOwnerAccountantSessionMock.mockReset()
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
        global_account_name: 'accountant12',
        relation_display_name: 'دعوت حسابدار',
        duty_description: null,
        mobile_number: '09122222222',
        status: 'pending',
        invitation_token: 'token',
        registration_link: 'https://example.test/invite/accountant12',
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
    accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockResolvedValue({
      id: 15,
      owner_user_id: 1,
      accountant_user_id: null,
      accountant_account_name: null,
      global_account_name: 'accountant15',
      relation_display_name: 'حسابدار جدید',
      duty_description: 'پیگیری پیشنهادها',
      mobile_number: '09123334444',
      status: 'pending',
      invitation_token: 'new-token',
      registration_link: 'https://example.test/invite/accountant15',
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    })
    accountantWorkspaceMocks.updateOwnerAccountantRelationMock.mockImplementation(async (relationId: number, payload: Record<string, unknown>) => ({
      id: relationId,
      owner_user_id: 1,
      accountant_user_id: 22,
      accountant_account_name: 'accountant11',
      global_account_name: 'accountant11',
      relation_display_name: 'حسابدار تست',
      duty_description: (payload.duty_description as string | null | undefined) ?? null,
      mobile_number: '09121111111',
      status: 'active',
      invitation_token: null,
      registration_link: null,
      expires_at: null,
      activated_at: '2026-01-02T10:00:00Z',
      deleted_at: null,
      created_at: '2026-01-01T10:00:00Z',
    }))
    accountantWorkspaceMocks.routeState.params = {}
    accountantWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-native accountant workspace without mounting the compatibility manager by default', async () => {
    const wrapper = mount(AccountantWorkspaceView)

    await flushPromises()

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.text()).toContain('حسابداران')
    expect(wrapper.text()).toContain('لیست حسابداران')
    expect(wrapper.text()).toContain('حسابدار تست')
    expect(wrapper.find('.accountant-manager-stub').exists()).toBe(false)
  })

  it('opens the route-native create dialog instead of the compatibility manager', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { section: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView, { attachTo: document.body })
    await flushPromises()
    await wrapper.get('.accountant-workspace-create').trigger('click')

    expect(document.body.textContent).toContain('افزودن حسابدار')
    expect(document.body.textContent).toContain('ثبت دعوت حسابدار')
    expect(wrapper.find('.accountant-manager-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('creates invitations with truthful SMS feedback and copies pending Web links', async () => {
    vi.useFakeTimers()
    const clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: clipboardWrite },
    })
    const created = {
      id: 15,
      owner_user_id: 1,
      accountant_user_id: null,
      accountant_account_name: null,
      global_account_name: 'accountant15',
      relation_display_name: 'حسابدار جدید',
      duty_description: 'پیگیری پیشنهادها',
      mobile_number: '09123334444',
      status: 'pending',
      invitation_token: 'new-token',
      registration_link: 'https://example.test/invite/accountant15',
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    }
    accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockResolvedValueOnce({
      ...created,
      sms_status: 'disabled',
    })

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = wrapper.vm as any
    Object.assign(vm.accountantState.createForm, {
      account_name: 'accountant15',
      relation_display_name: 'حسابدار جدید',
      mobile_number: '09123334444',
      duty_description: 'پیگیری پیشنهادها',
    })

    await vm.createRelation()
    await flushPromises()
    expect(accountantWorkspaceMocks.createOwnerAccountantRelationMock).toHaveBeenCalledWith({
      account_name: 'accountant15',
      relation_display_name: 'حسابدار جدید',
      mobile_number: '09123334444',
      duty_description: 'پیگیری پیشنهادها',
    })
    expect(wrapper.text()).toContain('پیامک دعوت ارسال نشد')

    accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockResolvedValueOnce({
      ...created,
      id: 16,
      sms_status: null,
    })
    await vm.createRelation()
    await flushPromises()
    expect(wrapper.text()).toContain('دعوت حسابدار با موفقیت ثبت شد.')

    const relation = {
      id: 12,
      registration_link: 'https://example.test/invite/accountant12',
    }
    await vm.copyRegistrationLink(relation)
    expect(clipboardWrite).toHaveBeenCalledWith(relation.registration_link)
    await vi.advanceTimersByTimeAsync(1800)

    await vm.copyRegistrationLink({ id: 13, registration_link: null })
    expect(clipboardWrite).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('routes relation selection, detail navigation, list back, and operations actions explicitly', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { section: 'sessions', tab: 'duty' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    await wrapper.get('.workspace-relation-list .ui-list-item').trigger('click')
    await wrapper.get('.accountant-selection-card .ui-button').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')
    await wrapper.get('.accountant-workspace-action').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-accountants-detail',
      params: { relationId: '11' },
      query: { section: 'sessions', tab: 'duty' },
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-accountants-detail',
      params: { relationId: '11' },
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

  it('saves duty through the route-native detail form', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    await wrapper.get('textarea').setValue('هماهنگی معاملات روزانه')
    await wrapper.get('.accountant-edit-form-card .ui-button--primary').trigger('click')
    await flushPromises()

    expect(accountantWorkspaceMocks.updateOwnerAccountantRelationMock).toHaveBeenCalledWith(11, {
      duty_description: 'هماهنگی معاملات روزانه',
    })
    expect(wrapper.text()).toContain('شرح وظیفه ذخیره شد')
  })
})
