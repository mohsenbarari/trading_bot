import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import OperationsView from './OperationsView.vue'

const operationsViewMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routerBackMock: vi.fn(),
  currentUserSummary: {
    value: null as null | Record<string, unknown>,
  },
  loadCurrentUserSummaryMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: operationsViewMocks.routerPushMock,
    back: operationsViewMocks.routerBackMock,
  }),
}))

vi.mock('../utils/currentUser', () => ({
  currentUserSummary: operationsViewMocks.currentUserSummary,
  loadCurrentUserSummary: operationsViewMocks.loadCurrentUserSummaryMock,
  isAdminRole: (role: string | null | undefined) => role === 'مدیر ارشد' || role === 'مدیر میانی',
  isAuthoritativeCurrentUserSummary: (value: Record<string, unknown> | null | undefined) =>
    Boolean(
      value &&
        typeof value.role === 'string' &&
        value.role.trim() &&
        (value.account_status === 'active' || value.account_status === 'inactive') &&
        typeof value.is_accountant === 'boolean' &&
        typeof value.is_customer === 'boolean',
    ),
}))

async function mountView() {
  const wrapper = mount(OperationsView)
  await flushPromises()
  return wrapper
}

function findAction(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('.operations-action-tile').find((action) => action.text().includes(label))
}

function actionTitles(wrapper: ReturnType<typeof mount>) {
  return wrapper
    .findAll('.operations-action-tile')
    .map((action) => action.get('.ui-action-card__title-row strong').text())
}

describe('OperationsView.vue', () => {
  beforeEach(() => {
    operationsViewMocks.routerPushMock.mockReset()
    operationsViewMocks.routerBackMock.mockReset()
    operationsViewMocks.loadCurrentUserSummaryMock.mockReset()
    operationsViewMocks.currentUserSummary.value = null
    operationsViewMocks.loadCurrentUserSummaryMock.mockImplementation(async () => ({
      state: operationsViewMocks.currentUserSummary.value ? 'ready' : 'error',
      source: 'network',
      user: operationsViewMocks.currentUserSummary.value,
      error: operationsViewMocks.currentUserSummary.value
        ? null
        : new Error('identity unavailable'),
    }))
  })

  it('renders the exact owner and management destinations for super admins without access summaries', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 1,
      role: 'مدیر ارشد',
      account_status: 'active',
      is_customer: false,
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(operationsViewMocks.loadCurrentUserSummaryMock).toHaveBeenCalledWith({ force: true })
    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.get('.ds-workspace').attributes('data-ui-system')).toBe('v2')
    expect(wrapper.findAll('.ds-workspace-section')).toHaveLength(2)
    expect(actionTitles(wrapper)).toEqual([
      'مشتریان',
      'حسابداران',
      'ارسال دعوت‌نامه',
      'مدیریت کاربران',
      'مدیریت کالاها',
      'پیام‌های مدیریت',
      'تنظیمات سیستم',
    ])
    expect(wrapper.text()).not.toMatch(/مسیر فعال|ابزار فعال|وضعیت دسترسی|بر اساس دسترسی/)
    expect(wrapper.find('.operations-header-action').exists()).toBe(false)

    await findAction(wrapper, 'مشتریان')!.trigger('click')
    await findAction(wrapper, 'تنظیمات سیستم')!.trigger('click')

    expect(operationsViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-customers',
    })
    expect(operationsViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'admin-system',
    })
  })

  it('marks authoritative cached actions as stale while the initial refresh is pending', async () => {
    let resolveRefresh: ((value: Record<string, unknown>) => void) | null = null
    operationsViewMocks.currentUserSummary.value = {
      id: 11,
      role: 'مدیر ارشد',
      account_status: 'active',
      is_customer: false,
      is_accountant: false,
    }
    operationsViewMocks.loadCurrentUserSummaryMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve
        }),
    )

    const wrapper = await mountView()

    expect(wrapper.get('.operations-identity-stale').text()).toContain('اقدام‌های ذخیره‌شده قبلی')
    expect(findAction(wrapper, 'تنظیمات سیستم')?.exists()).toBe(true)

    if (!resolveRefresh) throw new Error('Expected identity refresh resolver')
    ;(resolveRefresh as (value: Record<string, unknown>) => void)({
      state: 'ready',
      source: 'network',
      user: operationsViewMocks.currentUserSummary.value,
      error: null,
    })
    await flushPromises()

    expect(wrapper.find('.operations-identity-stale').exists()).toBe(false)
  })

  it('shows only the two truthful relation destinations for a normal owner', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 2,
      role: 'عادی',
      account_status: 'active',
      is_customer: false,
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(actionTitles(wrapper)).toEqual(['مشتریان', 'حسابداران'])
    expect(wrapper.findAll('.ds-workspace-section')).toHaveLength(1)
    expect(wrapper.text()).not.toMatch(/مدیریت کاربران|دسترسی مدیریتی|مسیر فعال|ابزار فعال/)
  })

  it('shows the bounded middle-manager destination set without super-admin tools', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 22,
      role: 'مدیر میانی',
      account_status: 'active',
      is_customer: false,
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(actionTitles(wrapper)).toEqual([
      'مشتریان',
      'حسابداران',
      'ارسال دعوت‌نامه',
      'مدیریت کاربران',
    ])
    expect(wrapper.text()).not.toMatch(/مدیریت کالاها|پیام‌های مدیریت|تنظیمات سیستم/)
  })

  it('gives customer accounts one real continuation without owner or admin cards', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 3,
      role: 'عادی',
      account_status: 'active',
      is_customer: true,
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(actionTitles(wrapper)).toEqual([])
    expect(wrapper.findAll('.ds-workspace-section')).toHaveLength(0)
    expect(wrapper.get('.operations-empty-state').text()).toContain('اقدام فعالی در این بخش ندارید')
    expect(wrapper.text()).not.toMatch(/مشتریان|حسابداران|ارسال دعوت‌نامه|مدیریت کاربران/)

    await wrapper.get('.operations-empty-state .ui-button').trigger('click')
    expect(operationsViewMocks.routerPushMock).toHaveBeenCalledWith({ name: 'account' })
  })

  it('does not misclassify accountants as owners and keeps the same Account continuation', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 4,
      role: 'عادی',
      account_status: 'active',
      is_customer: false,
      is_accountant: true,
    }

    const wrapper = await mountView()

    expect(actionTitles(wrapper)).toEqual([])
    expect(wrapper.findAll('.ds-workspace-section')).toHaveLength(0)
    expect(wrapper.get('.operations-empty-state').text()).toContain('رفتن به حساب')
    expect(wrapper.text()).not.toMatch(/مشتریان|حسابداران|دسترسی|مجوز/)
  })

  it('shows no invented actions before identity authority and offers a real retry', async () => {
    operationsViewMocks.loadCurrentUserSummaryMock
      .mockResolvedValueOnce({
        state: 'error',
        source: 'network',
        user: null,
        error: new Error('offline'),
      })
      .mockImplementationOnce(async () => {
        operationsViewMocks.currentUserSummary.value = {
          id: 7,
          role: 'عادی',
          account_status: 'active',
          is_customer: false,
          is_accountant: false,
        }
        return {
          state: 'ready',
          source: 'network',
          user: operationsViewMocks.currentUserSummary.value,
          error: null,
        }
      })

    const wrapper = await mountView()

    expect(wrapper.find('.operations-identity-error').exists()).toBe(true)
    expect(wrapper.findAll('.operations-action-tile')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('مسیر فعال')

    await wrapper.get('.operations-identity-retry').trigger('click')
    await flushPromises()
    wrapper.vm.$forceUpdate()
    await flushPromises()

    expect(operationsViewMocks.loadCurrentUserSummaryMock).toHaveBeenCalledTimes(2)
  })

  it('renders no actions for a role-only cached identity until authority is complete', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 71,
      role: 'مدیر ارشد',
    }
    operationsViewMocks.loadCurrentUserSummaryMock.mockResolvedValueOnce({
      state: 'ready',
      source: 'cache',
      user: operationsViewMocks.currentUserSummary.value,
      error: null,
    })

    const wrapper = await mountView()

    expect(wrapper.find('.operations-identity-error').exists()).toBe(true)
    expect(actionTitles(wrapper)).toEqual([])
    expect(wrapper.findAll('.ds-workspace-section')).toHaveLength(0)
    expect(wrapper.text()).not.toMatch(/مشتریان|حسابداران|تنظیمات سیستم/)
  })

  it('retains cached access with an explicit stale marker when refresh fails', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 8,
      role: 'مدیر ارشد',
      account_status: 'active',
      is_customer: false,
      is_accountant: false,
    }
    operationsViewMocks.loadCurrentUserSummaryMock.mockResolvedValueOnce({
      state: 'stale',
      source: 'cache',
      user: operationsViewMocks.currentUserSummary.value,
      error: new Error('refresh failed'),
    })

    const wrapper = await mountView()

    expect(wrapper.get('.operations-identity-stale').text()).toContain('اقدام‌های ذخیره‌شده قبلی')
    expect(findAction(wrapper, 'تنظیمات سیستم')?.exists()).toBe(true)
  })
})
