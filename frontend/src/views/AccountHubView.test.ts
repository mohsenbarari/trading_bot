import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountHubView from './AccountHubView.vue'

const accountHubMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routerBackMock: vi.fn(),
  currentUserSummary: {
    value: null as null | Record<string, unknown>,
  },
  loadCurrentUserSummaryMock: vi.fn(),
  requestTelegramLinkMock: vi.fn(),
  openTelegramLinkMock: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: accountHubMocks.routerPushMock,
    back: accountHubMocks.routerBackMock,
  }),
}))

vi.mock('../utils/currentUser', () => ({
  currentUserSummary: accountHubMocks.currentUserSummary,
  loadCurrentUserSummary: accountHubMocks.loadCurrentUserSummaryMock,
}))

vi.mock('../services/telegramLink', () => ({
  requestTelegramLink: accountHubMocks.requestTelegramLinkMock,
  openTelegramLink: accountHubMocks.openTelegramLinkMock,
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
    accountHubMocks.loadCurrentUserSummaryMock.mockReset()
    accountHubMocks.requestTelegramLinkMock.mockReset()
    accountHubMocks.openTelegramLinkMock.mockReset()
    accountHubMocks.currentUserSummary.value = null
    accountHubMocks.loadCurrentUserSummaryMock.mockImplementation(async () => ({
      state: accountHubMocks.currentUserSummary.value ? 'ready' : 'error',
      source: 'network',
      user: accountHubMocks.currentUserSummary.value,
      error: accountHubMocks.currentUserSummary.value ? null : new Error('identity unavailable'),
    }))
  })

  it('renders account sections and routes normal users to profile, sessions, storage and notifications', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 1,
      role: 'عادی',
      full_name: 'محمد',
      account_name: 'mohammad',
      account_status: 'active',
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(accountHubMocks.loadCurrentUserSummaryMock).toHaveBeenCalledWith({ force: true })
    expect(wrapper.findAll('.account-section-card')).toHaveLength(3)
    expect(wrapper.text()).not.toContain('مرکز حساب کاربری')
    expect(wrapper.text()).toContain('محمد')
    expect(wrapper.text()).toContain('فعال')
    expect(wrapper.text()).toContain('نشست‌های فعال')

    await findAction(wrapper, 'پروفایل من')!.trigger('click')
    await findAction(wrapper, 'نشست‌های فعال')!.trigger('click')
    await findAction(wrapper, 'حافظه و داده‌ها')!.trigger('click')
    await findAction(wrapper, 'اعلان‌ها')!.trigger('click')
    await wrapper.get('.account-return-control').trigger('click')

    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(1, { name: 'profile' })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(2, { name: 'account-security' })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(3, { name: 'account-storage' })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(4, { name: 'account-notifications' })
    expect(accountHubMocks.routerBackMock).toHaveBeenCalledTimes(1)
  })

  it('keeps accountant session restrictions visible without exposing the sessions action', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 2,
      role: 'عادی',
      account_name: 'accountant',
      is_accountant: true,
    }

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('مدیریت نشست برای حسابدار فعال نیست')
    expect(findAction(wrapper, 'نشست‌های فعال')).toBeUndefined()
    expect(findAction(wrapper, 'حافظه و داده‌ها')?.exists()).toBe(true)
    expect(wrapper.text()).toContain('دسترسی‌های مجاز حسابدار و حافظه دستگاه')
  })

  it('keeps account guidance removed without reviving the old accordion or summary-card layout', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 3,
      role: 'عادی',
      account_name: 'user3',
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(wrapper.findAll('.ui-metric-card')).toHaveLength(0)
    expect(wrapper.findAll('.account-guidance-item')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('راهنمای دسترسی')
    expect(wrapper.findAll('.account-accordion')).toHaveLength(0)
  })

  it('shows inactive account status in the compact header', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 6,
      role: 'عادی',
      account_name: 'blocked-user',
      account_status: 'inactive',
      is_accountant: false,
    }

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('blocked-user')
    expect(wrapper.text()).toContain('غیرفعال')
    expect(wrapper.get('.account-status-dot').classes()).toContain('account-status-dot--danger')
  })

  it('adds Telegram connection to profile settings and disables it after linking', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 4,
      role: 'عادی',
      account_name: 'telegram-user',
      is_accountant: false,
      can_connect_telegram: true,
      telegram_linked: false,
    }
    accountHubMocks.requestTelegramLinkMock.mockResolvedValue({
      telegram_linked: false,
      can_connect_telegram: true,
      telegram_url: 'https://t.me/example_bot?start=link_token',
    })

    const wrapper = await mountView()

    expect(wrapper.get('.account-telegram-panel').text()).toContain('برای استفاده از امکانات اپ در بستر تلگرام ضربه بزنید!')

    await wrapper.get('.telegram-connect-panel').trigger('click')
    await flushPromises()

    expect(accountHubMocks.requestTelegramLinkMock).toHaveBeenCalledTimes(1)
    expect(accountHubMocks.openTelegramLinkMock).toHaveBeenCalledWith('https://t.me/example_bot?start=link_token')

    wrapper.unmount()
    accountHubMocks.requestTelegramLinkMock.mockReset()

    accountHubMocks.currentUserSummary.value = {
      id: 5,
      role: 'عادی',
      account_name: 'linked-user',
      is_accountant: false,
      can_connect_telegram: true,
      telegram_linked: true,
    }

    const linkedWrapper = await mountView()
    expect(linkedWrapper.get('.account-telegram-panel').text()).toContain('متصل')
    expect(linkedWrapper.get('.telegram-connect-panel').attributes('disabled')).toBeDefined()

    await linkedWrapper.get('.telegram-connect-panel').trigger('click')
    expect(accountHubMocks.requestTelegramLinkMock).not.toHaveBeenCalled()
  })

  it('does not invent identity or active status when the account request fails', async () => {
    accountHubMocks.loadCurrentUserSummaryMock.mockResolvedValueOnce({
      state: 'error',
      source: 'network',
      user: null,
      error: new Error('offline'),
    })

    const wrapper = await mountView()

    expect(wrapper.find('.account-identity-error').exists()).toBe(true)
    expect(wrapper.find('.account-compact-header').exists()).toBe(false)
    expect(wrapper.findAll('.hub-action')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('فعال')
  })

  it('keeps cached account actions with a stale marker and never assumes a missing status is active', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 9,
      role: 'عادی',
      account_name: 'cached9',
      account_status: null,
      is_accountant: false,
    }
    accountHubMocks.loadCurrentUserSummaryMock.mockResolvedValueOnce({
      state: 'stale',
      source: 'cache',
      user: accountHubMocks.currentUserSummary.value,
      error: new Error('refresh failed'),
    })

    const wrapper = await mountView()

    expect(wrapper.get('.account-identity-stale').text()).toContain('نسخه ذخیره‌شده قبلی')
    expect(wrapper.get('.account-status-badge').text()).toBe('نامشخص')
    expect(findAction(wrapper, 'نشست‌های فعال')?.exists()).toBe(true)
  })
})
