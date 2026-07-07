import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import JalaliDatePicker from './JalaliDatePicker.vue'

const apiFetchMock = vi.fn()
const userProfileTimingMocks = vi.hoisted(() => ({
  parseJalaliToIranISOMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

vi.mock('../composables/useUserProfileTiming', () => ({
  useUserProfileTiming: () => ({
    countdownRestriction: null,
    countdownLimitation: null,
    parseJalaliToIranISO: userProfileTimingMocks.parseJalaliToIranISOMock,
    toEnglishDigits: (value: string) => value,
  }),
}))

vi.mock('lucide-vue-next', () => ({
  AlertTriangle: { template: '<span />' },
  ArrowRight: { template: '<span />' },
  Ban: { template: '<span />' },
  Bell: { template: '<span />' },
  CalendarDays: { template: '<span />' },
  Check: { template: '<span />' },
  ChevronLeft: { template: '<span />' },
  ChevronRight: { template: '<span />' },
  Clock: { template: '<span />' },
  Info: { template: '<span />' },
  Pencil: { template: '<span />' },
  RotateCcw: { template: '<span />' },
  Settings: { template: '<span />' },
  Trash2: { template: '<span />' },
  Undo2: { template: '<span />' },
  Users: { template: '<span />' },
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
    account_status: 'active',
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
    max_customers: 5,
    ...overrides,
  }
}

async function setOpenJalaliDate(wrapper: ReturnType<typeof mount>, value: string) {
  const picker = wrapper.findComponent(JalaliDatePicker)
  expect(picker.exists()).toBe(true)
  picker.vm.$emit('update:modelValue', value)
  picker.vm.$emit('change', value)
  await nextTick()
}

function findButtonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  if (!button) throw new Error(`Button not found: ${text}`)
  return button
}

describe('UserProfile.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    userProfileTimingMocks.parseJalaliToIranISOMock.mockReset()
    localStorage.clear()
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    vi.spyOn(window, 'confirm').mockImplementation(() => true)
  })

  it('allows admin to update max accountants and max customers for an owner', async () => {
    const user = makeUser()

    apiFetchMock.mockResolvedValueOnce(makeResponse({
      ...user,
      max_accountants: 6,
    }))
    apiFetchMock.mockResolvedValueOnce(makeResponse({
      ...user,
      max_accountants: 6,
      max_customers: 8,
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

    const maxCustomersInput = wrapper.get('.max-customers-input')
    await maxCustomersInput.setValue('8')
    await maxCustomersInput.trigger('change')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/12', {
      method: 'PUT',
      body: JSON.stringify({ max_accountants: 6 }),
    })
    expect(apiFetchMock.mock.calls).toContainEqual(['/api/users/12', {
      method: 'PUT',
      body: JSON.stringify({ max_customers: 8 }),
    }])
    expect(user.max_accountants).toBe(6)
    expect(user.max_customers).toBe(8)
  })

  it('renders read-only customer context for customer targets in admin view', async () => {
    const user = makeUser({
      is_customer: true,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
    })

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

    expect(wrapper.text()).toContain('اطلاعات مشتری')
    expect(wrapper.text()).toContain('مشتری ویژه')
    expect(wrapper.text()).toContain('owner20')
    expect(wrapper.text()).toContain('سطح 2')
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

  it('lets admins toggle account status, edit role, and delete the user from the settings flow', async () => {
    const user = makeUser({
      id: 18,
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: null,
    })

    apiFetchMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        account_status: 'inactive',
        global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
        global_web_locked_at: null,
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        account_status: 'inactive',
        role: 'پلیس',
        global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
        global_web_locked_at: null,
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
  await findButtonByText(wrapper, 'تغییر وضعیت حساب').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('در مهلت فعال‌سازی')
    expect(wrapper.text()).toContain('پایان مهلت فعال‌سازی')

    await findButtonByText(wrapper, 'ویرایش نقش').trigger('click')
    expect(wrapper.get('.form-select').classes()).toContain('ui-select')
    await wrapper.get('.form-select').setValue('پلیس')
    await wrapper.get('.edit-section .save-btn').trigger('click')
    await flushPromises()

    await findButtonByText(wrapper, 'بازگشت').trigger('click')
    await wrapper.get('.delete-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/18', {
      method: 'PUT',
      body: JSON.stringify({ account_status: 'inactive' }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/18', {
      method: 'PUT',
      body: JSON.stringify({ role: 'پلیس' }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/users/18', {
      method: 'DELETE',
    })
    expect(user.account_status).toBe('inactive')
    expect(user.role).toBe('پلیس')
    expect(wrapper.emitted('navigate')).toContainEqual(['manage_users'])
  })

  it('renders the stronger account-status detail when the account is globally locked', async () => {
    const user = makeUser({
      id: 19,
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-05-20T12:00:00Z',
      global_web_locked_at: '2026-05-21T08:00:00Z',
    })

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

    expect(wrapper.text()).toContain('غیرفعال (قفل کامل)')
    expect(wrapper.text()).toContain('قفل سراسری وب/پیام‌رسان')
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
    expect(wrapper.get('.modal-content').classes()).toContain('ui-responsive-dialog')
    expect(wrapper.get('.modal-overlay').classes()).toContain('ui-responsive-dialog-backdrop')

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
    await wrapper.get('.profile-control.settings-btn').trigger('click')

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

    expect(wrapper.get('.form-select-sm').classes()).toContain('ui-select')
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

  it('hides role editing for middle managers in admin view', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    const user = makeUser({ id: 31 })

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

    expect(wrapper.text()).not.toContain('ویرایش نقش')
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

  it('covers custom date picker helpers and custom block/limit save branches', async () => {
    const user = makeUser({ id: 34 })
    userProfileTimingMocks.parseJalaliToIranISOMock
      .mockReturnValueOnce(null)
      .mockReturnValueOnce('2031-03-21T08:15:00.000Z')
      .mockReturnValueOnce(null)
      .mockReturnValueOnce('2031-03-21T08:15:00.000Z')
    apiFetchMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        trading_restricted_until: '2031-03-21T08:15:00.000Z',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 2,
        max_active_commodities: 3,
        max_daily_requests: 4,
        limitations_expire_at: '2031-03-21T08:15:00.000Z',
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

    const vm = wrapper.vm as unknown as {
      customDate: string
      customLimitDate: string
      limitDurationMinutes: number
      limitMaxTrades: number
      limitMaxCommodities: number
      limitMaxRequests: number
      pickerStep: number
      tempDatePart: string
      tempTimePart: string
      showBlockDateModal: boolean
      showLimitDateModal: boolean
      blockTimePickerRef: { modelValue?: unknown } | null
      limitTimePickerRef: { modelValue?: unknown } | null
      initDatePicker: (currentValue: string) => void
      onDateChange: (value: unknown) => void
      handleNextStep: () => void
      handleFinalSubmit: () => void
      updateDatePart: (value: unknown) => void
      updateTimePart: (value: unknown) => void
      blockUser: (minutes: number) => Promise<void>
      blockUserCustom: () => Promise<void>
      saveLimitations: () => Promise<void>
    }

    vm.initDatePicker('invalid-date')
    expect(vm.tempDatePart).toBeTruthy()
    expect(vm.tempTimePart).toBeTruthy()

    vm.onDateChange('1409/01/01')
    vm.tempDatePart = ''
    vm.handleNextStep()
    expect(vm.pickerStep).toBe(1)
    vm.tempDatePart = '1409/01/01'
    vm.handleNextStep()
    expect(vm.pickerStep).toBe(2)

    vm.updateDatePart('1409/01/02')
    vm.updateTimePart('08:15')
    expect(vm.tempTimePart).toBe('08:15')
    vm.updateTimePart('8:15 AM')
    expect(vm.tempTimePart).toBe('08:15')
    vm.updateTimePart(new Date('2031-03-21T12:45:00Z'))
    expect(vm.tempTimePart).toBe('12:45')
    vm.updateTimePart(915)
    expect(vm.tempTimePart).toBe('915')

    vm.showBlockDateModal = true
    vm.tempDatePart = ''
    vm.handleFinalSubmit()
    expect(window.alert).toHaveBeenCalledWith('لطفاً تاریخ را انتخاب کنید.')

    vm.tempDatePart = '1409/01/01'
    vm.tempTimePart = ''
    vm.blockTimePickerRef = { modelValue: '08:15' }
    vm.handleFinalSubmit()
    expect(vm.customDate).toBe('1409/01/01 08:15')
    expect(vm.showBlockDateModal).toBe(false)

    await vm.blockUser(0)
    await flushPromises()
    expect(apiFetchMock).toHaveBeenCalledWith('/api/users/34', expect.objectContaining({ method: 'PUT' }))

    vm.customDate = '1409/01/01 08:15'
    await vm.blockUserCustom()
    expect(window.alert).toHaveBeenCalledWith('تاریخ نامعتبر است.')

    vm.customDate = '1409/01/01 08:15'
    await vm.blockUserCustom()
    await flushPromises()
    expect(userProfileTimingMocks.parseJalaliToIranISOMock).toHaveBeenCalledWith('1409/01/01 08:15')

    vm.limitDurationMinutes = -1
    vm.limitMaxTrades = 2
    vm.limitMaxCommodities = 3
    vm.limitMaxRequests = 4
    vm.customLimitDate = '1409/01/01 08:15'
    await vm.saveLimitations()
    expect(window.alert).toHaveBeenCalledWith('تاریخ نامعتبر است.')

    vm.showLimitDateModal = true
    vm.tempDatePart = '1409/01/01'
    vm.tempTimePart = ''
    vm.limitTimePickerRef = { modelValue: new Date('2031-03-21T08:15:00Z') }
    vm.handleFinalSubmit()
    expect(vm.customLimitDate).toBe('1409/01/01 08:15')
    expect(vm.showLimitDateModal).toBe(false)

    await vm.saveLimitations()
    await flushPromises()
    expect(apiFetchMock.mock.calls).toContainEqual([
      '/api/users/34',
      {
        method: 'PUT',
        body: JSON.stringify({
          max_daily_trades: 2,
          max_active_commodities: 3,
          max_daily_requests: 4,
          limitations_expire_at: '2031-03-21T08:15:00.000Z',
        }),
      },
    ])
  })

  it('drives the custom block and limitation date modals through their UI controls', async () => {
    const user = makeUser({ id: 35 })
    userProfileTimingMocks.parseJalaliToIranISOMock
      .mockReturnValueOnce('2031-03-21T09:30:00.000Z')
      .mockReturnValueOnce('2031-03-22T10:45:00.000Z')
    apiFetchMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        trading_restricted_until: '2031-03-21T09:30:00.000Z',
        trading_restricted_until_jalali: '۱۴۰۹/۰۱/۰۱ ۰۹:۳۰',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 7,
        max_active_commodities: 8,
        max_daily_requests: 9,
        limitations_expire_at: '2031-03-22T10:45:00.000Z',
        limitations_expire_at_jalali: '۱۴۰۹/۰۱/۰۲ ۱۰:۴۵',
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

    await wrapper.get('.settings-btn').trigger('click')
    await wrapper.get('.block-btn').trigger('click')
    expect(wrapper.get('.modal-content').classes()).toContain('ui-responsive-dialog')
    expect(wrapper.get('.modal-overlay').classes()).toContain('ui-responsive-dialog-backdrop')
    await wrapper.get('.modal-content .cancel-btn.full-width').trigger('click')

    await wrapper.get('.block-btn').trigger('click')
    await findButtonByText(wrapper, 'انتخاب زمان دلخواه').trigger('click')
    await wrapper.get('.custom-date-section .cancel-btn').trigger('click')

    await findButtonByText(wrapper, 'انتخاب زمان دلخواه').trigger('click')
    await wrapper.get('.custom-date-trigger').trigger('click')
    expect(wrapper.get('.date-modal-content').classes()).toContain('ui-responsive-dialog')
    expect(wrapper.get('.date-modal-overlay').classes()).toContain('ui-responsive-dialog-backdrop')
    expect(wrapper.find('.date-modal-content .ui-responsive-dialog__header button').exists()).toBe(false)
    await wrapper.get('.date-modal-content .integrated-cancel-btn').trigger('click')

    await wrapper.get('.custom-date-trigger').trigger('click')
    await setOpenJalaliDate(wrapper, '1409/02/03')
    await wrapper.get('.date-modal-content .integrated-save-btn').trigger('click')
    await wrapper.get('.date-modal-content .time-input').setValue('09:30')
    await wrapper.get('.date-modal-content .integrated-save-btn').trigger('click')
    await flushPromises()

    expect(wrapper.get('.custom-date-trigger').text()).toContain('1409/02/03 09:30')

    await wrapper.get('.custom-date-section .save-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/35', {
      method: 'PUT',
      body: JSON.stringify({ trading_restricted_until: '2031-03-21T09:30:00.000Z' }),
    })

    await findButtonByText(wrapper, 'اعمال محدودیت').trigger('click')
    const limitInputs = wrapper.findAll('.modal-content .form-input')
    await limitInputs[0]!.setValue('7')
    await limitInputs[1]!.setValue('8')
    await limitInputs[2]!.setValue('9')
    expect(wrapper.get('.modal-content .form-select').classes()).toContain('ui-select')
    await wrapper.get('.modal-content .form-select').setValue('-1')

    await wrapper.get('.modal-content .custom-date-trigger').trigger('click')
    expect(wrapper.get('.date-modal-content').classes()).toContain('ui-responsive-dialog')
    expect(wrapper.get('.date-modal-overlay').classes()).toContain('ui-responsive-dialog-backdrop')
    expect(wrapper.find('.date-modal-content .ui-responsive-dialog__header button').exists()).toBe(false)
    await wrapper.get('.date-modal-content .integrated-cancel-btn').trigger('click')
    await wrapper.get('.modal-content .custom-date-trigger').trigger('click')
    await setOpenJalaliDate(wrapper, '1409/02/03')
    await wrapper.get('.date-modal-content .integrated-save-btn').trigger('click')
    await wrapper.get('.date-modal-content .time-input').setValue('10:45')
    await wrapper.get('.date-modal-content .integrated-save-btn').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.modal-content .custom-date-trigger')[0]!.text()).toContain('1409/02/03 10:45')

    await wrapper.get('.modal-content .save-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/35', {
      method: 'PUT',
      body: JSON.stringify({
        max_daily_trades: 7,
        max_active_commodities: 8,
        max_daily_requests: 9,
        limitations_expire_at: '2031-03-22T10:45:00.000Z',
      }),
    })
    expect(userProfileTimingMocks.parseJalaliToIranISOMock).toHaveBeenCalledWith('1409/02/03 09:30')
    expect(userProfileTimingMocks.parseJalaliToIranISOMock).toHaveBeenCalledWith('1409/02/03 10:45')
  })

  it('syncs prop-backed admin refs, uses helper fallbacks, and restores body overflow on unmount', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2031-03-21T06:07:00Z'))

    const user = makeUser({
      id: 37,
      max_sessions: 2,
      max_accountants: 3,
      max_customers: 4,
      can_block_users: true,
      max_blocked_users: 9,
      account_status: 'active',
    })

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

    const vm = wrapper.vm as unknown as {
      editMaxSessions: number
      editMaxCustomers: number
      canBlockUsers: boolean
      editMaxBlockedUsers: number
      accountStatus: string
      showBlockModal: boolean
      showBlockDateModal: boolean
      tempDatePart: string
      tempTimePart: string
      blockTimePickerRef: { modelValue?: unknown } | null
      customDate: string
      handleFinalSubmit: () => void
      getCustomerTierLabel: (value: string | null | undefined) => string
    }

    await wrapper.setProps({
      user: makeUser({
        id: 37,
        max_sessions: 8,
        max_accountants: 3,
        max_customers: 11,
        can_block_users: false,
        max_blocked_users: 15,
        account_status: 'inactive',
      }),
    })
    await flushPromises()

    expect(vm.editMaxSessions).toBe(8)
    expect(vm.editMaxCustomers).toBe(11)
    expect(vm.canBlockUsers).toBe(false)
    expect(vm.editMaxBlockedUsers).toBe(15)
    expect(vm.accountStatus).toBe('inactive')
    expect(vm.getCustomerTierLabel('tier1')).toBe('سطح 1')
    expect(vm.getCustomerTierLabel('unknown-tier')).toBe('---')
    expect(wrapper.text()).toContain('غیرفعال')
    expect(wrapper.text()).toContain('این حساب از بازار خارج شده و تا فعال‌سازی مجدد، دسترسی معاملاتی ندارد.')

    vm.showBlockModal = true
    await nextTick()
    expect(document.body.style.overflow).toBe('hidden')

    vm.showBlockDateModal = true
    vm.tempDatePart = '1409/01/09'
    vm.tempTimePart = ''
    vm.blockTimePickerRef = null
    vm.handleFinalSubmit()
    expect(vm.customDate).toMatch(/^1409\/01\/09 \d{2}:\d{2}$/)

    wrapper.unmount()
    expect(document.body.style.overflow).toBe('')
    vi.useRealTimers()
  })

  it('covers session and customer save helpers plus zero-session and delete-failure branches', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = makeUser({
      id: 38,
      max_sessions: 2,
      max_customers: 5,
      can_block_users: true,
    })

    apiFetchMock
      .mockResolvedValueOnce(makeResponse({ ...user, max_sessions: 9 }))
      .mockResolvedValueOnce(makeResponse({ ...user, max_customers: 0 }))
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce(makeResponse({ terminated_sessions: 0 }))
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

    const vm = wrapper.vm as unknown as {
      editMaxCustomers: number
      saveMaxCustomers: () => Promise<void>
      toggleBlockCapability: () => Promise<void>
      terminateAllSessions: () => Promise<void>
      deleteUser: () => Promise<void>
    }

    await wrapper.get('.sessions-config-box .form-select-sm').setValue('3')
    vm.editMaxCustomers = Number.NaN
    await vm.saveMaxCustomers()
    await vm.toggleBlockCapability()
    await vm.terminateAllSessions()
    await vm.deleteUser()
    await flushPromises()

    expect(apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/38', {
      method: 'PUT',
      body: JSON.stringify({ max_sessions: 3 }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/38', {
      method: 'PUT',
      body: JSON.stringify({ max_customers: 0 }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(3, '/api/users/38', {
      method: 'PUT',
      body: JSON.stringify({ can_block_users: false }),
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(4, '/api/users/38/sessions/terminate-all', {
      method: 'POST',
    })
    expect(apiFetchMock).toHaveBeenNthCalledWith(5, '/api/users/38', {
      method: 'DELETE',
    })
    expect(vm.editMaxCustomers).toBe(0)
    expect(window.alert).toHaveBeenCalledWith('خطا در ذخیره مجوز بلاک')
    expect(window.alert).toHaveBeenCalledWith('نشست فعالی برای پایان دادن وجود نداشت.')
    expect(window.alert).toHaveBeenCalledWith('خطا در حذف کاربر')
    expect(confirmMock).toHaveBeenCalledWith('آیا از پایان دادن فوری به همه نشست‌های فعال این کاربر اطمینان دارید؟')
    expect(confirmMock).toHaveBeenCalledWith('آیا از حذف این کاربر اطمینان دارید؟')
  })

  it('keeps role editing open on save failure, supports admin back actions, and hides settings for ordinary users', async () => {
    const confirmMock = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = makeUser({ id: 36, role: 'عادی' })
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

    await wrapper.get('.profile-nav-back').trigger('click')
    await wrapper.get('.main-actions .back-btn').trigger('click')
    await wrapper.get('.settings-btn').trigger('click')
    await findButtonByText(wrapper, 'تغییر وضعیت حساب').trigger('click')
    await findButtonByText(wrapper, 'ویرایش نقش').trigger('click')
    await wrapper.get('.edit-section .save-btn').trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledWith('آیا از غیرفعال کردن حساب اطمینان دارید؟')
    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    expect(window.alert).toHaveBeenCalledWith('خطا در ذخیره تغییرات')
    expect(wrapper.find('.edit-section').exists()).toBe(true)

    await wrapper.get('.edit-section .cancel-btn').trigger('click')
    expect(wrapper.find('.edit-section').exists()).toBe(false)
    expect(wrapper.emitted('navigate')).toEqual([
      ['manage_users'],
      ['manage_users'],
    ])

    const nonAdminWrapper = mount(UserProfile, {
      props: {
        user: makeUser({ role: 'عادی' }),
        isAdminView: false,
      },
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    expect(nonAdminWrapper.find('.notification-btn').exists()).toBe(true)
    expect(nonAdminWrapper.find('.profile-control.settings-btn').exists()).toBe(false)
  })
})
