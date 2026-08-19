import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineComponent, h, nextTick, ref, Transition } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import UserProfile from './UserProfile.vue'

const { routeRequestMock } = vi.hoisted(() => ({
  routeRequestMock: vi.fn(),
}))
const userProfileSource = readFileSync(
  resolve(process.cwd(), 'src/components/UserProfile.vue'),
  'utf8',
)
const userProfileTimingMocks = vi.hoisted(() => ({
  parseJalaliToIranISOMock: vi.fn(),
}))

vi.mock('../utils/routeRequest', () => ({
  routeRequest: routeRequestMock,
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

function makeResponse(payload: unknown, options: { ok?: boolean; status?: number; rejectJson?: boolean } = {}) {
  const ok = options.ok ?? true
  return {
    ok,
    status: options.status ?? (ok ? 200 : 500),
    statusText: ok ? 'OK' : 'Server Error',
    headers: { get: () => 'application/json' },
    json: options.rejectJson
      ? async () => { throw new SyntaxError('invalid json') }
      : async () => payload,
  } as unknown as Response
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function makeUser(overrides: Record<string, unknown> = {}) {
  return {
    id: 12,
    telegram_id: null,
    username: null,
    full_name: 'Owner Twelve',
    account_name: 'owner12',
    mobile_number: '09120000000',
    address: 'بازار تهران، پلاک ۱۲',
    role: 'عادی',
    account_status: 'active',
    deactivated_at: null,
    created_at: '2026-01-10T08:30:00Z',
    has_bot_access: true,
    is_deleted: false,
    global_lock_grace_expires_at: null,
    global_web_locked_at: null,
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

async function mountProfile(user = makeUser(), props: Record<string, unknown> = {}) {
  const UserProfile = (await import('./UserProfile.vue')).default
  return mount(UserProfile, {
    props: {
      user,
      isAdminView: true,
      jwtToken: 'token',
      ...props,
    },
    global: {
      stubs: { teleport: true },
    },
  })
}

function findButtonByText(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text().includes(text))
  if (!button) throw new Error(`Button not found: ${text}`)
  return button
}

async function openSettings(wrapper: VueWrapper) {
  await wrapper.get('.settings-btn').trigger('click')
}

async function confirmDialog(wrapper: VueWrapper) {
  const dialog = wrapper.get('.ui-confirm-dialog')
  const button = dialog.findAll('button').at(-1)
  if (!button) throw new Error('Confirmation button not found')
  await button.trigger('click')
}

async function cancelDialog(wrapper: VueWrapper) {
  const dialog = wrapper.get('.ui-confirm-dialog')
  const button = dialog.findAll('button').at(0)
  if (!button) throw new Error('Cancel button not found')
  await button.trigger('click')
}

function expectDialogKeepsSafeCopy(wrapper: VueWrapper, rawDetail: string) {
  const dialog = wrapper.get('.ui-confirm-dialog')
  expect(dialog.exists()).toBe(true)
  expect(dialog.text()).toContain('تأیید نشد')
  expect(dialog.text()).not.toContain(rawDetail)
  expect(dialog.text()).not.toContain('owner12')
  expect(dialog.text()).not.toContain('09120000000')
}

function expectMutationCall(
  callNumber: number,
  url: string,
  method: 'PUT' | 'POST' | 'DELETE',
  body: Record<string, unknown> | undefined,
  fallbackMessage: string,
) {
  expect(routeRequestMock).toHaveBeenNthCalledWith(callNumber, url, {
    method,
    ...(body ? { body: JSON.stringify(body) } : {}),
    errorContext: {
      surface: 'admin',
      scope: 'action',
      operation: method === 'DELETE' ? 'delete' : 'update',
      userInitiated: true,
      fallbackMessage,
    },
  })
}

describe('UserProfile.vue authoritative admin actions', () => {
  beforeEach(() => {
    routeRequestMock.mockReset()
    userProfileTimingMocks.parseJalaliToIranISOMock.mockReset()
    localStorage.clear()
  })

  it('preserves ordinary profile-control motion while disabling UserProfile action transitions for reduced motion', () => {
    const profileControlRule = userProfileSource.match(/\.profile-control\s*\{([\s\S]*?)\n\}/)?.[1]
    const reducedMotionRule = userProfileSource.match(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/,
    )?.[1]

    expect(profileControlRule).toContain('transition: all 0.2s;')
    expect(reducedMotionRule).toBeDefined()
    for (const selector of [
      '.form-select',
      '.form-input',
      '.save-btn',
      '.cancel-btn',
      '.profile-control',
      '.duration-btn',
      '.custom-date-trigger',
    ]) {
      expect(reducedMotionRule).toContain(selector)
    }
    expect(reducedMotionRule).toContain('transition: none;')
  })

  it('keeps admin-profile typography locally aligned with the Figma Persian card scale', () => {
    expect(userProfileSource).toMatch(/<div class="card admin-user-profile">/)
    expect(userProfileSource).toMatch(
      /\.admin-user-profile\s*\{[\s\S]*?font-family:\s*Vazirmatn,\s*Tahoma,\s*Arial,\s*sans-serif;[\s\S]*?font-synthesis:\s*none;/,
    )
    expect(userProfileSource).toMatch(
      /\.value\.code\s*\{[\s\S]*?direction:\s*ltr;[\s\S]*?font-family:\s*var\(--ds-font-mono\);/,
    )
    expect(userProfileSource).toMatch(
      /\.usage-ratio\s*\{[\s\S]*?font-family:\s*'Vazirmatn',\s*tahoma,\s*sans-serif;[\s\S]*?direction:\s*ltr;/,
    )
    expect(userProfileSource).toMatch(
      /\.countdown-value\s*\{[\s\S]*?font-family:\s*'Vazirmatn',\s*var\(--ds-font-mono\);[\s\S]*?direction:\s*ltr;/,
    )
  })

  it('keeps one element root when rendered by a route transition', async () => {
    const transitionWarning = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const TransitionHost = defineComponent({
      setup() {
        const visible = ref(true)
        return () => h(
          Transition,
          { name: 'profile-route', appear: true },
          {
            default: () => (visible.value
              ? h(UserProfile, {
                user: makeUser(),
                isAdminView: true,
                jwtToken: 'token',
              })
              : null),
          },
        )
      },
    })
    const wrapper = mount(TransitionHost, {
      attachTo: document.body,
      global: {
        stubs: { teleport: true, transition: false },
      },
    })

    try {
      await nextTick()
      const profile = wrapper.get('.admin-user-profile')
      expect(profile.element).toBeInstanceOf(HTMLDivElement)
      expect(transitionWarning.mock.calls).not.toContainEqual(
        expect.arrayContaining([
          expect.stringContaining('non-element root node that cannot be animated'),
        ]),
      )
    } finally {
      wrapper.unmount()
      transitionWarning.mockRestore()
    }
  })

  it('renders admin chrome with shared ui action cards and form primitives', async () => {
    const wrapper = await mountProfile(makeUser())
    expect(wrapper.text()).toContain('بازار تهران، پلاک ۱۲')
    expect(wrapper.get('.settings-btn').classes()).toContain('ui-action-card')
    expect(wrapper.get('.delete-btn').classes()).toContain('ui-action-card')

    await openSettings(wrapper)
    expect(wrapper.get('.block-btn').classes()).toContain('ui-action-card')
    await findButtonByText(wrapper, 'اعمال محدودیت').trigger('click')

    expect(wrapper.get('.modal-content .form-input').classes()).toContain('ui-input')
    expect(wrapper.get('.modal-content .form-select').classes()).toContain('ui-select')
    expect(wrapper.get('.modal-content .save-btn').classes()).toContain('ui-button')
    expect(wrapper.get('.modal-content .cancel-btn').classes()).toContain('ui-button')
    await wrapper.get('.modal-content .form-select').setValue('-1')
    expect(wrapper.get('.custom-date-trigger').classes()).toContain('ui-button')
  })

  it('saves a role once, applies only the returned user, and never opens native dialogs', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true)
    const user = makeUser()
    const pending = deferred<Response>()
    routeRequestMock.mockReturnValueOnce(pending.promise)
    const wrapper = await mountProfile(user)

    await openSettings(wrapper)
    await findButtonByText(wrapper, 'ویرایش نقش').trigger('click')
    await wrapper.get('.edit-section .form-select').setValue('پلیس')

    const vm = wrapper.vm as unknown as { saveRole: () => Promise<unknown> }
    const first = vm.saveRole()
    const duplicate = vm.saveRole()
    expect(routeRequestMock).toHaveBeenCalledTimes(1)

    pending.resolve(makeResponse({ ...user, role: 'پلیس' }))
    await Promise.all([first, duplicate])
    await flushPromises()

    expectMutationCall(1, '/api/users/12', 'PUT', { role: 'پلیس' }, 'ذخیره نقش کاربر ناموفق بود.')
    expect(user.role).toBe('پلیس')
    expect(wrapper.find('.edit-section').exists()).toBe(false)
    expect(wrapper.text()).toContain('نقش کاربر تغییر کرد.')
    expect(alertSpy).not.toHaveBeenCalled()
    expect(confirmSpy).not.toHaveBeenCalled()
  })

  it('keeps the role editor and selected draft after HTTP or invalid-receipt failures', async () => {
    const user = makeUser()
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({}, { ok: false }))
      .mockResolvedValueOnce(makeResponse({ id: user.id, role: 'پلیس' }))
      .mockResolvedValueOnce(makeResponse({ ...user, id: 99, role: 'پلیس' }))
    const wrapper = await mountProfile(user)

    await openSettings(wrapper)
    await findButtonByText(wrapper, 'ویرایش نقش').trigger('click')
    await wrapper.get('.edit-section .form-select').setValue('پلیس')
    await wrapper.get('.edit-section .save-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.edit-section').exists()).toBe(true)
    expect((wrapper.get('.edit-section .form-select').element as HTMLSelectElement).value).toBe('پلیس')
    expect(user.role).toBe('عادی')
    expect(wrapper.get('.edit-section [role="alert"]').text()).toContain('ناموفق')

    await wrapper.get('.edit-section .save-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.edit-section').exists()).toBe(true)
    expect(user.role).toBe('عادی')

    await wrapper.get('.edit-section .save-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.edit-section').exists()).toBe(true)
    expect(user.role).toBe('عادی')
    expect(routeRequestMock).toHaveBeenCalledTimes(3)
  })

  it('serializes different keyed mutations per user and still guards same-key duplicates', async () => {
    const user = makeUser({ max_customers: 5 })
    const roleRequest = deferred<Response>()
    routeRequestMock
      .mockReturnValueOnce(roleRequest.promise)
      .mockResolvedValueOnce(makeResponse({ ...user, role: 'پلیس', max_customers: 9 }))
    const wrapper = await mountProfile(user)
    await openSettings(wrapper)
    await findButtonByText(wrapper, 'ویرایش نقش').trigger('click')
    await wrapper.get('.edit-section .form-select').setValue('پلیس')
    const vm = wrapper.vm as unknown as {
      editMaxCustomers: number
      saveRole: () => Promise<unknown>
      saveMaxCustomers: () => Promise<void>
    }

    const role = vm.saveRole()
    const duplicateRole = vm.saveRole()
    vm.editMaxCustomers = 9
    const racingQuota = vm.saveMaxCustomers()
    expect(routeRequestMock).toHaveBeenCalledTimes(1)
    await nextTick()
    expect(wrapper.get('.max-customers-input').attributes('disabled')).toBeDefined()

    roleRequest.resolve(makeResponse({ ...user, role: 'پلیس' }))
    await Promise.all([role, duplicateRole, racingQuota])
    await flushPromises()
    expect(user.role).toBe('پلیس')
    expect(user.max_customers).toBe(5)

    vm.editMaxCustomers = 9
    await vm.saveMaxCustomers()
    await flushPromises()
    expect(routeRequestMock).toHaveBeenCalledTimes(2)
    expect(user.max_customers).toBe(9)
    expectMutationCall(1, '/api/users/12', 'PUT', { role: 'پلیس' }, 'ذخیره نقش کاربر ناموفق بود.')
    expectMutationCall(2, '/api/users/12', 'PUT', { max_customers: 9 }, 'ذخیره سقف مشتریان ناموفق بود.')
  })

  it('uses an in-context account-status confirmation and retains it until a valid receipt arrives', async () => {
    const user = makeUser()
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({ ...user, account_status: 'inactive' }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        account_status: 'inactive',
        global_lock_grace_expires_at: '2026-08-10T14:30:00Z',
        global_web_locked_at: '2026-08-10T14:31:00Z',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        account_status: 'inactive',
        global_lock_grace_expires_at: '2026-08-10T14:30:00Z',
      }))
    const wrapper = await mountProfile(user)

    await openSettings(wrapper)
    await findButtonByText(wrapper, 'تغییر وضعیت حساب').trigger('click')
    expect(routeRequestMock).not.toHaveBeenCalled()
    expect(wrapper.get('.ui-confirm-dialog').text()).toContain('غیرفعال')
    expect(wrapper.get('.ui-confirm-dialog').text()).toContain('خروج کاربر از کانال تلگرام مورد انتظار است')
    expect(wrapper.get('.ui-confirm-dialog').text()).toContain('مهلت دو روزه')
    expect(wrapper.get('.ui-confirm-dialog').text()).toContain('لغو یا Escape هیچ تغییری ایجاد نمی‌کند')
    expect(wrapper.get('.ui-confirm-dialog').text()).not.toContain('owner12')
    expect(wrapper.get('.ui-confirm-dialog').text()).not.toContain('09120000000')

    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.account_status).toBe('active')
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(true)
    expect(wrapper.get('.ui-confirm-dialog [role="alert"]').text()).toContain('تأیید نشد')

    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.account_status).toBe('active')
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(true)

    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.account_status).toBe('inactive')
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expect(wrapper.text()).toContain('حساب غیرفعال شد. مهلت فعال‌سازی تا')
    expectMutationCall(3, '/api/users/12', 'PUT', { account_status: 'inactive' }, 'تغییر وضعیت حساب ناموفق بود.')
  })

  it('keeps account-status, unblock, and remove-limitations at zero mutation on Escape or cancel', async () => {
    const { AppHttpError } = await import('../utils/httpErrorPolicy')
    const rawStatusDetail = 'raw-status-detail: account=owner12; mobile=09120000000'
    const limitedUser = makeUser({
      max_daily_trades: 5,
      max_active_commodities: 6,
      max_daily_requests: 7,
    })
    const wrapper = await mountProfile(limitedUser)
    await openSettings(wrapper)

    await findButtonByText(wrapper, 'تغییر وضعیت حساب').trigger('click')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expect(routeRequestMock).not.toHaveBeenCalled()
    expect(limitedUser.account_status).toBe('active')

    await findButtonByText(wrapper, 'تغییر وضعیت حساب').trigger('click')
    await cancelDialog(wrapper)
    expect(routeRequestMock).not.toHaveBeenCalled()
    expect(limitedUser.account_status).toBe('active')

    routeRequestMock
      .mockRejectedValueOnce(new AppHttpError({ status: 403, detail: rawStatusDetail }))
      .mockRejectedValueOnce(new AppHttpError({ status: 404, detail: rawStatusDetail }))
    await findButtonByText(wrapper, 'تغییر وضعیت حساب').trigger('click')
    await confirmDialog(wrapper)
    await flushPromises()
    expect(limitedUser.account_status).toBe('active')
    expect(wrapper.get('.ui-confirm-dialog [role="alert"]').text()).toContain('اجازه این اقدام را ندارید')
    expect(wrapper.get('.ui-confirm-dialog').text()).not.toContain(rawStatusDetail)
    expect(wrapper.get('.ui-confirm-dialog').text()).not.toContain('owner12')
    await confirmDialog(wrapper)
    await flushPromises()
    expect(limitedUser.account_status).toBe('active')
    expect(wrapper.get('.ui-confirm-dialog [role="alert"]').text()).toContain('این کاربر دیگر در دسترس نیست')
    expect(wrapper.get('.ui-confirm-dialog').text()).not.toContain(rawStatusDetail)
    await cancelDialog(wrapper)
    wrapper.unmount()

    const blockedUser = makeUser({
      trading_restricted_until: '2031-03-21T08:15:00Z',
      trading_restricted_until_jalali: '۱۴۰۹/۰۱/۰۱',
    })
    const blockedWrapper = await mountProfile(blockedUser)
    await openSettings(blockedWrapper)
    await blockedWrapper.get('.unblock-btn').trigger('click')
    expect(blockedWrapper.get('.ui-confirm-dialog').text()).toContain('لغو یا Escape هیچ تغییری ایجاد نمی‌کند')
    expect(blockedWrapper.get('.ui-confirm-dialog').text()).not.toContain('owner12')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(blockedWrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expect(blockedUser.trading_restricted_until).toBe('2031-03-21T08:15:00Z')
    expect(routeRequestMock).toHaveBeenCalledTimes(2)
    blockedWrapper.unmount()

    const limitsWrapper = await mountProfile(limitedUser)
    await openSettings(limitsWrapper)
    await limitsWrapper.get('.unlimit-btn').trigger('click')
    await cancelDialog(limitsWrapper)
    expect(limitedUser.max_daily_trades).toBe(5)
    expect(routeRequestMock).toHaveBeenCalledTimes(2)
    limitsWrapper.unmount()
  })

  it('accepts activation only after both global-lock fields are authoritatively cleared', async () => {
    const user = makeUser({
      account_status: 'inactive',
      deactivated_at: '2026-08-08T10:00:00Z',
      global_lock_grace_expires_at: '2026-08-10T10:00:00Z',
      global_web_locked_at: '2026-08-11T10:00:00Z',
    })
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        account_status: 'active',
        global_lock_grace_expires_at: '2026-08-10T10:00:00Z',
        global_web_locked_at: null,
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        account_status: 'active',
        global_lock_grace_expires_at: null,
        global_web_locked_at: '2026-08-11T10:00:00Z',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        account_status: 'active',
        deactivated_at: null,
        global_lock_grace_expires_at: null,
        global_web_locked_at: null,
      }))
    const wrapper = await mountProfile(user)

    await openSettings(wrapper)
    await findButtonByText(wrapper, 'تغییر وضعیت حساب').trigger('click')
    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.account_status).toBe('inactive')
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(true)

    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.account_status).toBe('inactive')
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(true)

    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.account_status).toBe('active')
    expect(user.global_lock_grace_expires_at).toBeNull()
    expect(user.global_web_locked_at).toBeNull()
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expectMutationCall(3, '/api/users/12', 'PUT', { account_status: 'active' }, 'تغییر وضعیت حساب ناموفق بود.')
  })

  it('keeps block modal and custom date on network/receipt failure, then closes on the returned user', async () => {
    const user = makeUser()
    userProfileTimingMocks.parseJalaliToIranISOMock.mockReturnValue('2031-03-21T08:15:00.000Z')
    routeRequestMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(makeResponse({ ...user, id: 99, trading_restricted_until: '2031-03-21T08:15:00Z' }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        trading_restricted_until: '2031-03-21T08:15:00Z',
        trading_restricted_until_jalali: '۱۴۰۹/۰۱/۰۱',
      }))
    const wrapper = await mountProfile(user)
    await openSettings(wrapper)
    await wrapper.get('.block-btn').trigger('click')
    await findButtonByText(wrapper, 'انتخاب زمان دلخواه').trigger('click')
    const vm = wrapper.vm as unknown as {
      customDate: string
      showBlockModal: boolean
      blockUserCustom: () => Promise<void>
    }
    vm.customDate = '1409/01/01 08:15'

    await vm.blockUserCustom()
    await flushPromises()
    expect(vm.showBlockModal).toBe(true)
    expect(vm.customDate).toBe('1409/01/01 08:15')
    expect(user.trading_restricted_until).toBeNull()
    expect(wrapper.get('.modal-content [role="alert"]').text()).toBeTruthy()

    await vm.blockUserCustom()
    await flushPromises()
    expect(vm.showBlockModal).toBe(true)
    expect(user.trading_restricted_until).toBeNull()

    await vm.blockUserCustom()
    await flushPromises()
    expect(vm.showBlockModal).toBe(false)
    expect(user.trading_restricted_until).toBe('2031-03-21T08:15:00Z')
  })

  it('preserves limitation drafts on failure and removes limits only through confirmation', async () => {
    const user = makeUser()
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({}, { ok: false }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 5,
        max_active_commodities: 6,
        max_daily_requests: 7,
      }))
      .mockResolvedValueOnce(makeResponse(makeUser({
        max_daily_trades: null,
        max_active_commodities: null,
        max_daily_requests: null,
        limitations_expire_at: null,
      })))
    const wrapper = await mountProfile(user)
    await openSettings(wrapper)
    await findButtonByText(wrapper, 'اعمال محدودیت').trigger('click')
    const inputs = wrapper.findAll('.modal-content .form-input')
    await inputs[0]!.setValue('5')
    await inputs[1]!.setValue('6')
    await inputs[2]!.setValue('7')

    await wrapper.get('.modal-content .save-btn').trigger('click')
    await flushPromises()
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('5')
    expect(wrapper.find('.modal-content').exists()).toBe(true)
    expect(user.max_daily_trades).toBeNull()

    await wrapper.get('.modal-content .save-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.modal-content').exists()).toBe(false)
    expect(user.max_daily_trades).toBe(5)

    await wrapper.get('.unlimit-btn').trigger('click')
    expect(wrapper.get('.ui-confirm-dialog').text()).toContain('رفع محدودیت‌ها')
    expect(wrapper.get('.ui-confirm-dialog').text()).toContain('لغو یا Escape هیچ تغییری ایجاد نمی‌کند')
    expect(wrapper.get('.ui-confirm-dialog').text()).not.toContain('owner12')
    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.max_daily_trades).toBeNull()
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expectMutationCall(3, '/api/users/12', 'PUT', {
      max_daily_trades: null,
      max_active_commodities: null,
      max_daily_requests: null,
      limitations_expire_at: null,
    }, 'رفع محدودیت‌ها ناموفق بود.')
  })

  it('rejects invalid or stale finite limitation expiry receipts before exact-instant success', async () => {
    const user = makeUser()
    const expectedExpiry = '2031-03-21T08:15:00.000Z'
    userProfileTimingMocks.parseJalaliToIranISOMock.mockReturnValue(expectedExpiry)
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 2,
        max_active_commodities: 3,
        max_daily_requests: 4,
        limitations_expire_at: 'invalid-date',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 2,
        max_active_commodities: 3,
        max_daily_requests: 4,
        limitations_expire_at: '2030-03-21T08:15:00Z',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        max_daily_trades: 2,
        max_active_commodities: 3,
        max_daily_requests: 4,
        limitations_expire_at: '2031-03-21T08:15:00',
      }))
    const wrapper = await mountProfile(user)
    await openSettings(wrapper)
    await findButtonByText(wrapper, 'اعمال محدودیت').trigger('click')
    const vm = wrapper.vm as unknown as {
      customLimitDate: string
      limitDurationMinutes: number
      limitMaxTrades: number | null
      limitMaxCommodities: number | null
      limitMaxRequests: number | null
      showLimitationsModal: boolean
      saveLimitations: () => Promise<void>
    }
    vm.customLimitDate = '1410/01/01 11:45'
    vm.limitDurationMinutes = -1
    vm.limitMaxTrades = 2
    vm.limitMaxCommodities = 3
    vm.limitMaxRequests = 4
    const expectedBody = {
      max_daily_trades: 2,
      max_active_commodities: 3,
      max_daily_requests: 4,
      limitations_expire_at: expectedExpiry,
    }

    await vm.saveLimitations()
    await flushPromises()
    expect(vm.showLimitationsModal).toBe(true)
    expect(user.limitations_expire_at).toBeNull()

    await vm.saveLimitations()
    await flushPromises()
    expect(vm.showLimitationsModal).toBe(true)
    expect(user.limitations_expire_at).toBeNull()

    await vm.saveLimitations()
    await flushPromises()
    expect(vm.showLimitationsModal).toBe(false)
    expect(user.limitations_expire_at).toBe('2031-03-21T08:15:00')
    expectMutationCall(1, '/api/users/12', 'PUT', expectedBody, 'ذخیره محدودیت‌ها ناموفق بود.')
    expectMutationCall(2, '/api/users/12', 'PUT', expectedBody, 'ذخیره محدودیت‌ها ناموفق بود.')
    expectMutationCall(3, '/api/users/12', 'PUT', expectedBody, 'ذخیره محدودیت‌ها ناموفق بود.')
  })

  it('preserves session and accountant quota drafts across failures', async () => {
    const user = makeUser({ max_sessions: 2, max_accountants: 4 })
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({}, { ok: false }))
      .mockResolvedValueOnce(makeResponse({}, { ok: false }))
    const wrapper = await mountProfile(user)

    await wrapper.get('.form-select-sm').setValue('3')
    await flushPromises()
    expect((wrapper.get('.form-select-sm').element as HTMLSelectElement).value).toBe('3')
    expect(user.max_sessions).toBe(2)

    const maxAccountants = wrapper.get('.max-accountants-input')
    expect(maxAccountants.attributes('aria-label')).toBe('حداکثر حسابداران مجاز')
    expect(wrapper.get('.max-customers-input').attributes('aria-label')).toBe('حداکثر مشتریان مجاز')
    expect(wrapper.get('.max-blocked-users-input').attributes('aria-label')).toBe(
      'سقف کاربران قابل بلاک',
    )
    await maxAccountants.setValue('-5')
    await maxAccountants.trigger('change')
    await flushPromises()
    expect((maxAccountants.element as HTMLInputElement).value).toBe('0')
    expect(user.max_accountants).toBe(4)
    expect(wrapper.get('.quota-feedback').attributes('role')).toBe('alert')
    expectMutationCall(1, '/api/users/12', 'PUT', { max_sessions: 3 }, 'ذخیره تنظیمات نشست ناموفق بود.')
    expectMutationCall(2, '/api/users/12', 'PUT', { max_accountants: 0 }, 'ذخیره سقف حسابداران ناموفق بود.')
  })

  it('uses the exact max-customer contract and retains the draft until a valid success receipt', async () => {
    const user = makeUser({ max_customers: 5 })
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({}, { ok: false }))
      .mockResolvedValueOnce(makeResponse({ ...user, max_customers: 8 }))
    const wrapper = await mountProfile(user)
    const input = wrapper.get('.max-customers-input')

    await input.setValue('8')
    await input.trigger('change')
    await flushPromises()
    expect(user.max_customers).toBe(5)
    expect((input.element as HTMLInputElement).value).toBe('8')
    expectMutationCall(1, '/api/users/12', 'PUT', { max_customers: 8 }, 'ذخیره سقف مشتریان ناموفق بود.')

    await input.trigger('change')
    await flushPromises()
    expect(user.max_customers).toBe(8)
    expect(wrapper.get('.quota-feedback').attributes('role')).toBe('status')
    expectMutationCall(2, '/api/users/12', 'PUT', { max_customers: 8 }, 'ذخیره سقف مشتریان ناموفق بود.')
  })

  it('uses the exact block-quota contract and rejects mismatched receipts before success', async () => {
    const user = makeUser({ max_blocked_users: 12 })
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({ ...user, max_blocked_users: 12 }))
      .mockResolvedValueOnce(makeResponse({ ...user, max_blocked_users: 25 }))
    const wrapper = await mountProfile(user)
    const input = wrapper.get('.max-blocked-users-input')

    await input.setValue('25')
    await input.trigger('change')
    await flushPromises()
    expect(user.max_blocked_users).toBe(12)
    expect((input.element as HTMLInputElement).value).toBe('25')
    expectMutationCall(1, '/api/users/12', 'PUT', { max_blocked_users: 25 }, 'ذخیره سقف بلاک ناموفق بود.')

    await input.trigger('change')
    await flushPromises()
    expect(user.max_blocked_users).toBe(25)
    expectMutationCall(2, '/api/users/12', 'PUT', { max_blocked_users: 25 }, 'ذخیره سقف بلاک ناموفق بود.')
  })

  it('uses the exact block-capability contract and never infers success from intent', async () => {
    const user = makeUser({ can_block_users: true })
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({ ...user, can_block_users: true }))
      .mockResolvedValueOnce(makeResponse({ ...user, can_block_users: false }))
    const wrapper = await mountProfile(user)

    await wrapper.get('.toggle-block-capability-btn').trigger('click')
    await flushPromises()
    expect(user.can_block_users).toBe(true)
    expect(wrapper.get('.toggle-block-capability-btn').text()).toContain('فعال')
    expectMutationCall(1, '/api/users/12', 'PUT', { can_block_users: false }, 'ذخیره مجوز بلاک ناموفق بود.')

    await wrapper.get('.toggle-block-capability-btn').trigger('click')
    await flushPromises()
    expect(user.can_block_users).toBe(false)
    expect(wrapper.get('.toggle-block-capability-btn').text()).toContain('غیرفعال')
    expectMutationCall(2, '/api/users/12', 'PUT', { can_block_users: false }, 'ذخیره مجوز بلاک ناموفق بود.')
  })

  it('uses the exact preset-block payload and keeps the modal open through failure', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2030-01-01T10:00:00.000Z'))
    const user = makeUser()
    const restrictedUntil = '2030-01-01T11:00:00.000Z'
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({
        ...user,
        trading_restricted_until: 'not-a-date',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        trading_restricted_until: '2029-01-01T11:00:00Z',
      }))
      .mockResolvedValueOnce(makeResponse({
        ...user,
        trading_restricted_until: '2030-01-01T11:00:00',
        trading_restricted_until_jalali: '۱۴۰۸/۱۰/۱۱ ۱۴:۳۰',
      }))
    const wrapper = await mountProfile(user)

    try {
      await openSettings(wrapper)
      await wrapper.get('.block-btn').trigger('click')
      await findButtonByText(wrapper, '1 ساعت').trigger('click')
      await flushPromises()
      expect(wrapper.find('.modal-content').exists()).toBe(true)
      expect(user.trading_restricted_until).toBeNull()
      expectMutationCall(1, '/api/users/12', 'PUT', { trading_restricted_until: restrictedUntil }, 'اعمال مسدودیت ناموفق بود.')

      await findButtonByText(wrapper, '1 ساعت').trigger('click')
      await flushPromises()
      expect(wrapper.find('.modal-content').exists()).toBe(true)
      expect(user.trading_restricted_until).toBeNull()
      expectMutationCall(2, '/api/users/12', 'PUT', { trading_restricted_until: restrictedUntil }, 'اعمال مسدودیت ناموفق بود.')

      await findButtonByText(wrapper, '1 ساعت').trigger('click')
      await flushPromises()
      expect(wrapper.find('.modal-content').exists()).toBe(false)
      expect(user.trading_restricted_until).toBe('2030-01-01T11:00:00')
      expectMutationCall(3, '/api/users/12', 'PUT', { trading_restricted_until: restrictedUntil }, 'اعمال مسدودیت ناموفق بود.')
    } finally {
      wrapper.unmount()
      vi.useRealTimers()
    }
  })

  it('validates terminated_sessions and guards duplicate terminate-all submissions', async () => {
    const user = makeUser({ id: 15 })
    routeRequestMock.mockResolvedValueOnce(makeResponse({ detail: 'missing receipt' }))
    const wrapper = await mountProfile(user)
    await wrapper.get('.terminate-sessions-btn').trigger('click')
    await confirmDialog(wrapper)
    await flushPromises()
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(true)
    expect(wrapper.get('.ui-confirm-dialog [role="alert"]').text()).toContain('تأیید نشد')

    const pending = deferred<Response>()
    routeRequestMock.mockReturnValueOnce(pending.promise)
    const vm = wrapper.vm as unknown as { confirmPendingAction: () => Promise<void> }
    const first = vm.confirmPendingAction()
    const duplicate = vm.confirmPendingAction()
    expect(routeRequestMock).toHaveBeenCalledTimes(2)
    pending.resolve(makeResponse({ detail: '3 نشست پایان یافت', terminated_sessions: 3 }))
    await Promise.all([first, duplicate])
    await flushPromises()

    expectMutationCall(
      2,
      '/api/users/15/sessions/terminate-all',
      'POST',
      undefined,
      'پایان دادن به نشست‌های فعال ناموفق بود.',
    )
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expect(wrapper.text()).toContain('3 نشست پایان یافت.')
  })

  it('keeps delete confirmation on HTTP, malformed JSON, and wrong receipt; navigates only after exact receipt', async () => {
    const user = makeUser({ id: 18 })
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({}, { ok: false }))
      .mockResolvedValueOnce(makeResponse(null, { rejectJson: true }))
      .mockResolvedValueOnce(makeResponse({ message: 'deleted maybe' }))
      .mockResolvedValueOnce(makeResponse({ message: 'User deleted successfully' }))
    const wrapper = await mountProfile(user)

    await wrapper.get('.delete-btn').trigger('click')
    const dialogText = wrapper.get('.ui-confirm-dialog').text()
    expect(dialogText).toContain('آفرهای فعال را منقضی')
    expect(dialogText).toContain('دعوت‌های در انتظار را لغو')
    expect(dialogText).toContain('روابط مشتری/حسابدار')
    expect(dialogText).not.toContain('owner12')
    expect(dialogText).not.toContain('Owner Twelve')
    expect(dialogText).not.toContain('09120000000')
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await confirmDialog(wrapper)
      await flushPromises()
      expectDialogKeepsSafeCopy(wrapper, 'deleted maybe')
      expect(wrapper.emitted('navigate')).toBeUndefined()
    }

    await confirmDialog(wrapper)
    await flushPromises()
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expect(wrapper.emitted('navigate')).toEqual([['manage_users']])
    expectMutationCall(4, '/api/users/18', 'DELETE', undefined, 'حذف کاربر ناموفق بود.')
  })

  it('keeps delete and terminate-all confirmation on cancel, Escape, and raw HTTP failures', async () => {
    const { AppHttpError } = await import('../utils/httpErrorPolicy')
    const rawDeleteDetail = 'raw-delete-detail: user=18; email=owner12@example.test'
    const rawTerminateDetail = 'raw-terminate-detail: sessions=chrome-token-xyz'
    const user = makeUser({ id: 18, account_name: 'owner12', mobile_number: '09120000000' })

    const wrapper = await mountProfile(user)
    await wrapper.get('.delete-btn').trigger('click')
    await cancelDialog(wrapper)
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)

    await wrapper.get('.delete-btn').trigger('click')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expect(routeRequestMock).not.toHaveBeenCalled()

    routeRequestMock
      .mockRejectedValueOnce(new AppHttpError({ status: 400, detail: rawDeleteDetail }))
      .mockRejectedValueOnce(new AppHttpError({ status: 403, detail: rawDeleteDetail }))
      .mockRejectedValueOnce(new AppHttpError({ status: 404, detail: rawDeleteDetail }))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    await wrapper.get('.delete-btn').trigger('click')
    for (const expectedCopy of [
      'تأیید نشد',
      'اجازه این اقدام را ندارید',
      'این کاربر دیگر در دسترس نیست',
      'تأیید نشد',
    ]) {
      await confirmDialog(wrapper)
      await flushPromises()
      const dialog = wrapper.get('.ui-confirm-dialog')
      expect(dialog.text()).toContain(expectedCopy)
      expect(dialog.text()).not.toContain(rawDeleteDetail)
      expect(dialog.text()).not.toContain('owner12@example.test')
      expect(wrapper.emitted('navigate')).toBeUndefined()
    }

    await cancelDialog(wrapper)
    routeRequestMock.mockRejectedValueOnce(new AppHttpError({ status: 400, detail: rawTerminateDetail }))
    await wrapper.get('.terminate-sessions-btn').trigger('click')
    await confirmDialog(wrapper)
    await flushPromises()
    expectDialogKeepsSafeCopy(wrapper, rawTerminateDetail)
    expect(wrapper.text()).not.toContain(rawTerminateDetail)
    expect(routeRequestMock).toHaveBeenCalledTimes(5)
  })

  it('confirms unblock and mutates the restriction only from the returned user', async () => {
    const user = makeUser({
      trading_restricted_until: '2031-03-21T08:15:00Z',
      trading_restricted_until_jalali: '۱۴۰۹/۰۱/۰۱',
    })
    routeRequestMock
      .mockResolvedValueOnce(makeResponse({ ...user, trading_restricted_until: '2031-03-21T08:15:00Z' }))
      .mockResolvedValueOnce(makeResponse({ ...user, trading_restricted_until: null, trading_restricted_until_jalali: null }))
    const wrapper = await mountProfile(user)
    await openSettings(wrapper)
    await wrapper.get('.unblock-btn').trigger('click')
    expect(wrapper.get('.ui-confirm-dialog').text()).toContain('لغو یا Escape هیچ تغییری ایجاد نمی‌کند')
    expect(wrapper.get('.ui-confirm-dialog').text()).not.toContain('owner12')

    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.trading_restricted_until).toBe('2031-03-21T08:15:00Z')
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(true)

    await confirmDialog(wrapper)
    await flushPromises()
    expect(user.trading_restricted_until).toBeNull()
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
    expectMutationCall(1, '/api/users/12', 'PUT', { trading_restricted_until: null }, 'رفع مسدودیت ناموفق بود.')
    expectMutationCall(2, '/api/users/12', 'PUT', { trading_restricted_until: null }, 'رفع مسدودیت ناموفق بود.')
  })

  it('shows date validation in the active modal without sending a request', async () => {
    const wrapper = await mountProfile(makeUser())
    await openSettings(wrapper)
    await findButtonByText(wrapper, 'اعمال محدودیت').trigger('click')
    await wrapper.get('.modal-content .form-select').setValue('-1')
    await wrapper.get('.modal-content .save-btn').trigger('click')
    expect(wrapper.get('.modal-content [role="alert"]').text()).toContain('تاریخ پایان محدودیت')

    await wrapper.get('.modal-content .cancel-btn').trigger('click')
    await wrapper.get('.block-btn').trigger('click')
    await findButtonByText(wrapper, 'انتخاب زمان دلخواه').trigger('click')
    await findButtonByText(wrapper, 'تایید نهایی').trigger('click')
    expect(wrapper.get('.modal-content [role="alert"]').text()).toContain('تاریخ معتبر')
    expect(routeRequestMock).not.toHaveBeenCalled()
  })

  it('retains customer context and non-admin navigation behavior', async () => {
    const customer = makeUser({
      is_customer: true,
      customer_owner_account_name: 'owner20',
      customer_management_name: 'مشتری ویژه',
      customer_tier: 'tier2',
    })
    const wrapper = await mountProfile(customer)
    expect(wrapper.text()).toContain('اطلاعات مشتری')
    expect(wrapper.text()).toContain('مشتری ویژه')
    expect(wrapper.text()).toContain('owner20')
    expect(wrapper.text()).toContain('سطح 2')

    const nonAdmin = await mountProfile(makeUser({ role: 'تماشا' }), { isAdminView: false, jwtToken: null })
    await nonAdmin.get('.notification-btn').trigger('click')
    await nonAdmin.get('.profile-control.settings-btn').trigger('click')
    expect(nonAdmin.emitted('navigate')).toEqual([
      ['notifications'],
      ['user_settings'],
    ])

    const ordinary = await mountProfile(makeUser({ role: 'عادی' }), { isAdminView: false, jwtToken: null })
    expect(ordinary.find('.profile-control.settings-btn').exists()).toBe(false)
    expect(ordinary.find('.notification-btn').exists()).toBe(true)
  })

  it('renders the authoritative global-lock detail for an inactive account', async () => {
    const wrapper = await mountProfile(makeUser({
      account_status: 'inactive',
      global_lock_grace_expires_at: '2026-08-09T14:30:00Z',
      global_web_locked_at: '2026-08-10T14:30:00Z',
    }))

    expect(wrapper.text()).toContain('غیرفعال (قفل کامل)')
    expect(wrapper.text()).toContain('قفل سراسری وب/پیام‌رسان')
  })

  it('keeps role editing hidden for a cached middle-manager actor', async () => {
    vi.resetModules()
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))
    const FreshUserProfile = (await import('./UserProfile.vue')).default
    const wrapper = mount(FreshUserProfile, {
      props: {
        user: makeUser(),
        isAdminView: true,
        jwtToken: 'token',
      },
      global: { stubs: { teleport: true } },
    })

    await wrapper.get('.settings-btn').trigger('click')
    expect(wrapper.text()).not.toContain('ویرایش نقش')
  })

  it('shows the fixed admin-session rule without a browser alert and restores body overflow', async () => {
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {})
    const wrapper = await mountProfile(makeUser({ role: 'مدیر ارشد', max_sessions: 1 }))
    await wrapper.find('.sessions-config-box .inline-edit').trigger('click')
    expect(wrapper.get('.quota-feedback').text()).toContain('نمی‌تواند بیش از ۱ باشد')
    expect(alertSpy).not.toHaveBeenCalled()

    const vm = wrapper.vm as unknown as { showBlockModal: boolean }
    vm.showBlockModal = true
    await nextTick()
    expect(document.body.style.overflow).toBe('hidden')
    wrapper.unmount()
    expect(document.body.style.overflow).toBe('')
  })

  it('keeps an accountant session limit fixed at the backend-authoritative value of one', async () => {
    const user = makeUser({ is_accountant: true, max_sessions: 3 })
    const wrapper = await mountProfile(user)
    const select = wrapper.get('.form-select-sm')

    expect((select.element as HTMLSelectElement).value).toBe('1')
    expect(select.attributes('disabled')).toBeDefined()

    const vm = wrapper.vm as unknown as {
      editMaxSessions: number
      saveMaxSessions: () => Promise<void>
    }
    vm.editMaxSessions = 3
    routeRequestMock.mockResolvedValueOnce(makeResponse({ ...user, max_sessions: 1 }))
    await vm.saveMaxSessions()
    await flushPromises()

    expectMutationCall(1, '/api/users/12', 'PUT', { max_sessions: 1 }, 'ذخیره تنظیمات نشست ناموفق بود.')
    expect(user.max_sessions).toBe(1)
    expect(vm.editMaxSessions).toBe(1)
    expect(wrapper.get('.quota-feedback').attributes('role')).toBe('status')
  })

  it('shows a middle-manager self profile as read-only and never sends a sensitive mutation', async () => {
    const wrapper = await mountProfile(makeUser({ id: 12, role: 'مدیر میانی' }), {
      viewerUserId: 12,
      viewerRole: 'مدیر میانی',
    })

    expect(wrapper.get('.admin-sensitive-readonly').text()).toContain('حساب خودتان')
    expect(wrapper.find('.sessions-config-box').exists()).toBe(false)
    expect(wrapper.find('.settings-btn').exists()).toBe(false)
    expect(wrapper.find('.delete-btn').exists()).toBe(false)

    const vm = wrapper.vm as unknown as { saveMaxSessions: () => Promise<void> }
    await vm.saveMaxSessions()
    expect(routeRequestMock).not.toHaveBeenCalled()
  })

  it('shows a super-admin peer profile as read-only and keeps the directory return available', async () => {
    const wrapper = await mountProfile(makeUser({ id: 12, role: 'مدیر ارشد' }), {
      viewerUserId: 99,
      viewerRole: 'مدیر ارشد',
    })

    expect(wrapper.get('.admin-sensitive-readonly').text()).toContain('مدیر ارشد هم‌سطح')
    expect(wrapper.find('.sessions-config-box').exists()).toBe(false)
    expect(wrapper.find('.settings-btn').exists()).toBe(false)
    expect(wrapper.find('.delete-btn').exists()).toBe(false)

    await wrapper.get('.back-btn').trigger('click')
    expect(wrapper.emitted('navigate')).toEqual([['manage_users']])
  })

  it('recovers from a server-authoritative 403 without mutating the displayed user', async () => {
    const user = makeUser()
    const wrapper = await mountProfile(user, {
      viewerUserId: 99,
      viewerRole: 'مدیر ارشد',
    })
    // A preceding regression intentionally resets the module graph. Use the
    // class instance from this mounted component's runtime module graph.
    const { AppHttpError } = await import('../utils/httpErrorPolicy')
    routeRequestMock.mockRejectedValueOnce(new AppHttpError({
      status: 403,
      detail: 'اجازه ندارید',
    }))

    await openSettings(wrapper)
    await findButtonByText(wrapper, 'ویرایش نقش').trigger('click')
    await wrapper.get('.edit-section .form-select').setValue('پلیس')
    await wrapper.get('.edit-section .save-btn').trigger('click')
    await flushPromises()

    expect(user.role).toBe('عادی')
    expect(wrapper.get('.edit-section [role="alert"]').text()).toContain('اجازه تغییر این تنظیم حساس را ندارید')
    expect(wrapper.find('.ui-confirm-dialog').exists()).toBe(false)
  })
})
