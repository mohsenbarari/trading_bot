import { readFileSync } from 'node:fs'
import { DOMWrapper, enableAutoUnmount, flushPromises, mount as mountVue } from '@vue/test-utils'
import type { Ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  AccountantRelation,
  AccountantSessionSummary,
} from '../composables/useOwnerAccountants'
import AccountantWorkspaceView from './AccountantWorkspaceView.vue'

const accountantStage5Css = readFileSync(
  'src/styles/design-system-v2.stage5-accountant.css',
  'utf8',
)

enableAutoUnmount(afterEach)

function mount(component: typeof AccountantWorkspaceView, options: Record<string, unknown> = {}) {
  return mountVue(component, { attachTo: document.body, ...options })
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

type AccountantConfirmAction =
  | 'terminate-session'
  | 'cancel-invitation'
  | 'delete-relation'
  | 'delete-account'

interface AccountantWorkspaceTestVm {
  accountantState: {
    relations: Ref<AccountantRelation[]>
    createForm: {
      account_name: string
      relation_display_name: string
      mobile_number: string
      duty_description: string
    }
    editForm: {
      duty_description: string
    }
  }
  hasLoadedRelations: boolean
  error: string
  listScrollTop: number
  detailSessions: AccountantSessionSummary[]
  detailSessionsError: string
  dutyNotice: string
  dutyError: string
  sessionActionNotice: string
  listActionNotice: string
  isConfirmDialogOpen: boolean
  isCreatePanelOpen: boolean
  openCreatePanel: () => void
  closeCreatePanel: () => void
  createRelation: () => Promise<void>
  copyRegistrationLink: (relation: Record<string, unknown>) => Promise<void>
  loadRelations: (force?: boolean) => Promise<void>
  loadDetailSessions: (force?: boolean) => Promise<void>
  saveDuty: () => Promise<void>
  openConfirmDialog: (
    action: AccountantConfirmAction,
    relation: AccountantRelation | undefined,
    session?: AccountantSessionSummary,
  ) => void
  handleConfirmAction: () => Promise<void>
}

function viewVm(wrapper: ReturnType<typeof mount>) {
  return wrapper.vm as unknown as AccountantWorkspaceTestVm
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

const accountantWorkspaceMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routerReplaceMock: vi.fn(),
  fetchOwnerAccountantRelationMock: vi.fn(),
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
  routeProxy: null as null | {
    params: Record<string, unknown>
    query: Record<string, unknown>
  },
}))

vi.mock('vue-router', async () => {
  const { reactive } = await import('vue')
  accountantWorkspaceMocks.routeProxy = reactive(accountantWorkspaceMocks.routeState)
  return {
    useRoute: () => accountantWorkspaceMocks.routeProxy,
    useRouter: () => ({
      push: accountantWorkspaceMocks.routerPushMock,
      replace: accountantWorkspaceMocks.routerReplaceMock,
    }),
  }
})

vi.mock('../composables/useOwnerAccountants', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../composables/useOwnerAccountants')>()
  return {
    ...actual,
    fetchOwnerAccountantRelation: accountantWorkspaceMocks.fetchOwnerAccountantRelationMock,
    fetchOwnerAccountantRelations: accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock,
    fetchOwnerAccountantSessions: accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock,
    createOwnerAccountantRelation: accountantWorkspaceMocks.createOwnerAccountantRelationMock,
    updateOwnerAccountantRelation: accountantWorkspaceMocks.updateOwnerAccountantRelationMock,
    deleteOwnerAccountantRelation: accountantWorkspaceMocks.deleteOwnerAccountantRelationMock,
    terminateOwnerAccountantSession: accountantWorkspaceMocks.terminateOwnerAccountantSessionMock,
  }
})

describe('AccountantWorkspaceView.vue', () => {
  let pageScrollTop = 0
  const scrollToMock = vi.fn((leftOrOptions: number | ScrollToOptions, top?: number) => {
    pageScrollTop =
      typeof leftOrOptions === 'number'
        ? Math.max(0, Math.floor(top || 0))
        : Math.max(0, Math.floor(leftOrOptions.top || 0))
  })

  beforeEach(() => {
    pageScrollTop = 0
    scrollToMock.mockClear()
    Object.defineProperty(window, 'scrollY', {
      configurable: true,
      get: () => pageScrollTop,
    })
    Object.defineProperty(window, 'scrollTo', {
      configurable: true,
      value: scrollToMock,
    })
    accountantWorkspaceMocks.routerPushMock.mockReset()
    accountantWorkspaceMocks.routerReplaceMock.mockReset()
    accountantWorkspaceMocks.fetchOwnerAccountantRelationMock.mockReset()
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockReset()
    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockReset()
    accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockReset()
    accountantWorkspaceMocks.updateOwnerAccountantRelationMock.mockReset()
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockReset()
    accountantWorkspaceMocks.terminateOwnerAccountantSessionMock.mockReset()
    accountantWorkspaceMocks.fetchOwnerAccountantRelationMock.mockRejectedValue(
      Object.assign(new Error('رابطه حسابدار پیدا نشد.'), { status: 404 }),
    )
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
        web_short_link: 'https://example.test/i/ACCT0012',
        expires_at: '2026-01-09T10:00:00Z',
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
    accountantWorkspaceMocks.terminateOwnerAccountantSessionMock.mockResolvedValue({
      terminated_session_id: 'session-1',
      promoted_primary_session_id: null,
    })
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
      web_short_link: 'https://example.test/i/ACCT0015',
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    })
    accountantWorkspaceMocks.updateOwnerAccountantRelationMock.mockImplementation(
      async (relationId: number, payload: Record<string, unknown>) => ({
        id: relationId,
        owner_user_id: 1,
        accountant_user_id: 22,
        accountant_account_name: 'accountant11',
        global_account_name: 'accountant11',
        relation_display_name: 'حسابدار تست',
        duty_description: (payload.duty_description as string | null | undefined) ?? null,
        mobile_number: '09121111111',
        status: 'active',
        registration_link: null,
        expires_at: null,
        activated_at: '2026-01-02T10:00:00Z',
        deleted_at: null,
        created_at: '2026-01-01T10:00:00Z',
      }),
    )
    accountantWorkspaceMocks.routeState.params = {}
    accountantWorkspaceMocks.routeState.query = {}
  })

  it('renders the route-native accountant workspace without mounting the compatibility manager by default', async () => {
    const wrapper = mount(AccountantWorkspaceView)

    await flushPromises()

    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.get('.ds-workspace').attributes('data-ui-system')).toBe('v2')
    expect(wrapper.find('.ui-v2-workspace-accountant-layout').exists()).toBe(true)
    expect(wrapper.find('.ui-v2-workspace-accountant-list-section').exists()).toBe(true)
    expect(wrapper.text()).toContain('حسابداران')
    expect(wrapper.text()).toContain('لیست حسابداران')
    expect(wrapper.text()).toContain('حسابدار تست')
    expect(wrapper.text()).toContain('روابط ثبت‌شده')
    expect(wrapper.text()).not.toContain('حسابداران قابل مدیریت')
    expect(wrapper.text()).toContain('۱ دعوت نیازمند اقدام')
    expect(wrapper.text()).not.toContain('۲ رابطه')
    expect(wrapper.text()).not.toContain('۱ فعال')
    expect(wrapper.find('.accountant-workspace-action').exists()).toBe(false)
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
    expect(document.querySelectorAll('#accountant-workspace-overlay-host')).toHaveLength(1)
    const createDialog = document.querySelector('.ui-v2-workspace-overlay-panel')
    expect(
      createDialog
        ?.closest('[data-ui-system="v2"]')
        ?.classList.contains('ui-v2-workspace-accountant-root'),
    ).toBe(true)
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
      web_short_link: 'https://example.test/i/ACCT0015',
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
    const vm = viewVm(wrapper)
    Object.assign(vm.accountantState.createForm, {
      account_name: 'accountant۱۵',
      relation_display_name: 'حسابدار جدید',
      mobile_number: '۰۹۱۲۳۳۳۴۴۴۴',
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
    Object.assign(vm.accountantState.createForm, {
      account_name: 'accountant15',
      relation_display_name: 'حسابدار جدید',
      mobile_number: '09123334444',
      duty_description: 'پیگیری پیشنهادها',
    })
    await vm.createRelation()
    await flushPromises()
    expect(wrapper.text()).toContain('دعوت حسابدار با موفقیت ثبت شد.')

    const relation = {
      id: 12,
      web_short_link: 'https://example.test/i/ACCT0012',
    }
    await vm.copyRegistrationLink(relation)
    expect(clipboardWrite).toHaveBeenCalledWith(relation.web_short_link)
    const copiedCard = wrapper
      .findAll('.accountant-pending-card')
      .find((card) => card.text().includes('لینک دعوت کپی شد.'))
    expect(copiedCard?.text()).toContain('دعوت حسابدار')
    expect(copiedCard?.text()).toContain('مهلت استفاده تا')
    await vi.advanceTimersByTimeAsync(1800)

    await vm.copyRegistrationLink({ id: 13, registration_link: null })
    expect(clipboardWrite).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('reconciles a successful null-duty create receipt after the initial list request failed', async () => {
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockRejectedValueOnce(
      new Error('دریافت اولیه حسابداران ناموفق بود.'),
    )
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const created = {
      id: 19,
      owner_user_id: 1,
      accountant_user_id: null,
      accountant_account_name: null,
      global_account_name: 'accountant19',
      relation_display_name: 'حسابدار بازیابی',
      duty_description: null,
      mobile_number: '09129999999',
      status: 'pending',
      registration_link: null,
      expires_at: null,
      activated_at: null,
      deleted_at: null,
      created_at: '2026-01-03T10:00:00Z',
    }
    accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockResolvedValueOnce(created)
    vm.openCreatePanel()
    Object.assign(vm.accountantState.createForm, {
      account_name: 'accountant19',
      relation_display_name: 'حسابدار بازیابی',
      mobile_number: '09129999999',
      duty_description: '',
    })

    await vm.createRelation()
    await flushPromises()

    expect(accountantWorkspaceMocks.createOwnerAccountantRelationMock).toHaveBeenCalledWith({
      account_name: 'accountant19',
      relation_display_name: 'حسابدار بازیابی',
      mobile_number: '09129999999',
      duty_description: null,
    })
    expect(vm.hasLoadedRelations).toBe(true)
    expect(vm.error).toBe('')
    expect(
      vm.accountantState.relations.value.some((relation: { id: number }) => relation.id === 19),
    ).toBe(true)
    expect(wrapper.find('.accountant-global-refresh-error').exists()).toBe(false)
    expect(wrapper.get('.accountant-global-create-notice').text()).toContain(
      'دعوت حسابدار با موفقیت ثبت شد.',
    )
  })

  it('routes relation selection and the single back path with canonical list context', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { section: 'sessions', tab: 'duty' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    await wrapper.get('.workspace-relation-list .ui-list-item').trigger('click')
    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-accountants-detail',
      params: { relationId: '11' },
      query: {},
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'operations-accountants',
      query: {},
    })
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledTimes(2)
  })

  it('returns to the operations index from the accountant list route', async () => {
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    await wrapper.get('.ds-workspace-back').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations',
    })
  })

  it('renders list XOR detail on mobile and keeps a missing deep link recoverable', async () => {
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })

    try {
      const listWrapper = mount(AccountantWorkspaceView)
      await flushPromises()
      expect(listWrapper.find('.accountant-list-section').exists()).toBe(true)
      expect(listWrapper.find('.accountant-detail-section').exists()).toBe(false)
      listWrapper.unmount()

      accountantWorkspaceMocks.routeState.params = { relationId: '999' }
      const detailWrapper = mount(AccountantWorkspaceView)
      await flushPromises()
      expect(detailWrapper.find('.accountant-detail-section').exists()).toBe(true)
      expect(detailWrapper.find('.accountant-list-section').exists()).toBe(false)
      expect(detailWrapper.text()).toContain('حسابدار پیدا نشد')
      expect(detailWrapper.text()).toContain('بازگشت به فهرست')
      detailWrapper.unmount()
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
    }
  })

  it('hydrates and carries search, filter, and scroll in canonical route query', async () => {
    accountantWorkspaceMocks.routeState.query = {
      q: 'حسابدار',
      filter: 'active',
      listScroll: '37',
      panel: 'unknown',
      section: 'sessions',
      tab: 'danger',
    }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    expect(
      (wrapper.get('input[aria-label="جستجوی حسابدار"]').element as HTMLInputElement).value,
    ).toBe('حسابدار')
    expect(
      wrapper
        .findAll('.ui-filter-chip')
        .find((chip) => chip.text() === 'فعال')
        ?.attributes('aria-selected'),
    ).toBe('true')

    expect(scrollToMock).toHaveBeenCalledWith(0, 37)
    pageScrollTop = 88
    window.dispatchEvent(new Event('scroll'))
    await flushPromises()
    await wrapper.get('.workspace-relation-list .ui-list-item').trigger('click')

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-accountants-detail',
      params: { relationId: '11' },
      query: {
        q: 'حسابدار',
        filter: 'active',
        scroll: '88',
      },
    })
  })

  it('persists canonical list scroll through the actual app route container', async () => {
    const routeScroll = document.createElement('main')
    routeScroll.className = 'app-route-scroll'
    document.body.append(routeScroll)
    accountantWorkspaceMocks.routeState.query = { scroll: '96' }

    const wrapper = mount(AccountantWorkspaceView, { attachTo: routeScroll })
    try {
      await flushPromises()
      await flushPromises()

      expect(routeScroll.scrollTop).toBe(96)
      routeScroll.scrollTop = 124
      routeScroll.dispatchEvent(new Event('scroll'))
      await flushPromises()

      expect(viewVm(wrapper).listScrollTop).toBe(124)
      expect(accountantWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
        name: 'operations-accountants',
        params: {},
        query: { scroll: '124' },
      })
    } finally {
      wrapper.unmount()
      routeScroll.remove()
    }
  })

  it('restores canonical list scroll on desktop detail without letting detail scroll overwrite it', async () => {
    const routeScroll = document.createElement('main')
    routeScroll.className = 'app-route-scroll'
    document.body.append(routeScroll)
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { scroll: '96', tab: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView, { attachTo: routeScroll })
    try {
      await flushPromises()
      await flushPromises()

      expect(routeScroll.scrollTop).toBe(96)
      expect(scrollToMock).not.toHaveBeenCalledWith(0, 96)

      routeScroll.scrollTop = 124
      routeScroll.dispatchEvent(new Event('scroll'))
      await flushPromises()

      expect(viewVm(wrapper).listScrollTop).toBe(96)
      expect(accountantWorkspaceMocks.routerReplaceMock).not.toHaveBeenCalledWith(
        expect.objectContaining({
          query: expect.objectContaining({ scroll: '124' }),
        }),
      )

      await wrapper.get('.ds-workspace-back').trigger('click')
      expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
        name: 'operations-accountants',
        query: { scroll: '96' },
      })
    } finally {
      wrapper.unmount()
      routeScroll.remove()
    }
  })

  it('does not overwrite saved list scroll while a mobile detail route is scrolling', async () => {
    const originalWidth = window.innerWidth
    const routeScroll = document.createElement('main')
    routeScroll.className = 'app-route-scroll'
    document.body.append(routeScroll)
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { scroll: '96', tab: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView, { attachTo: routeScroll })
    try {
      await flushPromises()
      await flushPromises()

      routeScroll.scrollTop = 211
      routeScroll.dispatchEvent(new Event('scroll'))
      await flushPromises()

      expect(viewVm(wrapper).listScrollTop).toBe(96)
      expect(accountantWorkspaceMocks.routerReplaceMock).not.toHaveBeenCalledWith(
        expect.objectContaining({
          query: expect.objectContaining({ scroll: '211' }),
        }),
      )
    } finally {
      wrapper.unmount()
      routeScroll.remove()
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
    }
  })

  it('distinguishes search-empty from true-empty and clears list context nearby', async () => {
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    await wrapper.get('input[aria-label="جستجوی حسابدار"]').setValue('نامی که وجود ندارد')
    await flushPromises()

    expect(wrapper.text()).toContain('نتیجه‌ای پیدا نشد')
    expect(wrapper.text()).not.toContain('هنوز حسابداری ثبت نشده است')
    await wrapper.get('.ui-empty-state .ui-button').trigger('click')
    await flushPromises()

    expect(
      (wrapper.get('input[aria-label="جستجوی حسابدار"]').element as HTMLInputElement).value,
    ).toBe('')
    expect(wrapper.text()).toContain('حسابدار تست')
  })

  it('loads route-native accountant sessions for the detail sessions tab', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock).toHaveBeenCalledWith(11, {
      signal: expect.any(AbortSignal),
    })
    expect(wrapper.text()).toContain('نشست‌های فعال حسابدار')
    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).toContain('اصلی')
    expect(wrapper.text()).toContain('وب · آخرین فعالیت')
    expect(wrapper.text()).not.toContain('iran')
  })

  it('terminates only the named session and reports the result beside the session list', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await flushPromises()

    await wrapper.get('.accountant-session-actions .ui-button').trigger('click')
    expect(confirmDialog().text()).toContain('Chrome')
    expect(confirmDialog().text()).toContain('فقط دسترسی همین نشست قطع می‌شود')
    expect(confirmDialog().text()).toContain('نشست‌های دیگر باقی می‌مانند')

    await confirmDialog().get('.ui-button--primary').trigger('click')
    await flushPromises()

    expect(accountantWorkspaceMocks.terminateOwnerAccountantSessionMock).toHaveBeenCalledWith(
      11,
      'session-1',
    )
    expect(wrapper.text()).toContain('نشست «Chrome» پایان یافت.')
    expect(wrapper.text()).not.toContain('iran')
  })

  it('keeps accountant session termination recoverable with one safe error for raw failures and malformed receipts', async () => {
    const safeError =
      'پایان نشست تأیید نشد. اطلاعات نمایش‌داده‌شدهٔ نشست در این صفحه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
    const rawServerDetail = 'raw-server-detail: accountant=11; session=session-1'
    const rawMismatchedReceipt = 'raw-mismatched-receipt: accountant=11; session=different-session'
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }
    accountantWorkspaceMocks.terminateOwnerAccountantSessionMock.mockRejectedValueOnce(
      Object.assign(new Error(rawServerDetail), { status: 403 }),
    )

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await flushPromises()
    const vm = viewVm(wrapper)
    await wrapper.get('.accountant-session-actions .ui-button').trigger('click')
    const pushCallsBeforeFailure = accountantWorkspaceMocks.routerPushMock.mock.calls.length
    const replaceCallsBeforeFailure = accountantWorkspaceMocks.routerReplaceMock.mock.calls.length

    await confirmDialog().get('.ui-button--primary').trigger('click')
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    expect(confirmDialog().get('[role="alert"]').text()).toBe(safeError)
    expect(document.body.textContent).not.toContain(rawServerDetail)
    expect(vm.detailSessions.map((session) => session.id)).toEqual(['session-1'])
    expect(vm.accountantState.relations.value.some((relation) => relation.id === 11)).toBe(true)
    expect(accountantWorkspaceMocks.routerPushMock.mock.calls).toHaveLength(pushCallsBeforeFailure)
    expect(accountantWorkspaceMocks.routerReplaceMock.mock.calls).toHaveLength(
      replaceCallsBeforeFailure,
    )

    accountantWorkspaceMocks.terminateOwnerAccountantSessionMock.mockResolvedValueOnce({
      detail: rawMismatchedReceipt,
      terminated_session_id: 'different-session',
      promoted_primary_session_id: null,
    })
    await confirmDialog().get('.ui-button--primary').trigger('click')
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    expect(confirmDialog().get('[role="alert"]').text()).toBe(safeError)
    expect(confirmDialog().text()).not.toContain(rawMismatchedReceipt)
    expect(confirmDialog().text()).not.toContain('different-session')
    expect(confirmDialog().text()).not.toContain('پاسخ پایان نشست حسابدار معتبر نبود.')
    expect(vm.detailSessions.map((session) => session.id)).toEqual(['session-1'])
    expect(vm.accountantState.relations.value.some((relation) => relation.id === 11)).toBe(true)
    expect(accountantWorkspaceMocks.routerPushMock.mock.calls).toHaveLength(pushCallsBeforeFailure)
    expect(accountantWorkspaceMocks.routerReplaceMock.mock.calls).toHaveLength(
      replaceCallsBeforeFailure,
    )

    accountantWorkspaceMocks.terminateOwnerAccountantSessionMock.mockResolvedValueOnce({
      terminated_session_id: 'session-1',
      promoted_primary_session_id: null,
    })
    await confirmDialog().get('.ui-button--primary').trigger('click')
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(false)
    expect(vm.detailSessions).toEqual([])
    expect(wrapper.text()).toContain('نشست «Chrome» پایان یافت.')
  })

  it('saves duty through the route-native detail form', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    expect(wrapper.get('.accountant-edit-form-card').text()).not.toContain('ثبت معاملات')
    await wrapper.get('textarea').setValue('هماهنگی معاملات روزانه')
    await wrapper.get('.accountant-edit-form-card .ui-button--primary').trigger('click')
    await flushPromises()

    expect(accountantWorkspaceMocks.updateOwnerAccountantRelationMock).toHaveBeenCalledWith(11, {
      duty_description: 'هماهنگی معاملات روزانه',
    })
    expect(wrapper.text()).toContain('شرح وظیفه ذخیره شد')
  })

  it('accepts a null receipt when clearing an accountant duty description', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await wrapper.get('textarea').setValue('')
    await wrapper.get('.accountant-edit-form-card .ui-button--primary').trigger('click')
    await flushPromises()

    expect(accountantWorkspaceMocks.updateOwnerAccountantRelationMock).toHaveBeenCalledWith(11, {
      duty_description: null,
    })
    expect(wrapper.text()).toContain('شرح وظیفه ذخیره شد')
    expect(
      viewVm(wrapper).accountantState.relations.value.find((relation) => relation.id === 11),
    ).toMatchObject({ duty_description: null })
  })

  it('keeps a failed direct-detail load distinct from not-found and retries beside the failure', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockResolvedValueOnce([])
    accountantWorkspaceMocks.fetchOwnerAccountantRelationMock.mockRejectedValueOnce(
      new Error('ارتباط با سرور برقرار نشد.'),
    )

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    expect(wrapper.text()).toContain('دریافت پرونده حسابدار ممکن نشد')
    expect(wrapper.text()).toContain('ارتباط با سرور برقرار نشد.')
    expect(wrapper.text()).not.toContain('حسابدار پیدا نشد')
    expect(wrapper.text()).not.toContain('هنوز حسابداری ثبت نشده است')

    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockResolvedValueOnce([])
    accountantWorkspaceMocks.fetchOwnerAccountantRelationMock.mockResolvedValueOnce({
      id: 11,
      owner_user_id: 1,
      accountant_user_id: 22,
      accountant_account_name: 'accountant11',
      global_account_name: 'accountant11',
      relation_display_name: 'حسابدار تست',
      duty_description: 'ثبت معاملات',
      mobile_number: '09121111111',
      status: 'active',
      registration_link: null,
      expires_at: null,
      activated_at: '2026-01-02T10:00:00Z',
      deleted_at: null,
      created_at: '2026-01-01T10:00:00Z',
    })
    await wrapper.get('.accountant-detail-retry').trigger('click')
    await flushPromises()

    expect(accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock).toHaveBeenCalledTimes(2)
    expect(accountantWorkspaceMocks.fetchOwnerAccountantRelationMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('حسابدار تست')
    expect(wrapper.text()).not.toContain('دریافت پرونده حسابدار ممکن نشد')
  })

  it('retains accountant list, selection, search, filter, and duty draft across refresh failure and retry', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const snapshot = vm.accountantState.relations.value.map((relation) => ({ ...relation }))

    await wrapper.get('input[aria-label="جستجوی حسابدار"]').setValue('حسابدار تست')
    const activeFilter = wrapper.findAll('.ui-filter-chip').find((chip) => chip.text() === 'فعال')
    await activeFilter!.trigger('click')
    await wrapper.get('textarea').setValue('پیش‌نویس ذخیره‌نشده')

    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockRejectedValueOnce(
      new Error('نوسازی حسابداران ناموفق بود.'),
    )
    await vm.loadRelations()
    await flushPromises()

    expect(wrapper.text()).toContain('نوسازی حسابداران ناموفق بود.')
    expect(wrapper.text()).toContain('حسابدار تست')
    expect(
      (wrapper.get('input[aria-label="جستجوی حسابدار"]').element as HTMLInputElement).value,
    ).toBe('حسابدار تست')
    expect(activeFilter!.attributes('aria-selected')).toBe('true')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(
      'پیش‌نویس ذخیره‌نشده',
    )
    expect(wrapper.text()).not.toContain('حسابدار پیدا نشد')

    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockResolvedValueOnce(snapshot)
    await wrapper.get('.accountant-list-retry').trigger('click')
    await flushPromises()

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(
      'پیش‌نویس ذخیره‌نشده',
    )
    expect(
      (wrapper.get('input[aria-label="جستجوی حسابدار"]').element as HTMLInputElement).value,
    ).toBe('حسابدار تست')
    expect(wrapper.text()).not.toContain('نوسازی حسابداران ناموفق بود.')
  })

  it('keeps relation confirmation errors in-dialog and removes only the relation named by the expected receipt', async () => {
    const safeError =
      'لغو رابطه و دعوت تأیید نشد. اطلاعات نمایش‌داده‌شدهٔ رابطه در این صفحه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
    const rawServerDetail = 'raw-server-detail: accountant relation=12'
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await wrapper.get('.accountant-pending-card [aria-label="اقدام‌های دعوت"]').trigger('click')
    await wrapper.get('.accountant-pending-card .ui-action-overflow__item--danger').trigger('click')
    const vm = viewVm(wrapper)

    expect(confirmDialog().text()).toContain('لغو رابطه و دعوت حسابدار')
    expect(confirmDialog().text()).toContain('رابطه و دعوت در انتظار')
    expect(confirmDialog().text()).toContain('رزرو هویت و نام کاربری آزاد می‌شود')
    expect(confirmDialog().text()).toContain('حذف زنجیره‌ای حساب')
    expect(confirmDialog().text()).toContain('اجرا نمی‌شود')
    expect(confirmDialog().text()).not.toContain('آفرهای فعال')
    expect(confirmDialog().text()).not.toContain('سابقه معاملات')

    const pushCallsBeforeFailure = accountantWorkspaceMocks.routerPushMock.mock.calls.length
    const replaceCallsBeforeFailure = accountantWorkspaceMocks.routerReplaceMock.mock.calls.length
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockRejectedValueOnce(
      Object.assign(new Error(rawServerDetail), { status: 403 }),
    )
    await vm.handleConfirmAction()
    await flushPromises()

    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)
    expect(confirmDialog().get('[role="alert"]').text()).toBe(safeError)
    expect(document.body.textContent).not.toContain(rawServerDetail)
    expect(vm.detailSessionsError).toBe('')
    expect(wrapper.text()).toContain('دعوت حسابدار')
    expect(vm.accountantState.relations.value.some((item: { id: number }) => item.id === 12)).toBe(
      true,
    )
    expect(accountantWorkspaceMocks.routerPushMock.mock.calls).toHaveLength(pushCallsBeforeFailure)
    expect(accountantWorkspaceMocks.routerReplaceMock.mock.calls).toHaveLength(
      replaceCallsBeforeFailure,
    )

    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockResolvedValueOnce({
      detail: 'raw-mismatched-receipt: accountant relation=12',
      id: 12,
      status: 'pending',
    })
    await vm.handleConfirmAction()
    await flushPromises()

    expect(confirmDialog().get('[role="alert"]').text()).toBe(safeError)
    expect(confirmDialog().text()).not.toContain('raw-mismatched-receipt')
    expect(vm.accountantState.relations.value.some((item: { id: number }) => item.id === 12)).toBe(
      true,
    )

    const pendingDelete = deferred<Record<string, unknown>>()
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockReturnValueOnce(
      pendingDelete.promise,
    )
    const firstAttempt = vm.handleConfirmAction()
    const duplicateAttempt = vm.handleConfirmAction()
    expect(accountantWorkspaceMocks.deleteOwnerAccountantRelationMock).toHaveBeenCalledTimes(3)
    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(true)

    const relation = vm.accountantState.relations.value.find(
      (item: { id: number }) => item.id === 12,
    )
    pendingDelete.resolve({ ...relation, status: 'revoked' })
    await Promise.all([firstAttempt, duplicateAttempt])
    await flushPromises()

    expect(accountantWorkspaceMocks.deleteOwnerAccountantRelationMock).toHaveBeenLastCalledWith(
      12,
      'cancel-pending',
      'لغو رابطه و دعوت حسابدار ناموفق بود.',
    )
    expect(hasBodyDialog('.ui-confirm-dialog')).toBe(false)
    expect(vm.accountantState.relations.value.some((item: { id: number }) => item.id === 12)).toBe(
      false,
    )
    expect(wrapper.text()).toContain('رابطه و دعوت «دعوت حسابدار» لغو و رزرو هویت آزاد شد.')
  })

  it('uses exact-name acknowledgement for active account deletion and exposes the audited cascade', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'danger' }

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const relation = vm.accountantState.relations.value.find(
      (item: { id: number }) => item.id === 11,
    )
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockResolvedValueOnce({
      ...relation,
      status: 'deleted',
    })

    await wrapper.get('.accountant-detail-list .ui-button--danger').trigger('click')

    const dialog = accountDeletionDialog()
    expect(dialog.text()).toContain('حذف حساب حسابدار تست')
    expect(dialog.text()).toContain('دسترسی وب‌اپ و ربات قطع می‌شود')
    expect(dialog.text()).toContain('آفرهای فعال منقضی می‌شوند')
    expect(dialog.text()).toContain('سوابق معاملات حذف نمی‌شوند')
    const confirmButton = dialog.get('.ui-button--danger')
    expect(confirmButton.attributes('disabled')).toBeDefined()

    await dialog.get('input:not([type="checkbox"])').setValue('حسابدار تست')
    await dialog.get('input[type="checkbox"]').setValue(true)
    expect(confirmButton.attributes('disabled')).toBeUndefined()
    await confirmButton.trigger('click')
    await flushPromises()

    expect(accountantWorkspaceMocks.deleteOwnerAccountantRelationMock).toHaveBeenCalledWith(
      11,
      'delete-account',
      'حذف حساب حسابدار ناموفق بود.',
    )
    expect(hasBodyDialog('.ui-v2-workspace-account-deletion-dialog')).toBe(false)
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-accountants',
      query: {},
    })
  })

  it('keeps account deletion open on a failed receipt without exposing server detail or changing the relation', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'danger' }
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockRejectedValueOnce(
      Object.assign(new Error('raw-server-detail: accountant_11'), { status: 404 }),
    )

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    await wrapper.get('.accountant-detail-list .ui-button--danger').trigger('click')
    const dialog = accountDeletionDialog()
    await dialog.get('input:not([type="checkbox"])').setValue('حسابدار تست')
    await dialog.get('input[type="checkbox"]').setValue(true)
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    const retainedDialog = accountDeletionDialog()
    expect(retainedDialog.text()).toContain('حذف حساب انجام نشد. لطفاً دوباره تلاش کنید.')
    expect(retainedDialog.text()).not.toContain('raw-server-detail')
    expect(vm.accountantState.relations.value.some((item: { id: number }) => item.id === 11)).toBe(true)
    expect(accountantWorkspaceMocks.routerPushMock).not.toHaveBeenCalled()
  })

  it('returns from a deleted detail when refresh reconciliation wins the receipt race', async () => {
    const routeScroll = document.createElement('main')
    routeScroll.className = 'app-route-scroll'
    routeScroll.scrollTop = 256
    document.body.append(routeScroll)
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'danger' }
    const pendingDeletion = deferred<Record<string, unknown>>()
    const pendingNavigation = deferred<void>()
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockReturnValueOnce(
      pendingDeletion.promise,
    )
    accountantWorkspaceMocks.routerPushMock.mockReturnValueOnce(pendingNavigation.promise)

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const relation = vm.accountantState.relations.value.find(
      (item: { id: number }) => item.id === 11,
    )
    vm.openConfirmDialog('delete-account', relation)
    const deletionRequest = vm.handleConfirmAction()

    vm.accountantState.relations.value = vm.accountantState.relations.value.filter(
      (item: { id: number }) => item.id !== 11,
    )
    await flushPromises()
    pendingDeletion.resolve({ id: 11, status: 'deleted' })
    await flushPromises()

    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-accountants',
      query: {},
    })
    expect(accountantWorkspaceMocks.routerReplaceMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ query: {} }),
    )

    pendingNavigation.resolve()
    await deletionRequest
    await flushPromises()

    expect(routeScroll.scrollTop).toBe(0)
    expect(vm.listActionNotice).toBe('حساب «حسابدار تست» حذف شد.')
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-accountants',
      query: {},
    })
    routeScroll.remove()
  })

  it('uses 900px as the adaptive master-detail boundary', async () => {
    const originalWidth = window.innerWidth

    try {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 899 })
      const compactList = mount(AccountantWorkspaceView)
      await flushPromises()
      expect(compactList.find('.accountant-list-section').exists()).toBe(true)
      expect(compactList.find('.accountant-detail-section').exists()).toBe(false)
      compactList.unmount()

      accountantWorkspaceMocks.routeState.params = { relationId: '11' }
      const compactDetail = mount(AccountantWorkspaceView)
      await flushPromises()
      expect(compactDetail.find('.accountant-list-section').exists()).toBe(false)
      expect(compactDetail.find('.accountant-detail-section').exists()).toBe(true)
      compactDetail.unmount()

      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 900 })
      for (const width of [900, 1024, 1440]) {
        Object.defineProperty(window, 'innerWidth', { configurable: true, value: width })
        const wideDetail = mount(AccountantWorkspaceView)
        await flushPromises()
        expect(wideDetail.get('.ds-workspace').classes()).toContain('ds-workspace--split')
        expect(wideDetail.get('.ds-workspace').classes()).not.toContain('ui-workspace--narrow')
        expect(wideDetail.get('.ds-workspace').classes()).toContain(
          'ui-v2-workspace-accountant-root',
        )
        expect(accountantStage5Css).toContain(
          '.ui-v2-workspace-accountant-root.ui-v2-workspace-adapter',
        )
        expect(accountantStage5Css).toContain('grid-template-columns: minmax(0, 1fr);')
        expect(accountantStage5Css).toContain('min-block-size: var(--ui-v2-size-target-min);')
        expect(accountantStage5Css).toContain('@media (prefers-reduced-motion: reduce)')
        expect(accountantStage5Css).toContain('transition: none;')
        expect(accountantStage5Css).not.toContain('padding-bottom')
        expect(accountantStage5Css).not.toContain('position: sticky')
        expect(wideDetail.find('.accountant-list-section').exists()).toBe(true)
        expect(wideDetail.find('.accountant-detail-section').exists()).toBe(true)
        wideDetail.unmount()
      }
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
    }
  })

  it('keeps pending capabilities limited and terminal relations read-only', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '12' }
    accountantWorkspaceMocks.routeState.query = { tab: 'danger' }

    const pendingWrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    expect(pendingWrapper.text()).toContain('دعوت هنوز فعال نشده است')
    expect(pendingWrapper.findAll('.ui-tabs__tab').map((tab) => tab.text())).toEqual([
      'مشخصات',
      'حساس',
    ])
    expect(pendingWrapper.text()).toContain('لغو رابطه و دعوت حسابدار')
    expect(pendingWrapper.text()).not.toContain('نشست‌های فعال حسابدار')
    expect(pendingWrapper.find('textarea').exists()).toBe(false)
    expect(hasBodyDialog('.ui-v2-workspace-account-deletion-dialog')).toBe(false)
    pendingWrapper.unmount()

    accountantWorkspaceMocks.routeState.params = { relationId: '13' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }
    const terminalRelation = {
      id: 13,
      owner_user_id: 1,
      accountant_user_id: 23,
      accountant_account_name: 'accountant13',
      global_account_name: 'accountant13',
      relation_display_name: 'حسابدار پایان‌یافته',
      duty_description: 'شرح قدیمی',
      mobile_number: '09123333333',
      status: 'revoked',
      registration_link: null,
      expires_at: null,
      activated_at: '2026-01-02T10:00:00Z',
      deleted_at: '2026-01-04T10:00:00Z',
      created_at: '2026-01-01T10:00:00Z',
    }
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockResolvedValueOnce([])
    accountantWorkspaceMocks.fetchOwnerAccountantRelationMock.mockResolvedValueOnce(
      terminalRelation,
    )

    const terminalWrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    expect(terminalWrapper.text()).toContain('این رابطه پایان یافته است')
    expect(terminalWrapper.findAll('.ui-tabs__tab').map((tab) => tab.text())).toEqual(['مشخصات'])
    expect(terminalWrapper.find('textarea').exists()).toBe(false)
    expect(terminalWrapper.text()).not.toContain('نشست‌های فعال حسابدار')
    expect(terminalWrapper.text()).not.toContain('حذف حساب')
    expect(accountantWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
      name: 'operations-accountants-detail',
      params: { relationId: '13' },
      query: {},
    })
    expect(accountantWorkspaceMocks.fetchOwnerAccountantRelationMock).toHaveBeenCalledWith(13, {
      signal: expect.any(AbortSignal),
    })
    terminalWrapper.unmount()
  })

  it('caches an empty sessions result and only retries it when explicitly forced', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }
    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockResolvedValue([])

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await flushPromises()
    const vm = viewVm(wrapper)

    expect(accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock).toHaveBeenCalledTimes(1)
    await vm.loadDetailSessions(false)
    await flushPromises()
    expect(accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock).toHaveBeenCalledTimes(1)

    await vm.loadDetailSessions(true)
    await flushPromises()
    expect(accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('نشست فعالی وجود ندارد')
    wrapper.unmount()
  })

  it('aborts stale session loads on relation change and unmount without applying their result', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }
    const staleSessions = deferred<AccountantSessionSummary[]>()
    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockReturnValueOnce(
      staleSessions.promise,
    )

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const firstSignal = accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mock.calls[0]?.[1]
      ?.signal as AbortSignal
    expect(firstSignal.aborted).toBe(false)

    vm.accountantState.relations.value = vm.accountantState.relations.value.filter(
      (relation: { id: number }) => relation.id !== 11,
    )
    await flushPromises()
    expect(firstSignal.aborted).toBe(true)

    staleSessions.resolve([
      {
        id: 'stale-session',
        device_name: 'Stale device',
        device_ip: null,
        platform: 'web',
        home_server: 'iran',
        is_primary: false,
        is_active: true,
        created_at: '2026-01-01T10:00:00Z',
        last_active_at: '2026-01-02T10:00:00Z',
      },
    ])
    await flushPromises()
    expect(vm.detailSessions).toEqual([])
    expect(wrapper.text()).not.toContain('Stale device')
    wrapper.unmount()

    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockClear()
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    const unmountSessions = deferred<Array<Record<string, unknown>>>()
    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockReturnValueOnce(
      unmountSessions.promise,
    )
    const unmountWrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const unmountSignal = accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mock
      .calls[0]?.[1]?.signal as AbortSignal
    expect(unmountSignal.aborted).toBe(false)
    unmountWrapper.unmount()
    expect(unmountSignal.aborted).toBe(true)
    unmountSessions.resolve([])
    await flushPromises()
  })

  it('discards stale duty and session actions after the selected relation changes', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }
    const pendingDuty = deferred<Record<string, unknown>>()
    accountantWorkspaceMocks.updateOwnerAccountantRelationMock.mockReturnValueOnce(
      pendingDuty.promise,
    )

    const dutyWrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const dutyVm = viewVm(dutyWrapper)
    dutyVm.accountantState.editForm.duty_description = 'پیش‌نویس جدید'
    const dutyRequest = dutyVm.saveDuty()
    dutyVm.accountantState.relations.value = dutyVm.accountantState.relations.value.filter(
      (relation: { id: number }) => relation.id !== 11,
    )
    await flushPromises()
    pendingDuty.resolve({ id: 11, status: 'active', duty_description: 'پیش‌نویس جدید' })
    await dutyRequest
    await flushPromises()

    expect(dutyVm.dutyNotice).toBe('')
    expect(dutyVm.dutyError).toBe('')
    expect(
      dutyVm.accountantState.relations.value.some((relation: { id: number }) => relation.id === 11),
    ).toBe(false)
    dutyWrapper.unmount()

    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }
    const pendingTermination = deferred<{
      terminated_session_id: string
      promoted_primary_session_id: string | null
    }>()
    accountantWorkspaceMocks.terminateOwnerAccountantSessionMock.mockReturnValueOnce(
      pendingTermination.promise,
    )
    const sessionWrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await flushPromises()
    const sessionVm = viewVm(sessionWrapper)
    const relation = sessionVm.accountantState.relations.value.find(
      (item: { id: number }) => item.id === 11,
    )
    const session = sessionVm.detailSessions[0]
    sessionVm.openConfirmDialog('terminate-session', relation, session)
    const terminationRequest = sessionVm.handleConfirmAction()
    sessionVm.accountantState.relations.value = sessionVm.accountantState.relations.value.filter(
      (item: { id: number }) => item.id !== 11,
    )
    await flushPromises()
    pendingTermination.resolve({
      terminated_session_id: 'session-1',
      promoted_primary_session_id: null,
    })
    await terminationRequest
    await flushPromises()

    expect(sessionVm.sessionActionNotice).toBe('')
    expect(sessionVm.detailSessions).toEqual([])
    sessionWrapper.unmount()
  })

  it('does not navigate or announce a stale account deletion response', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'danger' }
    const pendingDeletion = deferred<Record<string, unknown>>()
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockReturnValueOnce(
      pendingDeletion.promise,
    )

    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const relation = vm.accountantState.relations.value.find(
      (item: { id: number }) => item.id === 11,
    )
    vm.openConfirmDialog('delete-account', relation)
    const deletionRequest = vm.handleConfirmAction()
    accountantWorkspaceMocks.routeProxy!.params = { relationId: '12' }
    await flushPromises()
    pendingDeletion.resolve({ ...relation, status: 'deleted' })
    await deletionRequest
    await flushPromises()

    expect(vm.listActionNotice).toBe('')
    expect(vm.accountantState.relations.value.some((item: { id: number }) => item.id === 11)).toBe(
      false,
    )
    expect(vm.accountantState.relations.value.some((item: { id: number }) => item.id === 12)).toBe(
      true,
    )
    expect(accountantWorkspaceMocks.routerPushMock).not.toHaveBeenCalled()
    expect(vm.isConfirmDialogOpen).toBe(false)
    wrapper.unmount()
  })

  it('locks create dismissal and controls, preserves a newer draft, and rejects an unrelated receipt', async () => {
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    const pendingCreate = deferred<Record<string, unknown>>()
    accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockReturnValueOnce(
      pendingCreate.promise,
    )

    try {
      const wrapper = mount(AccountantWorkspaceView)
      await flushPromises()
      const vm = viewVm(wrapper)
      vm.openCreatePanel()
      Object.assign(vm.accountantState.createForm, {
        account_name: 'accountant15',
        relation_display_name: 'حسابدار جدید',
        mobile_number: '09123334444',
        duty_description: 'پیگیری پیشنهادها',
      })
      await flushPromises()

      const createRequest = vm.createRelation()
      const duplicateRequest = vm.createRelation()
      await flushPromises()
      expect(accountantWorkspaceMocks.createOwnerAccountantRelationMock).toHaveBeenCalledTimes(1)
      expect(wrapper.get('.ui-v2-workspace-accountant-create-panel').attributes('aria-busy')).toBe(
        'true',
      )
      expect(
        wrapper
          .findAll('.ui-v2-workspace-accountant-create-panel input')
          .every((input) => input.attributes('disabled') !== undefined),
      ).toBe(true)
      expect(
        wrapper
          .findAll('.ui-v2-workspace-accountant-create-actions .ui-button')
          .every((button) => button.attributes('disabled') !== undefined),
      ).toBe(true)
      expect(
        wrapper
          .get('.ui-v2-workspace-accountant-create-actions')
          .element.closest('.ui-v2-workspace-overlay-body'),
      ).not.toBeNull()
      expect(wrapper.find('.ui-bottom-sheet__actions').exists()).toBe(false)
      expect(document.querySelector('.ui-bottom-sheet__header button')).toBeNull()
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      document.querySelector<HTMLElement>('.ui-sheet-backdrop')?.click()
      await flushPromises()
      expect(vm.isCreatePanelOpen).toBe(true)
      vm.closeCreatePanel()
      expect(vm.isCreatePanelOpen).toBe(true)

      vm.accountantState.createForm.duty_description = 'پیش‌نویس جدیدتر'
      pendingCreate.resolve({
        id: 15,
        owner_user_id: 1,
        accountant_user_id: null,
        accountant_account_name: null,
        global_account_name: 'accountant15',
        relation_display_name: 'حسابدار جدید',
        duty_description: 'پیگیری پیشنهادها',
        mobile_number: '09123334444',
        status: 'pending',
        registration_link: null,
        expires_at: null,
        activated_at: null,
        deleted_at: null,
        created_at: '2026-01-03T10:00:00Z',
      })
      await Promise.all([createRequest, duplicateRequest])
      await flushPromises()

      expect(vm.accountantState.createForm.duty_description).toBe('پیش‌نویس جدیدتر')
      expect(vm.isCreatePanelOpen).toBe(true)
      expect(wrapper.get('.accountant-global-create-notice').text()).toContain(
        'دعوت حسابدار با موفقیت ثبت شد.',
      )

      accountantWorkspaceMocks.createOwnerAccountantRelationMock.mockResolvedValueOnce({
        id: 99,
        status: 'pending',
        global_account_name: 'somebody-else',
        relation_display_name: 'فرد دیگر',
        mobile_number: '09120000000',
        duty_description: null,
      })
      await vm.createRelation()
      await flushPromises()
      expect(wrapper.text()).toContain('پاسخ ایجاد حسابدار معتبر نبود.')
      expect(
        vm.accountantState.relations.value.some((relation: { id: number }) => relation.id === 99),
      ).toBe(false)
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
    }
  })

  it('disables duty controls and preserves a newer duty draft when an older save succeeds', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }
    const pendingDuty = deferred<Record<string, unknown>>()
    accountantWorkspaceMocks.updateOwnerAccountantRelationMock.mockReturnValueOnce(
      pendingDuty.promise,
    )
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)

    vm.accountantState.editForm.duty_description = 'متن اول'
    const dutyRequest = vm.saveDuty()
    await flushPromises()
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    expect(
      wrapper
        .findAll('.accountant-edit-form-card .ui-button')
        .every((button) => button.attributes('disabled') !== undefined),
    ).toBe(true)

    vm.accountantState.editForm.duty_description = 'پیش‌نویس جدید'
    const relation = vm.accountantState.relations.value.find(
      (item: { id: number }) => item.id === 11,
    )
    pendingDuty.resolve({ ...relation, duty_description: 'متن اول' })
    await dutyRequest
    await flushPromises()

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('پیش‌نویس جدید')
    expect(wrapper.text()).toContain('تغییر قبلی ذخیره شد')
    expect(wrapper.text()).toContain('پیش‌نویس جدید هنوز ذخیره نشده است')
  })

  it('invalidates sessions when the live accountant identity disappears', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'sessions' }
    const staleSessions = deferred<Array<Record<string, unknown>>>()
    accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mockReturnValueOnce(
      staleSessions.promise,
    )
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const signal = accountantWorkspaceMocks.fetchOwnerAccountantSessionsMock.mock.calls[0]?.[1]
      ?.signal as AbortSignal

    vm.accountantState.relations.value = vm.accountantState.relations.value.map((relation) =>
      relation.id === 11 ? { ...relation, accountant_user_id: null } : relation,
    )
    await flushPromises()
    expect(signal.aborted).toBe(true)
    expect(wrapper.text()).toContain('حساب کاربری متصل در دسترس نیست')
    expect(wrapper.findAll('.ui-tabs__tab').map((tab) => tab.text())).toEqual([
      'مشخصات',
      'شرح وظیفه',
      'حساس',
    ])
    expect(wrapper.text()).not.toContain('نشست‌های فعال حسابدار')

    staleSessions.resolve([
      {
        id: 'stale-after-id-loss',
        device_name: 'نشست قدیمی',
        device_ip: null,
        platform: 'web',
        home_server: 'iran',
        is_primary: true,
        is_active: true,
        created_at: '2026-01-01T10:00:00Z',
        last_active_at: '2026-01-02T10:00:00Z',
      },
    ])
    await flushPromises()
    expect(vm.detailSessions).toEqual([])
    expect(wrapper.text()).not.toContain('نشست قدیمی')
  })

  it('uses relation-only deletion for an active relation without a live account', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '21' }
    accountantWorkspaceMocks.routeState.query = { tab: 'danger' }
    const orphanRelation = {
      id: 21,
      owner_user_id: 1,
      accountant_user_id: null,
      accountant_account_name: null,
      global_account_name: 'orphan21',
      relation_display_name: 'رابطه بدون حساب',
      duty_description: 'بایگانی',
      mobile_number: '09124444444',
      status: 'active',
      registration_link: null,
      expires_at: null,
      activated_at: '2026-01-02T10:00:00Z',
      deleted_at: null,
      created_at: '2026-01-01T10:00:00Z',
    }
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockResolvedValueOnce([
      orphanRelation,
    ])
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockResolvedValueOnce({
      ...orphanRelation,
      status: 'deleted',
    })
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()

    expect(wrapper.text()).toContain('فقط همین رابطه حذف می‌شود')
    expect(wrapper.text()).not.toContain('آفرهای فعال منقضی')
    await wrapper.get('.accountant-detail-list .ui-button--danger').trigger('click')
    expect(hasBodyDialog('.ui-v2-workspace-account-deletion-dialog')).toBe(false)
    expect(confirmDialog().text()).toContain('حذف رابطه رابطه بدون حساب')
    expect(confirmDialog().text()).toContain('حذف زنجیره‌ای')
    expect(confirmDialog().text()).toContain('اجرا نمی‌شود')

    await viewVm(wrapper).handleConfirmAction()
    await flushPromises()
    expect(accountantWorkspaceMocks.deleteOwnerAccountantRelationMock).toHaveBeenCalledWith(
      21,
      'delete-relation',
      'حذف رابطه حسابدار ناموفق بود.',
    )
    expect(accountantWorkspaceMocks.routerPushMock).toHaveBeenCalledWith({
      name: 'operations-accountants',
      query: {},
    })
  })

  it('hydrates legacy panel and section values before replacing them with canonical context', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = {
      panel: 'pending',
      section: 'sessions',
      filter: 'obsolete',
      listScroll: '33',
    }
    const detailWrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    await flushPromises()

    expect(
      detailWrapper
        .findAll('.ui-filter-chip')
        .find((chip) => chip.text() === 'دعوت‌ها')
        ?.attributes('aria-selected'),
    ).toBe('true')
    expect(
      detailWrapper
        .findAll('.ui-tabs__tab')
        .find((tab) => tab.text() === 'نشست‌ها')
        ?.attributes('aria-selected'),
    ).toBe('true')
    expect(accountantWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
      name: 'operations-accountants-detail',
      params: { relationId: '11' },
      query: { filter: 'pending', scroll: '33', tab: 'sessions' },
    })
    detailWrapper.unmount()

    accountantWorkspaceMocks.routeState.params = {}
    accountantWorkspaceMocks.routeState.query = { panel: 'create' }
    const createWrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    expect(createWrapper.find('.ui-v2-workspace-accountant-create-panel').exists()).toBe(true)
    expect(accountantWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
      name: 'operations-accountants',
      params: {},
      query: {},
    })
  })

  it('keeps refresh failure recovery visible on a mobile detail route', async () => {
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }

    try {
      const wrapper = mount(AccountantWorkspaceView)
      await flushPromises()
      accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockRejectedValueOnce(
        new Error('نوسازی در نمای جزئیات ناموفق بود.'),
      )
      await viewVm(wrapper).loadRelations()
      await flushPromises()

      expect(wrapper.find('.accountant-list-section').exists()).toBe(false)
      expect(wrapper.get('.accountant-global-refresh-error').text()).toContain(
        'نوسازی در نمای جزئیات ناموفق بود.',
      )
      expect(wrapper.get('.accountant-global-refresh-error').text()).toContain('تلاش دوباره')
      expect(wrapper.text()).toContain('حسابدار تست')
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
    }
  })

  it('versions and aborts relation-list GETs so an older response cannot erase a created invitation', async () => {
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const staleSnapshot = vm.accountantState.relations.value.map((relation) => ({ ...relation }))
    const staleRefresh = deferred<AccountantRelation[]>()
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockReturnValueOnce(
      staleRefresh.promise,
    )

    const refreshRequest = vm.loadRelations()
    await flushPromises()
    const staleSignal = accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mock.calls.at(
      -1,
    )?.[0]?.signal as AbortSignal
    Object.assign(vm.accountantState.createForm, {
      account_name: 'accountant15',
      relation_display_name: 'حسابدار جدید',
      mobile_number: '09123334444',
      duty_description: 'پیگیری پیشنهادها',
    })
    await vm.createRelation()
    expect(staleSignal.aborted).toBe(true)

    const created = vm.accountantState.relations.value.find(
      (relation: { id: number }) => relation.id === 15,
    )
    if (!created) throw new Error('دعوت ایجادشده برای آزمون در state پیدا نشد.')
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockResolvedValueOnce([
      ...staleSnapshot,
      created,
    ])
    await vm.loadRelations()
    staleRefresh.resolve(staleSnapshot)
    await refreshRequest
    await flushPromises()

    expect(
      vm.accountantState.relations.value.some((relation: { id: number }) => relation.id === 15),
    ).toBe(true)
    expect(wrapper.text()).toContain('دعوت حسابدار با موفقیت ثبت شد.')
  })

  it('keeps successful duty and delete mutations when stale list refreshes settle later', async () => {
    accountantWorkspaceMocks.routeState.params = { relationId: '11' }
    accountantWorkspaceMocks.routeState.query = { tab: 'duty' }
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)
    const staleSnapshot = vm.accountantState.relations.value.map((relation) => ({ ...relation }))

    const staleDutyRefresh = deferred<AccountantRelation[]>()
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockReturnValueOnce(
      staleDutyRefresh.promise,
    )
    const dutyRefreshRequest = vm.loadRelations()
    await flushPromises()
    const dutyRefreshSignal =
      accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mock.calls.at(-1)?.[0]
        ?.signal as AbortSignal
    vm.accountantState.editForm.duty_description = 'شرح ذخیره‌شده'
    await vm.saveDuty()
    expect(dutyRefreshSignal.aborted).toBe(true)
    staleDutyRefresh.resolve(staleSnapshot)
    await dutyRefreshRequest
    expect(
      vm.accountantState.relations.value.find((relation: { id: number }) => relation.id === 11)
        ?.duty_description,
    ).toBe('شرح ذخیره‌شده')

    const staleDeleteRefresh = deferred<AccountantRelation[]>()
    accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mockReturnValueOnce(
      staleDeleteRefresh.promise,
    )
    const deleteRefreshRequest = vm.loadRelations()
    await flushPromises()
    const deleteRefreshSignal =
      accountantWorkspaceMocks.fetchOwnerAccountantRelationsMock.mock.calls.at(-1)?.[0]
        ?.signal as AbortSignal
    const relation = vm.accountantState.relations.value.find(
      (item: { id: number }) => item.id === 11,
    )
    accountantWorkspaceMocks.deleteOwnerAccountantRelationMock.mockResolvedValueOnce({
      ...relation,
      status: 'deleted',
    })
    vm.openConfirmDialog('delete-account', relation)
    await vm.handleConfirmAction()
    expect(deleteRefreshSignal.aborted).toBe(true)
    staleDeleteRefresh.resolve(staleSnapshot)
    await deleteRefreshRequest
    await flushPromises()

    expect(vm.accountantState.relations.value.some((item: { id: number }) => item.id === 11)).toBe(
      false,
    )
  })

  it('rehydrates list controls from history and replaces invalid query keys canonically', async () => {
    const wrapper = mount(AccountantWorkspaceView)
    await flushPromises()
    const vm = viewVm(wrapper)

    accountantWorkspaceMocks.routeProxy!.query = { q: '  دعوت  ', filter: 'pending', scroll: '41' }
    await flushPromises()
    expect(
      (wrapper.get('input[aria-label="جستجوی حسابدار"]').element as HTMLInputElement).value,
    ).toBe('دعوت')
    expect(
      wrapper
        .findAll('.ui-filter-chip')
        .find((chip) => chip.text() === 'دعوت‌ها')
        ?.attributes('aria-selected'),
    ).toBe('true')
    expect(scrollToMock).toHaveBeenCalledWith(0, 41)

    accountantWorkspaceMocks.routeProxy!.query = {
      q: '   ',
      filter: 'unsupported',
      scroll: '-3',
      listScroll: ['88'],
      tab: 'unsupported',
      panel: 'legacy-value',
    }
    await flushPromises()

    expect(
      (wrapper.get('input[aria-label="جستجوی حسابدار"]').element as HTMLInputElement).value,
    ).toBe('')
    expect(
      wrapper
        .findAll('.ui-filter-chip')
        .find((chip) => chip.text() === 'همه')
        ?.attributes('aria-selected'),
    ).toBe('true')
    expect(vm.listScrollTop).toBe(0)
    expect(accountantWorkspaceMocks.routerReplaceMock).toHaveBeenLastCalledWith({
      name: 'operations-accountants',
      params: {},
      query: {},
    })
    wrapper.unmount()
  })
})
