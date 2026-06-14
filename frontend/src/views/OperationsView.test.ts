import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import OperationsView from './OperationsView.vue'

const operationsViewMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routerBackMock: vi.fn(),
  currentUserSummary: {
    value: null as null | Record<string, unknown>,
  },
  primeCurrentUserSummaryMock: vi.fn(async () => null),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: operationsViewMocks.routerPushMock,
    back: operationsViewMocks.routerBackMock,
  }),
}))

vi.mock('../utils/currentUser', () => ({
  currentUserSummary: operationsViewMocks.currentUserSummary,
  primeCurrentUserSummary: operationsViewMocks.primeCurrentUserSummaryMock,
  isAdminRole: (role: string | null | undefined) => role === 'مدیر ارشد' || role === 'مدیر میانی',
}))

async function mountView() {
  const wrapper = mount(OperationsView)
  await flushPromises()
  return wrapper
}

function findAction(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('.hub-action').find((action) => action.text().includes(label))
}

describe('OperationsView.vue', () => {
  beforeEach(() => {
    operationsViewMocks.routerPushMock.mockReset()
    operationsViewMocks.routerBackMock.mockReset()
    operationsViewMocks.primeCurrentUserSummaryMock.mockClear()
    operationsViewMocks.currentUserSummary.value = null
  })

  it('renders accordion groups and full management actions for super admins', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 1,
      role: 'مدیر ارشد',
      is_customer: false,
    }

    const wrapper = await mountView()

    expect(operationsViewMocks.primeCurrentUserSummaryMock).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.operations-accordion')).toHaveLength(3)
    expect(wrapper.find('#operations-management-header').attributes('aria-controls')).toBe('operations-management-panel')
    expect(wrapper.find('#operations-management-panel').attributes('role')).toBe('region')
    expect(wrapper.text()).toContain('دسترسی کامل مدیریتی')
    expect(wrapper.text()).toContain('تنظیمات سیستم')

    await findAction(wrapper, 'مشتریان')!.trigger('click')
    await findAction(wrapper, 'تنظیمات سیستم')!.trigger('click')

    expect(operationsViewMocks.routerPushMock).toHaveBeenNthCalledWith(1, {
      name: 'profile',
      query: { workspace: 'customers' },
    })
    expect(operationsViewMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'admin',
      query: { section: 'settings' },
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

    const managementHeader = wrapper.findAll('.operations-accordion-header')[1]!
    expect(managementHeader.attributes('aria-expanded')).toBe('true')
    await managementHeader.trigger('click')
    expect(wrapper.findAll('.operations-accordion')[1]!.classes()).not.toContain('open')
    expect(managementHeader.attributes('aria-expanded')).toBe('false')
  })

  it('explains why relation management is hidden for customer accounts', async () => {
    operationsViewMocks.currentUserSummary.value = {
      id: 3,
      role: 'عادی',
      is_customer: true,
    }

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('این بخش برای حساب مشتری فعال نیست')
    expect(wrapper.findAll('.hub-action').some((action) => action.text().includes('دعوت، مدیریت، محدودیت'))).toBe(false)
    expect(findAction(wrapper, 'اعلان‌ها')?.exists()).toBe(true)
  })
})
