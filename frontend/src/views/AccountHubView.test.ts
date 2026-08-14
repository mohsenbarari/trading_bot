import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AccountHubView from './AccountHubView.vue'

const accountHubSource = readFileSync(resolve(process.cwd(), 'src/views/AccountHubView.vue'), 'utf8')

const accountHubMocks = vi.hoisted(() => ({
  routerPushMock: vi.fn(),
  routerBackMock: vi.fn(),
  currentUserSummary: {
    value: null as null | Record<string, unknown>,
  },
  loadCurrentUserSummaryMock: vi.fn(),
  requestTelegramLinkMock: vi.fn(),
  openTelegramAccountLinkMock: vi.fn(),
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

vi.mock('../services/telegramLink', () => ({
  requestTelegramLink: accountHubMocks.requestTelegramLinkMock,
  openTelegramAccountLink: accountHubMocks.openTelegramAccountLinkMock,
  TELEGRAM_LINK_UNAVAILABLE_MESSAGE: 'لینک اتصال تلگرام آماده نشد.',
  TELEGRAM_LINK_REQUEST_FAILED_MESSAGE: 'ساخت لینک اتصال تلگرام ناموفق بود.',
}))

async function mountView() {
  const wrapper = mount(AccountHubView)
  await flushPromises()
  return wrapper
}

function findAction(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('.hub-action').find((action) => action.text().includes(label))
}

function findSectionGrid(wrapper: ReturnType<typeof mount>, title: string) {
  const section = wrapper
    .findAll('.account-section-card')
    .find((card) => card.get('h2').text() === title)

  if (!section) throw new Error(`Expected ${title} account section`)
  return section.get('.account-action-grid')
}

describe('AccountHubView.vue', () => {
  beforeEach(() => {
    accountHubMocks.routerPushMock.mockReset()
    accountHubMocks.routerBackMock.mockReset()
    accountHubMocks.loadCurrentUserSummaryMock.mockReset()
    accountHubMocks.requestTelegramLinkMock.mockReset()
    accountHubMocks.openTelegramAccountLinkMock.mockReset()
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
      is_customer: false,
    }

    const wrapper = await mountView()

    expect(accountHubMocks.loadCurrentUserSummaryMock).toHaveBeenCalledWith({ force: true })
    expect(wrapper.get('h1.account-page-title').text()).toBe('حساب')
    expect(wrapper.findAll('.account-section-card')).toHaveLength(3)
    expect(wrapper.text()).not.toContain('مرکز حساب کاربری')
    expect(wrapper.text()).toContain('محمد')
    expect(wrapper.find('.account-status-badge').exists()).toBe(false)
    expect(wrapper.find('.account-status-dot').exists()).toBe(false)
    expect(wrapper.text()).toContain('نشست‌های فعال')

    await findAction(wrapper, 'پروفایل من')!.trigger('click')
    await findAction(wrapper, 'نشست‌های فعال')!.trigger('click')
    await findAction(wrapper, 'حافظه و داده‌ها')!.trigger('click')
    await findAction(wrapper, 'اعلان‌ها')!.trigger('click')
    await wrapper.get('.account-return-control').trigger('click')

    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(1, { name: 'profile' })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(2, { name: 'account-security' })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(3, { name: 'account-storage' })
    expect(accountHubMocks.routerPushMock).toHaveBeenNthCalledWith(4, {
      name: 'account-notifications',
    })
    expect(
      new Set(accountHubMocks.routerPushMock.mock.calls.map(([location]) => location.name)).size,
    ).toBe(4)
    expect(accountHubMocks.routerBackMock).toHaveBeenCalledTimes(1)
  })

  it('uses the singleton grid modifier only for one-action profile and notification sections', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 10,
      role: 'عادی',
      account_name: 'desktop-grid-user',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
      can_connect_telegram: true,
    }

    const wrapper = await mountView()
    const profileGrid = findSectionGrid(wrapper, 'پروفایل')
    const securityGrid = findSectionGrid(wrapper, 'امنیت و داده‌ها')
    const notificationGrid = findSectionGrid(wrapper, 'اعلان‌ها')

    expect(profileGrid.findAll('.hub-action')).toHaveLength(1)
    expect(profileGrid.classes()).toContain('account-action-grid--single')
    expect(wrapper.find('.account-telegram-panel').exists()).toBe(true)
    expect(profileGrid.find('.account-telegram-panel').exists()).toBe(false)
    expect(notificationGrid.findAll('.hub-action')).toHaveLength(1)
    expect(notificationGrid.classes()).toContain('account-action-grid--single')
    expect(securityGrid.findAll('.hub-action')).toHaveLength(2)
    expect(securityGrid.classes()).not.toContain('account-action-grid--single')

    expect(accountHubSource).toContain(
      ":class=\"{ 'account-action-grid--single': profileActions.length === 1 }\"",
    )
    expect(accountHubSource).toContain(
      ":class=\"{ 'account-action-grid--single': notificationActions.length === 1 }\"",
    )
    expect(accountHubSource).toContain(
      '.account-action-grid--single {\n  grid-template-columns: minmax(0, 1fr);\n}',
    )
    expect(accountHubSource).toContain(
      '@media (max-width: 700px) {\n  .account-action-grid {\n    grid-template-columns: 1fr;\n  }',
    )
  })

  it('uses the singleton grid modifier for the accountant storage-only security section', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 11,
      role: 'عادی',
      account_name: 'accountant-grid-user',
      account_status: 'active',
      is_accountant: true,
      is_customer: false,
    }

    const wrapper = await mountView()
    const profileGrid = findSectionGrid(wrapper, 'پروفایل')
    const securityGrid = findSectionGrid(wrapper, 'امنیت و داده‌ها')
    const notificationGrid = findSectionGrid(wrapper, 'اعلان‌ها')

    expect(profileGrid.findAll('.hub-action')).toHaveLength(1)
    expect(profileGrid.classes()).toContain('account-action-grid--single')
    expect(securityGrid.findAll('.hub-action')).toHaveLength(1)
    expect(securityGrid.classes()).toContain('account-action-grid--single')
    expect(notificationGrid.findAll('.hub-action')).toHaveLength(1)
    expect(notificationGrid.classes()).toContain('account-action-grid--single')
    expect(accountHubSource).toContain(
      ":class=\"{ 'account-action-grid--single': securityActions.length === 1 }\"",
    )
  })

  it('marks an authoritative cached account as stale while the initial refresh is pending', async () => {
    let resolveRefresh: ((value: Record<string, unknown>) => void) | null = null
    accountHubMocks.currentUserSummary.value = {
      id: 12,
      role: 'عادی',
      account_name: 'cached-account',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
    }
    accountHubMocks.loadCurrentUserSummaryMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveRefresh = resolve
        }),
    )

    const wrapper = await mountView()

    expect(wrapper.get('.account-identity-stale').text()).toContain('نسخه ذخیره‌شده قبلی')
    expect(findAction(wrapper, 'نشست‌های فعال')?.exists()).toBe(true)

    if (!resolveRefresh) throw new Error('Expected identity refresh resolver')
    ;(resolveRefresh as (value: Record<string, unknown>) => void)({
      state: 'ready',
      source: 'network',
      user: accountHubMocks.currentUserSummary.value,
      error: null,
    })
    await flushPromises()

    expect(wrapper.find('.account-identity-stale').exists()).toBe(false)
  })

  it('gives accountants exactly profile, storage and notification destinations without session or logout actions', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 2,
      role: 'عادی',
      account_name: 'accountant',
      account_status: 'active',
      is_accountant: true,
      is_customer: false,
    }

    const wrapper = await mountView()

    expect(wrapper.text()).not.toContain('مدیریت نشست برای حسابدار فعال نیست')
    expect(wrapper.text()).not.toContain('توسط سرگروه مدیریت')
    expect(wrapper.text()).not.toContain('نشست‌ها، حافظه')
    expect(findAction(wrapper, 'نشست‌های فعال')).toBeUndefined()
    expect(wrapper.text()).not.toContain('خروج از حساب')
    expect(wrapper.findAll('.hub-action').map((action) => action.text())).toEqual([
      expect.stringContaining('پروفایل من'),
      expect.stringContaining('حافظه و داده‌ها'),
      expect.stringContaining('اعلان‌ها'),
    ])

    for (const label of ['پروفایل من', 'حافظه و داده‌ها', 'اعلان‌ها']) {
      await findAction(wrapper, label)!.trigger('click')
    }
    expect(accountHubMocks.routerPushMock.mock.calls.map(([location]) => location.name)).toEqual([
      'profile',
      'account-storage',
      'account-notifications',
    ])
  })

  it('keeps account guidance removed without reviving the old accordion or summary-card layout', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 3,
      role: 'عادی',
      account_name: 'user3',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
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
      is_customer: false,
    }

    const wrapper = await mountView()

    expect(wrapper.text()).toContain('blocked-user')
    expect(wrapper.get('.account-status-badge').text()).toBe('حساب غیرفعال')
    expect(wrapper.get('.account-status-dot').classes()).toContain('account-status-dot--danger')
  })

  it('shows a restriction marker without turning normal active state into a positive badge', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 7,
      role: 'عادی',
      account_name: 'restricted-user',
      account_status: 'active',
      global_web_locked_at: '2026-08-09T11:00:00Z',
      is_accountant: false,
      is_customer: false,
    }

    const wrapper = await mountView()

    expect(wrapper.get('.account-status-badge').text()).toBe('دسترسی محدود')
    expect(wrapper.get('.account-status-dot').classes()).toContain('account-status-dot--warning')
  })

  it('adds Telegram connection to profile settings and disables it after linking', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 4,
      role: 'عادی',
      account_name: 'telegram-user',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
      can_connect_telegram: true,
      telegram_linked: false,
    }
    accountHubMocks.requestTelegramLinkMock.mockResolvedValue({
      telegram_linked: false,
      can_connect_telegram: true,
      bot_username: 'example_bot',
      telegram_url: 'https://t.me/example_bot?start=link_token',
      start_parameter: 'link_token',
    })
    accountHubMocks.openTelegramAccountLinkMock.mockReturnValueOnce(true)

    const wrapper = await mountView()

    expect(wrapper.get('.account-telegram-panel').text()).toContain(
      'برای استفاده از امکانات اپ در بستر تلگرام ضربه بزنید!',
    )

    await wrapper.get('.telegram-connect-panel').trigger('click')
    await flushPromises()

    expect(accountHubMocks.requestTelegramLinkMock).toHaveBeenCalledTimes(1)
    expect(accountHubMocks.openTelegramAccountLinkMock).toHaveBeenCalledWith(
      expect.objectContaining({
        bot_username: 'example_bot',
        telegram_url: 'https://t.me/example_bot?start=link_token',
        start_parameter: 'link_token',
      }),
    )

    wrapper.unmount()
    accountHubMocks.requestTelegramLinkMock.mockReset()

    accountHubMocks.currentUserSummary.value = {
      id: 5,
      role: 'عادی',
      account_name: 'linked-user',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
      can_connect_telegram: true,
      telegram_linked: true,
    }

    const linkedWrapper = await mountView()
    expect(linkedWrapper.get('.account-telegram-panel').text()).toContain('متصل')
    expect(linkedWrapper.get('.telegram-connect-panel').attributes('disabled')).toBeDefined()

    await linkedWrapper.get('.telegram-connect-panel').trigger('click')
    expect(accountHubMocks.requestTelegramLinkMock).not.toHaveBeenCalled()
  })

  it('keeps hostile Telegram receipt metadata out of the account surface', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 45,
      role: 'عادی',
      account_name: 'telegram-receipt-user',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
      can_connect_telegram: true,
      telegram_linked: false,
    }
    accountHubMocks.requestTelegramLinkMock.mockResolvedValue({
      telegram_linked: false,
      can_connect_telegram: true,
      bot_username: 'example_bot',
      telegram_url: 'javascript:alert(1)',
      start_parameter: 'link_hostile',
      detail: 'server=iran route=/api/internal/telegram/link',
    })

    const wrapper = await mountView()
    await wrapper.get('.telegram-connect-panel').trigger('click')
    await flushPromises()

    expect(wrapper.get('.telegram-connect-panel__error').text()).toBe(
      'لینک اتصال تلگرام آماده نشد.',
    )
    expect(wrapper.text()).not.toContain('server=iran')
    expect(wrapper.text()).not.toContain('/api/internal/telegram/link')
    expect(wrapper.text()).not.toContain('javascript:alert(1)')
    expect(accountHubMocks.openTelegramAccountLinkMock).toHaveBeenCalledWith(
      expect.objectContaining({ telegram_url: 'javascript:alert(1)' }),
    )
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

  it('renders no account or session destinations for a role-only cached identity', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 91,
      role: 'عادی',
      account_name: 'partial91',
    }
    accountHubMocks.loadCurrentUserSummaryMock.mockResolvedValueOnce({
      state: 'ready',
      source: 'cache',
      user: accountHubMocks.currentUserSummary.value,
      error: null,
    })

    const wrapper = await mountView()

    expect(wrapper.find('.account-identity-error').exists()).toBe(true)
    expect(wrapper.findAll('.hub-action')).toHaveLength(0)
    expect(wrapper.find('.account-compact-header').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/نشست‌های فعال|حافظه و داده‌ها|اعلان‌ها/)
  })

  it('keeps authoritative cached account actions with a stale marker and no active badge', async () => {
    accountHubMocks.currentUserSummary.value = {
      id: 9,
      role: 'عادی',
      account_name: 'cached9',
      account_status: 'active',
      is_accountant: false,
      is_customer: false,
    }
    accountHubMocks.loadCurrentUserSummaryMock.mockResolvedValueOnce({
      state: 'stale',
      source: 'cache',
      user: accountHubMocks.currentUserSummary.value,
      error: new Error('refresh failed'),
    })

    const wrapper = await mountView()

    expect(wrapper.get('.account-identity-stale').text()).toContain('نسخه ذخیره‌شده قبلی')
    expect(wrapper.find('.account-status-badge').exists()).toBe(false)
    expect(wrapper.find('.account-status-dot').exists()).toBe(false)
    expect(findAction(wrapper, 'نشست‌های فعال')?.exists()).toBe(true)
  })
})
