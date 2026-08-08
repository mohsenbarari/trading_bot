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
}))

async function mountView() {
  const wrapper = mount(OperationsView)
  await flushPromises()
  return wrapper
}

function findAction(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('.operations-action-tile').find((action) => action.text().includes(label))
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
      error: operationsViewMocks.currentUserSummary.value ? null : new Error('identity unavailable'),
    }))
  })

  it('renders the workspace index and full management actions for super admins', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 1,
      role: 'مدیر ارشد',
      is_customer: false,
    }

    const wrapper = await mountView()

    expect(operationsViewMocks.loadCurrentUserSummaryMock).toHaveBeenCalledWith({ force: true })
    expect(wrapper.find('.ds-workspace').exists()).toBe(true)
    expect(wrapper.findAll('.ds-workspace-section')).toHaveLength(3)
    expect(wrapper.text()).toContain('دسترسی کامل مدیریتی')
    expect(wrapper.text()).toContain('تنظیمات سیستم')
    expect(wrapper.text()).not.toContain('مسیر جدید')
    expect(wrapper.text()).toContain('مدیریت مشتریان و حسابداران از مسیرهای اختصاصی همین بخش.')

    await findAction(wrapper, 'مشتریان')!.trigger('click')
    await findAction(wrapper, 'تنظیمات سیستم')!.trigger('click')

    expect(operationsViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'operations-customers',
    })
    expect(operationsViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'admin-system',
    })
  })

  it('shows a management empty state for non-admin users while keeping relation actions', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 2,
      role: 'عادی',
      is_customer: false,
    }

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('دسترسی مدیریتی فعال نیست')
    expect(wrapper.text()).not.toContain('ارسال دعوت‌نامه')
    expect(findAction(wrapper, 'مشتریان')?.exists()).toBe(true)
    expect(wrapper.text()).toContain('مدیریت: ندارد')
    expect(wrapper.text()).toContain('ندارد')
  })

  it('explains why relation management is hidden for customer accounts', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 3,
      role: 'عادی',
      is_customer: true,
    }

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('این بخش برای حساب مشتری فعال نیست')
    expect(wrapper.findAll('.operations-action-tile').some((action) => action.text().includes('دعوت، مدیریت، محدودیت'))).toBe(false)
    expect(wrapper.find('.operations-header-action').exists()).toBe(true)
  })

  it('shows no invented actions before identity authority and offers a real retry', async () => {
    operationsViewMocks.loadCurrentUserSummaryMock
      .mockResolvedValueOnce({ state: 'error', source: 'network', user: null, error: new Error('offline') })
      .mockImplementationOnce(async () => {
        operationsViewMocks.currentUserSummary.value = {
          id: 7,
          role: 'عادی',
          is_customer: false,
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

  it('retains cached access with an explicit stale marker when refresh fails', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 8,
      role: 'مدیر ارشد',
      is_customer: false,
    }
    operationsViewMocks.loadCurrentUserSummaryMock.mockResolvedValueOnce({
      state: 'stale',
      source: 'cache',
      user: operationsViewMocks.currentUserSummary.value,
      error: new Error('refresh failed'),
    })

    const wrapper = await mountView()

    expect(wrapper.get('.operations-identity-stale').text()).toContain('دسترسی‌های ذخیره‌شده قبلی')
    expect(findAction(wrapper, 'تنظیمات سیستم')?.exists()).toBe(true)
  })
})
