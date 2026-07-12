import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CreateInvitationView from './CreateInvitationView.vue'

const createInvitationMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: createInvitationMocks.apiFetchMock,
}))

function makeJsonResponse(payload: unknown, ok = true, status = ok ? 200 : 400) {
  return {
    ok,
    status,
    json: async () => payload,
  }
}

function installClipboard(writeText?: ReturnType<typeof vi.fn>) {
  Object.defineProperty(window.navigator, 'clipboard', {
    configurable: true,
    value: writeText ? { writeText } : undefined,
  })
}

function installExecCommand(result: boolean | Error) {
  const execCommand = vi.fn(() => {
    if (result instanceof Error) {
      throw result
    }
    return result
  })
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    value: execCommand,
  })
  return execCommand
}

async function mountView(
  props: Partial<{ apiBaseUrl: string; jwtToken: string | null }> = {},
  options: { clearInitialFetch?: boolean } = {},
) {
  const wrapper = mount(CreateInvitationView, {
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
      ...props,
    },
  })
  await flushPromises()
  if (options.clearInitialFetch !== false) {
    createInvitationMocks.apiFetchMock.mockClear()
  }
  return wrapper
}

async function fillInviteForm(wrapper: ReturnType<typeof mount>, mobile = '09123456789') {
  await wrapper.get('#account_name').setValue('alireza')
  await wrapper.get('#mobile_number').setValue(mobile)
  await wrapper.get('#role').setValue('مدیر میانی')
}

describe('CreateInvitationView.vue', () => {
  beforeEach(() => {
    createInvitationMocks.apiFetchMock.mockReset()
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') {
        return Promise.resolve(makeJsonResponse([]))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })
    vi.useFakeTimers()
    installClipboard(vi.fn().mockResolvedValue(undefined))
    Object.defineProperty(window, 'confirm', {
      configurable: true,
      value: vi.fn(() => true),
    })
    localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('blocks invite creation when the admin is not authenticated', async () => {
    const wrapper = await mountView({ jwtToken: null })

    await wrapper.get('form').trigger('submit.prevent')

    expect(createInvitationMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('❌ خطا: شما احراز هویت نشده‌اید.')
  })

  it('rejects invalid mobile numbers before calling the API', async () => {
    const wrapper = await mountView()
    await fillInviteForm(wrapper, '۰۹۱۲۳')

    await wrapper.get('form').trigger('submit.prevent')

    expect(createInvitationMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('❌ شماره موبایل نامعتبر است.')
  })

  it('normalizes Persian digits and preserves the canonical Web link from the contract', async () => {
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: 'https://coin.gold-trade.ir/invite/abc?foo=1',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper, '۰۹۱۲۳۴۵۶۷۸۹')

    expect(wrapper.findAll('.form-group.ui-form-field')).toHaveLength(3)
    expect(wrapper.get('#account_name').classes()).toContain('ui-input')
    expect(wrapper.get('#role').classes()).toEqual(expect.arrayContaining(['ui-input', 'ui-select']))
    expect(wrapper.get('button[type="submit"]').classes()).toContain('ui-button')
    expect(wrapper.get('button.secondary').classes()).toContain('ui-button--secondary')

    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(createInvitationMocks.apiFetchMock).toHaveBeenCalledWith('/api/invitations/', {
      method: 'POST',
      body: JSON.stringify({
        account_name: 'alireza',
        mobile_number: '09123456789',
        role: 'مدیر میانی',
      }),
    })
    const textInputs = wrapper.findAll('.success-box input[readonly]')
    expect(wrapper.text()).toContain('✅ لینک دعوت با موفقیت ایجاد شد:')
    expect(textInputs[0]!.classes()).toContain('ui-input')
    expect(textInputs[1]!.classes()).toContain('ui-input')
    expect((textInputs[0]!.element as HTMLInputElement).value).toBe('https://t.me/mbmtrading1_bot?start=invite-token')
    expect((textInputs[1]!.element as HTMLInputElement).value).toBe('https://coin.gold-trade.ir/invite/abc?foo=1')
  })

  it('prefers explicit v2 links, accepts a Web-only success, and renders the SMS outcome', async () => {
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        bot_link: null,
        web_short_link: 'https://coin.gold-trade.ir/i/V2-CODE',
        bot_available: false,
        web_available: true,
        state: 'pending',
        sms_status: 'disabled',
        link: 'https://t.me/legacy_bot?start=must-not-render',
        short_link: 'https://foreign.example/i/must-not-render',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.find('.success-box').exists()).toBe(true)
    expect(wrapper.find('.telegram-btn').exists()).toBe(false)
    expect(wrapper.text()).toContain('پیامک دعوت ارسال نشد؛ لینک را دستی ارسال کنید.')
    const inputs = wrapper.findAll('.success-box input[readonly]')
    expect(inputs).toHaveLength(1)
    expect((inputs[0]!.element as HTMLInputElement).value).toBe('https://coin.gold-trade.ir/i/V2-CODE')
    expect(wrapper.text()).not.toContain('foreign.example')
  })

  it('fails closed when a successful response contains no usable registration link', async () => {
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        state: 'pending',
        bot_available: false,
        web_available: false,
        sms_status: 'disabled',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.find('.success-box').exists()).toBe(false)
    expect(wrapper.text()).toContain('لینک قابل استفاده‌ای برای این دعوت‌نامه آماده نشد.')
  })

  it('renders backend error details with strong markup when invite creation is rejected', async () => {
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({ detail: 'خطا **مهم**' }, false, 409),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)

    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.get('.result-box.error').html()).toContain('<strong>مهم</strong>')
    expect(wrapper.text()).toContain('❌ خطا مهم')
  })

  it('shows thrown request errors when the API call itself fails', async () => {
    createInvitationMocks.apiFetchMock.mockRejectedValue(new Error('network down'))

    const wrapper = await mountView()
    await fillInviteForm(wrapper)

    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.text()).toContain('❌ network down')
  })

  it('copies the Telegram invite link through navigator.clipboard and clears the toast after the timeout', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    installClipboard(writeText)
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: '',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    await wrapper.get('.copy-btn').trigger('click')
    await flushPromises()

    expect(wrapper.get('.copy-btn').classes()).toContain('ui-button')
    expect(writeText).toHaveBeenCalledWith('https://t.me/mbmtrading1_bot?start=invite-token')
    expect(wrapper.get('.copy-btn').text()).toBe('کپی شد!')

    await vi.advanceTimersByTimeAsync(2000)

    expect(wrapper.get('.copy-btn').text()).toBe('کپی')
  })

  it('falls back to execCommand copy for the web link and resets the form state on demand', async () => {
    installClipboard(undefined)
    const execCommand = installExecCommand(true)
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: 'https://coin.gold-trade.ir/register/short-code',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    await wrapper.get('.copy-btn.web').trigger('click')
    await flushPromises()

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(wrapper.get('.copy-btn.web').text()).toBe('کپی شد!')

    await wrapper.get('button.secondary').trigger('click')

    expect((wrapper.get('#account_name').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('#mobile_number').element as HTMLInputElement).value).toBe('')
    expect(wrapper.find('.success-box').exists()).toBe(false)
  })

  it('copies the derived web link through navigator.clipboard and surfaces clipboard failures', async () => {
    const writeText = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('clipboard denied'))
    installClipboard(writeText)
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: 'https://coin.gold-trade.ir/invite/route-token',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    await wrapper.get('.copy-btn.web').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenNthCalledWith(1, 'https://coin.gold-trade.ir/invite/route-token')
    expect(wrapper.get('.copy-btn.web').text()).toBe('کپی شد!')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.copy-btn.web').text()).toBe('کپی')

    await wrapper.get('.copy-btn.web').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenNthCalledWith(2, 'https://coin.gold-trade.ir/invite/route-token')
    expect(wrapper.get('.copy-btn.web').text()).toBe('خطا')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.copy-btn.web').text()).toBe('کپی')
  })

  it('keeps raw short links when the backend returns a non-URL value', async () => {
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: 'not-a-valid-url',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    const textInputs = wrapper.findAll('.success-box input[readonly]')
    expect((textInputs[1]!.element as HTMLInputElement).value).toBe('not-a-valid-url')
  })

  it('limits invite role choices for cached middle managers', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))

    const wrapper = await mountView()
    const roleOptions = wrapper.findAll('#role option').map((option) => (option.element as HTMLOptionElement).value)

    expect(roleOptions).toEqual(['تماشا', 'عادی'])
    expect((wrapper.get('#role').element as HTMLSelectElement).value).toBe('عادی')
  })

  it('loads pending invitations and renders their direct web registration links', async () => {
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') {
        return Promise.resolve(makeJsonResponse([
          {
            id: 12,
            account_name: 'pending-user',
            mobile_number: '09120000000',
            role: 'عادی',
            web_link: 'https://coin.gold-trade.ir/register?token=INV-PENDING',
            short_link: 'https://coin.gold-trade.ir/i/SHORT12',
            expires_at: '2026-06-12T10:00:00',
            created_at: '2026-06-11T10:00:00',
          },
        ]))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })

    expect(createInvitationMocks.apiFetchMock).toHaveBeenCalledWith('/api/invitations/pending')
    expect(wrapper.text()).toContain('pending-user')
    expect(wrapper.text()).toContain('09120000000')
    expect(wrapper.get('.pending-refresh-btn').classes()).toContain('ui-button')
    expect(wrapper.get('.pending-copy-btn').classes()).toContain('ui-button')
    expect(wrapper.get('.delete-pending-btn').classes()).toContain('ui-button')
    const pendingInputWrapper = wrapper.get('.pending-link-row input[readonly]')
    expect(pendingInputWrapper.classes()).toContain('ui-input')
    const pendingInput = pendingInputWrapper.element as HTMLInputElement
    expect(pendingInput.value).toBe('https://coin.gold-trade.ir/i/SHORT12')
  })

  it('copies both pending invitation surfaces and tracks each status independently', async () => {
    const writeText = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('clipboard denied'))
    installClipboard(writeText)
    createInvitationMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([
      {
        id: 14,
        account_name: 'dual-surface-user',
        mobile_number: '09120000014',
        role: 'عادی',
        bot_link: 'https://t.me/bot?start=INV-14',
        web_short_link: 'https://coin.gold-trade.ir/i/INV14',
        bot_available: true,
        web_available: true,
        state: 'pending',
        sms_status: 'disabled',
        expires_at: '2026-07-14T10:00:00Z',
      },
    ]))

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const buttons = wrapper.findAll('.pending-copy-btn')
    expect(buttons).toHaveLength(2)

    await buttons[0]!.trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(1, 'https://t.me/bot?start=INV-14')
    expect(buttons[0]!.text()).toBe('کپی شد!')
    expect(buttons[1]!.text()).toBe('کپی لینک وب')

    await buttons[1]!.trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(2, 'https://coin.gold-trade.ir/i/INV14')
    expect(buttons[1]!.text()).toBe('خطا')

    await vi.advanceTimersByTimeAsync(2000)
    expect(buttons[0]!.text()).toBe('کپی لینک تلگرام')
    expect(buttons[1]!.text()).toBe('کپی لینک وب')
  })

  it('uses the fallback copy path for a pending invitation surface', async () => {
    installClipboard(undefined)
    const execCommand = installExecCommand(true)
    createInvitationMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([
      {
        id: 15,
        account_name: 'fallback-user',
        mobile_number: '09120000015',
        role: 'عادی',
        web_short_link: 'https://coin.gold-trade.ir/i/INV15',
        web_available: true,
        state: 'pending',
        expires_at: '2026-07-14T10:00:00Z',
      },
    ]))

    const wrapper = await mountView({}, { clearInitialFetch: false })
    await wrapper.get('.pending-copy-btn').trigger('click')
    await flushPromises()

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(wrapper.get('.pending-copy-btn').text()).toBe('کپی شد!')
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.pending-copy-btn').text()).toBe('کپی لینک وب')
  })

  it('reports both bounded fallback-copy failures for pending invitation links', async () => {
    installClipboard(undefined)
    const execCommand = installExecCommand(false)
    createInvitationMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([{
      id: 16,
      account_name: 'fallback-error-user',
      mobile_number: '09120000016',
      role: 'عادی',
      bot_link: 'https://t.me/bot?start=INV-16',
      web_short_link: 'https://coin.gold-trade.ir/i/INV16',
      bot_available: true,
      web_available: true,
      state: 'pending',
      expires_at: '2026-07-14T10:00:00Z',
    }]))

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const inputs = wrapper.findAll('.pending-link-row input[readonly]')
    await inputs[0]!.trigger('click')
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(wrapper.findAll('.pending-copy-btn')[0]!.text()).toBe('خطا')

    execCommand.mockImplementation(() => { throw new Error('copy unavailable') })
    await inputs[1]!.trigger('click')
    expect(wrapper.findAll('.pending-copy-btn')[1]!.text()).toBe('خطا')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.findAll('.pending-copy-btn')[0]!.text()).toBe('کپی لینک تلگرام')
    expect(wrapper.findAll('.pending-copy-btn')[1]!.text()).toBe('کپی لینک وب')
  })

  it('deletes a pending invitation after confirmation and removes it from the list', async () => {
    createInvitationMocks.apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/invitations/pending') {
        return Promise.resolve(makeJsonResponse([
          {
            id: 12,
            account_name: 'pending-user',
            mobile_number: '09120000000',
            role: 'عادی',
            web_link: 'https://coin.gold-trade.ir/register?token=INV-PENDING',
            expires_at: '2026-06-12T10:00:00',
            created_at: '2026-06-11T10:00:00',
          },
        ]))
      }
      if (url === '/api/invitations/pending/12' && init?.method === 'DELETE') {
        return Promise.resolve(makeJsonResponse({}, true, 204))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })

    expect(wrapper.text()).toContain('pending-user')

    await wrapper.get('.delete-pending-btn').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('دعوت‌نامه pending-user حذف شود؟')
    expect(createInvitationMocks.apiFetchMock).toHaveBeenCalledWith('/api/invitations/pending/12', { method: 'DELETE' })
    expect(wrapper.text()).not.toContain('pending-user')
    expect(wrapper.get('.pending-state.empty').classes()).toContain('ui-empty-state')
    expect(wrapper.text()).toContain('دعوت‌نامه pending وجود ندارد.')
  })

  it('renders pending invitation load failures with the shared error state', async () => {
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({ detail: 'pending down' }, false, 500),
    )

    const wrapper = await mountView({}, { clearInitialFetch: false })

    const pendingError = wrapper.get('.pending-error')
    expect(pendingError.classes()).toContain('ui-empty-state')
    expect(pendingError.attributes('role')).toBe('alert')
    expect(pendingError.text()).toContain('pending down')
  })

  it('shows a fallback copy error for the Telegram link when execCommand returns false', async () => {
    installClipboard(undefined)
    installExecCommand(false)
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: '',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    await wrapper.get('.copy-btn').trigger('click')
    await flushPromises()

    expect(wrapper.get('.copy-btn').text()).toBe('خطا')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.copy-btn').text()).toBe('کپی')
  })

  it('surfaces Telegram clipboard write failures without using the fallback textarea path', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('clipboard denied'))
    installClipboard(writeText)
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: '',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    await wrapper.get('.copy-btn').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith('https://t.me/mbmtrading1_bot?start=invite-token')
    expect(wrapper.get('.copy-btn').text()).toBe('خطا')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.copy-btn').text()).toBe('کپی')
  })

  it('shows a fallback copy error for the web link when execCommand throws', async () => {
    installClipboard(undefined)
    installExecCommand(new Error('copy failed'))
    createInvitationMocks.apiFetchMock.mockResolvedValue(
      makeJsonResponse({
        link: 'https://t.me/mbmtrading1_bot?start=invite-token',
        short_link: 'https://coin.gold-trade.ir/invite/route-token',
      }),
    )

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    await wrapper.get('.copy-btn.web').trigger('click')
    await flushPromises()

    expect(wrapper.get('.copy-btn.web').text()).toBe('خطا')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.copy-btn.web').text()).toBe('کپی')
  })
})
