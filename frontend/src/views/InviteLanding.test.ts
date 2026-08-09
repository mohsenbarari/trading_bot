import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import InviteLanding from './InviteLanding.vue'
import {
  clearRegistrationExchangeId,
  clearRegistrationHandoff,
  readRegistrationHandoff,
} from '../utils/registrationHandoff'

const inviteLandingMocks = vi.hoisted(() => ({
  route: { params: { code: 'abc123' } },
  push: vi.fn(),
  replace: vi.fn(),
  fetch: vi.fn(),
  openTelegramLink: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => inviteLandingMocks.route,
  useRouter: () => ({ push: inviteLandingMocks.push, replace: inviteLandingMocks.replace }),
}))

vi.mock('../services/telegramLink', () => ({
  openTelegramLink: inviteLandingMocks.openTelegramLink,
}))

function serializedStorage(storage: Storage): string {
  return Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index) ?? ''
    return `${key}:${storage.getItem(key) ?? ''}`
  }).join('|')
}

describe('InviteLanding.vue', () => {
  beforeEach(() => {
    inviteLandingMocks.route.params.code = 'abc123'
    inviteLandingMocks.push.mockReset()
    inviteLandingMocks.replace.mockReset()
    inviteLandingMocks.fetch.mockReset()
    inviteLandingMocks.openTelegramLink.mockReset()
    clearRegistrationHandoff()
    clearRegistrationExchangeId()
    localStorage.clear()
    sessionStorage.clear()
    vi.stubGlobal('fetch', inviteLandingMocks.fetch)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('loads the invitation and config, renders both registration actions, and routes web registration', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'token-123',
            expires_at: '2026-07-14T10:00:00Z',
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ bot_username: 'mbmtrading1_bot' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            kind: 'invitation',
            account_name: 'user1',
            mobile_number: '0912****000',
            role: 'عادی',
            progress: 'context_ready',
            requires_otp: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )

    const wrapper = mount(InviteLanding, { attachTo: document.body })
    await flushPromises()

    expect(wrapper.findAll('main')).toHaveLength(1)
    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(
      1,
      '/api/invitations/lookup/abc123',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )
    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(
      2,
      '/api/config',
      expect.objectContaining({
        signal: expect.any(AbortSignal),
      }),
    )
    expect(wrapper.get('h1').text()).toBe('دعوت‌نامه اختصاصی')
    expect(wrapper.text()).toContain('تا پایان مهلت، روش ثبت‌نام را انتخاب کنید.')
    expect(wrapper.text()).toContain('دعوت‌نامه معتبر است')
    expect(wrapper.get('[data-invite-outcome]').attributes('tabindex')).toBe('-1')
    expect(document.activeElement).toBe(wrapper.get('[data-invite-outcome]').element)
    expect(wrapper.find('a[href*="token-123"]').exists()).toBe(false)
    expect(wrapper.html()).not.toContain('token-123')
    expect(wrapper.html()).not.toContain('mbmtrading1_bot')
    expect(serializedStorage(sessionStorage)).not.toContain('token-123')
    expect(serializedStorage(localStorage)).not.toContain('token-123')

    await wrapper.get('button.ui-v2-auth-invite-route--telegram').trigger('click')
    expect(inviteLandingMocks.openTelegramLink).toHaveBeenCalledWith(
      'https://t.me/mbmtrading1_bot?start=token-123',
    )
    expect(wrapper.html()).not.toContain('token-123')
    expect(serializedStorage(sessionStorage)).not.toContain('token-123')
    expect(serializedStorage(localStorage)).not.toContain('token-123')

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ثبت‌نام از طریق وب'))!
      .trigger('click')
    await flushPromises()
    expect(inviteLandingMocks.push).toHaveBeenCalledWith({ name: 'web-register' })
    expect(readRegistrationHandoff()).toBeNull()
    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(
      3,
      '/api/auth/registration-context/exchange',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
    const exchangeBody = JSON.parse(String(inviteLandingMocks.fetch.mock.calls[2]?.[1]?.body))
    expect(exchangeBody).toEqual({
      kind: 'invitation',
      token: 'token-123',
      exchange_id: expect.stringMatching(/^exchange_[a-f0-9]{64}$/u),
    })
    expect(exchangeBody.exchange_id).not.toContain('abc123')
    expect(String(inviteLandingMocks.push.mock.calls[0]?.[0])).not.toContain('token-123')
    expect(serializedStorage(sessionStorage)).not.toContain('token-123')
    expect(serializedStorage(localStorage)).not.toContain('token-123')
    wrapper.unmount()
  })

  it('shows a friendly error when invitation lookup fails', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(new Response(null, { status: 404 }))

    const wrapper = mount(InviteLanding, { attachTo: document.body })
    await flushPromises()

    expect(wrapper.text()).toContain('دعوت‌نامه نامعتبر یا منقضی شده است.')
    expect(wrapper.text()).toContain('دعوت‌نامه قابل استفاده نیست')
    expect(wrapper.find('.ui-v2-auth-invite-actions').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('تلاش مجدد')
    expect(wrapper.get('[data-invite-outcome]').attributes('tabindex')).toBe('-1')
    expect(document.activeElement).toBe(wrapper.get('[data-invite-outcome]').element)
    wrapper.unmount()
  })

  it('retries the same lookup after a recoverable network failure', async () => {
    inviteLandingMocks.fetch
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'retry-token',
            valid: true,
            state: 'pending',
            bot_available: false,
            web_available: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.text()).toContain('بررسی دعوت‌نامه اکنون ممکن نشد')
    expect(wrapper.text()).toContain('بررسی دعوت‌نامه انجام نشد')
    expect(wrapper.text()).not.toContain('دعوت‌نامه قابل استفاده نیست')
    const retry = wrapper.findAll('button').find((button) => button.text().includes('تلاش مجدد'))!
    await retry.trigger('click')
    await flushPromises()

    expect(inviteLandingMocks.fetch).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('دعوت‌نامه معتبر')
    expect(wrapper.get('h1').text()).toBe('ثبت‌نام در وب‌اپ')
    expect(wrapper.text()).toContain('ادامه ثبت‌نام')
  })

  it('reuses the random tab-bound exchange id after a committed response is lost', async () => {
    const lookupReceipt = () =>
      new Response(
        JSON.stringify({
          token: 'INV-response-lost',
          valid: true,
          state: 'pending',
          bot_available: false,
          web_available: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(lookupReceipt())
      .mockRejectedValueOnce(new TypeError('response lost'))
      .mockResolvedValueOnce(lookupReceipt())
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            kind: 'invitation',
            account_name: 'user1',
            mobile_number: '0912****000',
            role: 'عادی',
            progress: 'context_ready',
            requires_otp: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )

    const wrapper = mount(InviteLanding)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ادامه ثبت‌نام اکنون ممکن نشد')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تلاش مجدد'))!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    const exchangeBodies = inviteLandingMocks.fetch.mock.calls
      .filter(([url]) => url === '/api/auth/registration-context/exchange')
      .map(([, options]) => JSON.parse(String(options?.body)))
    expect(exchangeBodies).toHaveLength(2)
    expect(exchangeBodies[0]).toEqual(exchangeBodies[1])
    expect(exchangeBodies[0]).toEqual({
      kind: 'invitation',
      token: 'INV-response-lost',
      exchange_id: expect.stringMatching(/^exchange_[a-f0-9]{64}$/u),
    })
    expect(exchangeBodies[0].exchange_id).not.toContain('abc123')
    expect(inviteLandingMocks.push).toHaveBeenCalledWith({ name: 'web-register' })
    expect(wrapper.html()).not.toContain('INV-response-lost')
  })

  it('retains the exchange binding until navigation succeeds', async () => {
    const lookupReceipt = () =>
      new Response(
        JSON.stringify({
          token: 'INV-navigation-loss',
          valid: true,
          state: 'pending',
          bot_available: false,
          web_available: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    const contextReceipt = () =>
      new Response(
        JSON.stringify({
          kind: 'invitation',
          account_name: 'user1',
          mobile_number: '0912****000',
          role: 'عادی',
          progress: 'context_ready',
          requires_otp: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(lookupReceipt())
      .mockResolvedValueOnce(contextReceipt())
      .mockResolvedValueOnce(lookupReceipt())
      .mockResolvedValueOnce(contextReceipt())
    inviteLandingMocks.push
      .mockResolvedValueOnce({ type: 4, to: { name: 'web-register' } })
      .mockResolvedValueOnce(undefined)

    const wrapper = mount(InviteLanding)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ادامه ثبت‌نام اکنون ممکن نشد')
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تلاش مجدد'))!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    const exchangeBodies = inviteLandingMocks.fetch.mock.calls
      .filter(([url]) => url === '/api/auth/registration-context/exchange')
      .map(([, options]) => JSON.parse(String(options?.body)))
    expect(exchangeBodies).toHaveLength(2)
    expect(exchangeBodies[0].exchange_id).toBe(exchangeBodies[1].exchange_id)
    expect(inviteLandingMocks.push).toHaveBeenCalledTimes(2)
    expect(serializedStorage(sessionStorage)).not.toContain('exchange_')
    expect(wrapper.html()).not.toContain('INV-navigation-loss')
  })

  it('treats an exchange claim owned by another id as terminal', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'INV-claimed',
            valid: true,
            state: 'pending',
            bot_available: false,
            web_available: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'already claimed' }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(InviteLanding)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('دعوت‌نامه نامعتبر یا منقضی شده است.')
    expect(wrapper.text()).not.toContain('تلاش مجدد')
    expect(wrapper.html()).not.toContain('INV-claimed')
  })

  it('routes a recovered durable completion to login and clears the exchange binding', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'INV-completed-response-loss',
            valid: true,
            state: 'pending',
            bot_available: false,
            web_available: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: 'registration_complete' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))

    const wrapper = mount(InviteLanding)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(inviteLandingMocks.replace).toHaveBeenCalledWith({
      name: 'login',
      query: { registration: 'complete' },
    })
    expect(inviteLandingMocks.push).not.toHaveBeenCalled()
    expect(inviteLandingMocks.fetch).toHaveBeenNthCalledWith(
      3,
      '/api/auth/registration-context/clear',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' }),
    )
    expect(serializedStorage(sessionStorage)).not.toContain('exchange_')
    expect(wrapper.html()).not.toContain('INV-completed-response-loss')
  })

  it('keeps the durable completion marker and exchange binding when login navigation fails', async () => {
    const lookupReceipt = () =>
      new Response(
        JSON.stringify({
          token: 'INV-completion-navigation-loss',
          valid: true,
          state: 'pending',
          bot_available: false,
          web_available: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    const completionReceipt = () =>
      new Response(JSON.stringify({ status: 'registration_complete' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(lookupReceipt())
      .mockResolvedValueOnce(completionReceipt())
      .mockResolvedValueOnce(lookupReceipt())
      .mockResolvedValueOnce(completionReceipt())
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    inviteLandingMocks.replace
      .mockResolvedValueOnce({ type: 4, to: { name: 'login' } })
      .mockResolvedValueOnce(undefined)

    const wrapper = mount(InviteLanding)
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ادامه ثبت‌نام اکنون ممکن نشد')
    expect(serializedStorage(sessionStorage)).toContain('exchange_')
    expect(
      inviteLandingMocks.fetch.mock.calls.some(
        ([url]) => url === '/api/auth/registration-context/clear',
      ),
    ).toBe(false)

    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('تلاش مجدد'))!
      .trigger('click')
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('ادامه ثبت‌نام'))!
      .trigger('click')
    await flushPromises()

    expect(inviteLandingMocks.replace).toHaveBeenCalledTimes(2)
    expect(inviteLandingMocks.fetch.mock.calls.map(([url]) => url)).toEqual([
      '/api/invitations/lookup/abc123',
      '/api/auth/registration-context/exchange',
      '/api/invitations/lookup/abc123',
      '/api/auth/registration-context/exchange',
      '/api/auth/registration-context/clear',
    ])
    expect(serializedStorage(sessionStorage)).not.toContain('exchange_')
    expect(wrapper.html()).not.toContain('INV-completion-navigation-loss')
  })

  it('bounds an unavailable lookup by timeout and offers recovery', async () => {
    vi.useFakeTimers()
    inviteLandingMocks.fetch.mockImplementationOnce(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal?.addEventListener('abort', () => reject(options.signal?.reason), {
            once: true,
          })
        }),
    )

    const wrapper = mount(InviteLanding)
    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()

    expect(wrapper.text()).toContain('بررسی دعوت‌نامه اکنون ممکن نشد')
    expect(wrapper.text()).toContain('تلاش مجدد')
  })

  it('renders only the Web path when the v2 contract marks Telegram unavailable', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          token: 'accountant-token',
          valid: true,
          state: 'pending',
          bot_available: false,
          web_available: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(inviteLandingMocks.fetch).toHaveBeenCalledTimes(1)
    expect(wrapper.find('button.ui-v2-auth-invite-route--telegram').exists()).toBe(false)
    expect(wrapper.get('h1').text()).toBe('ثبت‌نام در وب‌اپ')
    expect(wrapper.text()).toContain('ادامه ثبت‌نام')
  })

  it('routes a completed invitation to OTP login without showing registration actions', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          valid: false,
          state: 'completed',
          bot_available: false,
          web_available: false,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(inviteLandingMocks.replace).toHaveBeenCalledWith({
      name: 'login',
      query: { registration: 'complete' },
    })
    expect(wrapper.find('.ui-v2-auth-invite-actions').exists()).toBe(false)
    expect(inviteLandingMocks.fetch).toHaveBeenCalledTimes(1)
  })

  it('keeps Web registration available when Telegram config cannot be loaded', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'token-123',
            valid: true,
            state: 'pending',
            bot_available: true,
            web_available: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 503 }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.find('button.ui-v2-auth-invite-route--telegram').exists()).toBe(false)
    expect(wrapper.get('h1').text()).toBe('ثبت‌نام در وب‌اپ')
    expect(wrapper.text()).toContain('ادامه ثبت‌نام')
    expect(wrapper.text()).not.toContain('دعوت‌نامه قابل استفاده نیست')
  })

  it('disables only Telegram when config has no bot username', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'token-123',
            valid: true,
            state: 'pending',
            bot_available: true,
            web_available: true,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.find('button.ui-v2-auth-invite-route--telegram').exists()).toBe(false)
    expect(wrapper.get('h1').text()).toBe('ثبت‌نام در وب‌اپ')
    expect(wrapper.text()).toContain('ادامه ثبت‌نام')
  })

  it('offers retry when the only available Telegram path cannot be prepared', async () => {
    inviteLandingMocks.fetch
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            token: 'telegram-only-token',
            valid: true,
            state: 'pending',
            bot_available: true,
            web_available: false,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 503 }))

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.text()).toContain('بررسی دعوت‌نامه اکنون ممکن نشد')
    expect(wrapper.text()).toContain('تلاش مجدد')
    expect(wrapper.find('.ui-v2-auth-invite-actions').exists()).toBe(false)
  })

  it('renders the bounded terminal message for an expired invitation', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          valid: false,
          state: 'expired',
          bot_available: false,
          web_available: false,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.text()).toContain(
      'مهلت ثبت‌نام پایان یافته است. لطفاً دعوت‌نامه جدید دریافت کنید.',
    )
    expect(wrapper.find('.ui-v2-auth-invite-actions').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('تلاش مجدد')
  })

  it('fails closed when a pending response omits its token', async () => {
    inviteLandingMocks.fetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          valid: true,
          state: 'pending',
          bot_available: false,
          web_available: true,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    const wrapper = mount(InviteLanding)
    await flushPromises()

    expect(wrapper.text()).toContain('دعوت‌نامه نامعتبر یا منقضی شده است.')
    expect(wrapper.find('.ui-v2-auth-invite-actions').exists()).toBe(false)
  })
})
