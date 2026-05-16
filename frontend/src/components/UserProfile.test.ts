import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiFetchMock = vi.fn()

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

vi.mock('../composables/useUserProfileTiming', () => ({
  useUserProfileTiming: () => ({
    countdownRestriction: null,
    countdownLimitation: null,
    parseJalaliToIranISO: vi.fn(),
    toEnglishDigits: (value: string) => value,
  }),
}))

vi.mock('lucide-vue-next', () => ({
  ArrowRight: { template: '<span />' },
  ChevronLeft: { template: '<span />' },
}))

vi.mock('vue3-persian-datetime-picker', () => ({
  default: { template: '<div />' },
}))

function makeResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

function makeUser(overrides: Record<string, unknown> = {}) {
  return {
    id: 12,
    account_name: 'owner12',
    mobile_number: '09120000000',
    role: 'عادی',
    has_bot_access: true,
    can_block_users: true,
    max_blocked_users: 10,
    trading_restricted_until: null,
    trading_restricted_until_jalali: null,
    max_daily_trades: null,
    max_active_commodities: null,
    max_daily_requests: null,
    limitations_expire_at: null,
    limitations_expire_at_jalali: null,
    trades_count: 0,
    commodities_traded_count: 0,
    channel_messages_count: 0,
    max_sessions: 2,
    max_accountants: 3,
    ...overrides,
  }
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  if (!button) throw new Error(`Button not found: ${text}`)
  return button
}

describe('UserProfile.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    vi.spyOn(window, 'confirm').mockImplementation(() => true)
  })

  it('allows admin to update max accountants for an owner', async () => {
    const user = makeUser()

    apiFetchMock.mockResolvedValueOnce(makeResponse({
      ...user,
      max_accountants: 6,
    }))

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    const input = wrapper.get('.max-accountants-input')
    await input.setValue('6')
    await input.trigger('change')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/users/12', {
      method: 'PUT',
      body: JSON.stringify({ max_accountants: 6 }),
    })
    expect(user.max_accountants).toBe(6)
  })

  it('allows admin to update block permissions and terminate all sessions', async () => {
    const user = makeUser({
      id: 15,
      account_name: 'owner15',
      mobile_number: '09123334444',
    })

    apiFetchMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        can_block_users: false,
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        can_block_users: false,
        max_blocked_users: 25,
      }))
      .mockResolvedValueOnce(makeResponse({
        detail: '3 نشست پایان یافت',
        terminated_sessions: 3,
      }))

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.toggle-block-capability-btn').trigger('click')
    await flushPromises()

    const maxBlockedInput = wrapper.get('.max-blocked-users-input')
    await maxBlockedInput.setValue('25')
    await flushPromises()

    await wrapper.get('.terminate-sessions-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledTimes(3)
    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/15', {
      method: 'PUT',
      body: JSON.stringify({ can_block_users: false }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/15', {
      method: 'PUT',
      body: JSON.stringify({ max_blocked_users: 25 }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/users/15/sessions/terminate-all', {
      method: 'POST',
    })
    expect(user.can_block_users).toBe(false)
    expect(user.max_blocked_users).toBe(25)
  })

  it('lets admins toggle bot access, edit role, and delete the user from the settings flow', async () => {
    const user = makeUser({ id: 18 })

    apiFetchMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        has_bot_access: false,
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        has_bot_access: false,
        role: 'پلیس',
      }))
      .mockResolvedValueOnce({ ok: true })

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.settings-btn').trigger('click')
    await findButtonByText(wrapper, 'تغییر دسترسی بات').trigger('click')
    await flushPromises()

    await findButtonByText(wrapper, 'ویرایش نقش').trigger('click')
    await wrapper.get('.form-select').setValue('پلیس')
    await wrapper.get('.edit-section .save-btn').trigger('click')
    await flushPromises()

    await findButtonByText(wrapper, 'بازگشت').trigger('click')
    await wrapper.get('.delete-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/18', {
      method: 'PUT',
      body: JSON.stringify({ has_bot_access: false }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/18', {
      method: 'PUT',
      body: JSON.stringify({ role: 'پلیس' }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/users/18', {
      method: 'DELETE',
    })
    expect(user.has_bot_access).toBe(false)
    expect(user.role).toBe('پلیس')
    expect(wrapper.emitted('navigate')).toContainEqual(['manage_users'])
  })

  it('lets admins apply and remove limitations, then block and unblock the user', async () => {
    const user = makeUser({ id: 21 })

    apiFetchMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 5,
        max_active_commodities: 6,
        max_daily_requests: 7,
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 5,
        max_active_commodities: 6,
        max_daily_requests: 7,
        trading_restricted_until: '2030-01-01T00:00:00Z',
        trading_restricted_until_jalali: '۱۴۰۸/۱۰/۱۱',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 5,
        max_active_commodities: 6,
        max_daily_requests: 7,
        trading_restricted_until: null,
        trading_restricted_until_jalali: null,
      }))
      .mockResolvedValueOnce(makeResponse(makeUser({ id: 21 })))

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.settings-btn').trigger('click')
    await findButtonByText(wrapper, 'اعمال محدودیت').trigger('click')

    const limitInputs = wrapper.findAll('.modal-content .form-input')
    expect(limitInputs[0]).toBeTruthy()
    await limitInputs[0]!.setValue('5')
    await limitInputs[1]!.setValue('6')
    await limitInputs[2]!.setValue('7')
    const saveButtons = wrapper.findAll('.modal-content .save-btn')
    expect(saveButtons[0]).toBeTruthy()
    await saveButtons[0]!.trigger('click')
    await flushPromises()

    await wrapper.get('.block-btn').trigger('click')
    await findButtonByText(wrapper, '1 ساعت').trigger('click')
    await flushPromises()

    await wrapper.get('.unblock-btn').trigger('click')
    await flushPromises()

    await wrapper.get('.unlimit-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/21', {
      method: 'PUT',
      body: JSON.stringify({
        max_daily_trades: 5,
        max_active_commodities: 6,
        max_daily_requests: 7,
        limitations_expire_at: null,
      }),
    })

    const blockCall = apiFetchMock.mock.calls[1]
    expect(blockCall?.[0]).toBe('/api/users/21')
    expect(blockCall?.[1]?.method).toBe('PUT')
    expect(JSON.parse(blockCall?.[1]?.body as string)).toEqual({
      trading_restricted_until: expect.any(String),
    })

    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/users/21', {
      method: 'PUT',
      body: JSON.stringify({ trading_restricted_until: null }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(4, '/api/users/21', {
      method: 'PUT',
      body: JSON.stringify({
        max_daily_trades: null,
        max_active_commodities: null,
        max_daily_requests: null,
        limitations_expire_at: null,
      }),
    })
  })

  it('emits notifications and settings navigation for non-admin profiles', async () => {
    const user = makeUser({ role: 'تماشا' })

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: false,
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.notification-btn').trigger('click')
    await wrapper.get('.menu-button.settings-btn').trigger('click')

    expect(wrapper.emitted('navigate')).toEqual([
      ['notifications'],
      ['user_settings'],
    ])
  })

  it('shows the admin session security warning and restores max session value after a save failure', async () => {
    const user = makeUser({ id: 30, max_sessions: 2 })
    apiFetchMock.mockResolvedValueOnce({ ok: false })

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.form-select-sm').setValue('3')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/users/30', {
      method: 'PUT',
      body: JSON.stringify({ max_sessions: 3 }),
    })
    expect(window.alert).toHaveBeenCalledWith('خطا در ذخیره تنظیمات نشست')
    expect((wrapper.get('.form-select-sm').element as HTMLSelectElement).value).toBe('2')

    const securedWrapper = mount(UserProfile, {
      props: {
        user: makeUser({ id: 31, role: 'مدیر ارشد', max_sessions: 1 }),
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await securedWrapper.find('.sessions-config-box .inline-edit').trigger('click')
    expect(window.alert).toHaveBeenCalledWith('به دلایل امنیتی، تعداد نشست‌های مجاز برای مدیران سایت نمی‌تواند بیش از ۱ باشد.')
  })

  it('requires explicit custom dates before saving manual limitations or manual blocks', async () => {
    const user = makeUser({ id: 32 })

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await wrapper.get('.settings-btn').trigger('click')
    await findButtonByText(wrapper, 'اعمال محدودیت').trigger('click')
    await wrapper.findAll('.modal-content .form-select')[0]!.setValue('-1')
    await wrapper.findAll('.modal-content .save-btn')[0]!.trigger('click')
    expect(window.alert).toHaveBeenCalledWith('لطفاً تاریخ پایان محدودیت را انتخاب کنید.')
    expect(apiFetchMock).not.toHaveBeenCalled()

    await wrapper.findAll('.modal-content .cancel-btn')[0]!.trigger('click')
    await wrapper.get('.block-btn').trigger('click')
    await findButtonByText(wrapper, 'انتخاب زمان دلخواه').trigger('click')
    await findButtonByText(wrapper, 'تایید نهایی').trigger('click')

    expect(window.alert).toHaveBeenCalledWith('لطفاً یک تاریخ معتبر انتخاب کنید.')
    expect(apiFetchMock).not.toHaveBeenCalled()
  })

  it('normalizes and rolls back owner limits when max-accountant or max-block save calls fail', async () => {
    const user = makeUser({ id: 33, max_accountants: 4, max_blocked_users: 12 })
    apiFetchMock
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: false })

    const UserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(UserProfile, {
      props: {
        user,
        isAdminView: true,
        jwtToken: 'token',
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    const maxAccountantsInput = wrapper.get('.max-accountants-input')
    await maxAccountantsInput.setValue('-5')
    await maxAccountantsInput.trigger('change')
    await flushPromises()

    const maxBlockedInput = wrapper.get('.max-blocked-users-input')
    await maxBlockedInput.setValue('500')
    await flushPromises()

    expect(apiFetchMock.mock.calls).toContainEqual([
      '/api/users/33',
      {
        method: 'PUT',
        body: JSON.stringify({ max_accountants: 0 }),
      },
    ])
    expect(apiFetchMock.mock.calls).toContainEqual([
      '/api/users/33',
      {
        method: 'PUT',
        body: JSON.stringify({ max_blocked_users: 100 }),
      },
    ])
    expect(window.alert).toHaveBeenCalledWith('خطا در ذخیره سقف حسابداران')
    expect(window.alert).toHaveBeenCalledWith('خطا در ذخیره سقف بلاک')
    expect((wrapper.get('.max-accountants-input').element as HTMLInputElement).value).toBe('4')
    expect((wrapper.get('.max-blocked-users-input').element as HTMLInputElement).value).toBe('12')
  })
})