import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountHubView from './AccountHubView.vue'

const accountHubMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routerBackMock: vi.fn(),
  currentUserSummary: {
    value: null as null | Record<string, unknown>,
  },
  primeCurrentUserSummaryMock: vi.fn(async () => null),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: accountHubMocks.routerPushMock,
    back: accountHubMocks.routerBackMock,
  }),
}))

vi.mock('../utils/currentUser', () => ({
  currentUserSummary: accountHubMocks.currentUserSummary,
  primeCurrentUserSummary: accountHubMocks.primeCurrentUserSummaryMock,
}))

async function mountView() {
  const wrapper = mount(AccountHubView)
  await flushPromises()
  return wrapper
}

function findAction(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('.hub-action').find((action) => action.text().includes(label))
}

describe('AccountHubView.vue', () => {
  beforeEach(() => {
    accountHubMocks.routerPushMock.mockReset()
    accountHubMocks.routerBackMock.mockReset()
    accountHubMocks.primeCurrentUserSummaryMock.mockClear()
    accountHubMocks.currentUserSummary.value = null
  })

  it('renders account accordions and routes normal users to profile, sessions, storage and notifications', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 1,
      role: 'عادی',
      full_name: 'محمد',
      account_name: 'mohammad',
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(accountHubMocks.primeCurrentUserSummaryMock).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.account-accordion')).toHaveLength(3)
    expect(wrapper.find('#account-profile-header').attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('#account-profile-panel').attributes('role')).toBe('region')
    expect(wrapper.text()).toContain('محمد')
    expect(wrapper.text()).toContain('نشست‌های فعال')

    await findAction(wrapper, 'پروفایل من')!.trigger('click')
    await findAction(wrapper, 'نشست‌های فعال')!.trigger('click')
    await findAction(wrapper, 'حافظه و داده‌ها')!.trigger('click')
    await findAction(wrapper, 'اعلان‌ها')!.trigger('click')

    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(1, { name: 'profile' })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(2, {
      name: 'account-security',
    })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(3, {
      name: 'account-storage',
    })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(4, { name: 'account-notifications' })
  })

  it('keeps accountant session restrictions visible without exposing the sessions action', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 2,
      role: 'عادی',
      account_name: 'accountant',
      is_accountant: true,
    }

    const wrapper = await mountView()

    expect(wrapper.find('#account-security-header').attributes('aria-controls')).toBe('account-security-panel')
    expect(wrapper.text()).toContain('مدیریت نشست برای حسابدار فعال نیست')
    expect(findAction(wrapper, 'نشست‌های فعال')).toBeUndefined()
    expect(findAction(wrapper, 'حافظه و داده‌ها')?.exists()).toBe(true)
    expect(wrapper.text()).toContain('تنظیمات مجاز حساب حسابدار')
  })

  it('collapses account sections with the project accordion contract', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 3,
      role: 'عادی',
      account_name: 'user3',
      is_accountant: false,
    }

    const wrapper = await mountView()

    const header = wrapper.findAll('.account-accordion-header')[0]!
    expect(header.attributes('aria-expanded')).toBe('true')
    await header.trigger('click')

    expect(wrapper.findAll('.account-accordion')[0]!.classes()).not.toContain('open')
    expect(header.attributes('aria-expanded')).toBe('false')
  })
})
