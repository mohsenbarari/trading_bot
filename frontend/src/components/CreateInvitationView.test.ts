import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { DOMWrapper, flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CreateInvitationView from './CreateInvitationView.vue'

const createInvitationViewSource = readFileSync(
  resolve(process.cwd(), 'src/components/CreateInvitationView.vue'),
  'utf8',
)

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
    headers: { get: () => 'application/json' },
    json: async () => payload,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise })
  return { promise, resolve }
}

function installClipboard(writeText?: ReturnType<typeof vi.fn>) {
  Object.defineProperty(window.navigator, 'clipboard', {
    configurable: true,
    value: writeText ? { writeText } : undefined,
  })
}

function pendingInvitation(overrides: Record<string, unknown> = {}) {
  return {
    id: 12,
    account_name: 'pending-user',
    mobile_number: '09120000000',
    role: 'عادی',
    web_short_link: 'https://coin.gold-trade.ir/i/PEND0012',
    web_available: true,
    state: 'pending',
    expires_at: '2026-06-12T10:00:00Z',
    ...overrides,
  }
}

const mountedWrappers: Array<{ unmount: () => void }> = []

async function mountView(
  props: Partial<{ apiBaseUrl: string; jwtToken: string | null }> = {},
  options: { clearInitialFetch?: boolean; attachToBody?: boolean } = {},
) {
  const wrapper = mount(CreateInvitationView, {
    attachTo: options.attachToBody ? document.body : undefined,
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
      ...props,
    },
  })
  mountedWrappers.push(wrapper)
  await flushPromises()
  if (options.clearInitialFetch !== false) createInvitationMocks.apiFetchMock.mockClear()
  return wrapper
}

async function fillInviteForm(wrapper: ReturnType<typeof mount>, mobile = '09123456789') {
  await wrapper.get('#account_name').setValue('alireza')
  await wrapper.get('#mobile_number').setValue(mobile)
  await wrapper.get('#role').setValue('مدیر میانی')
}

async function openDeleteDialog(wrapper: ReturnType<typeof mount>) {
  await wrapper.get('.delete-pending-btn').trigger('click')
  await flushPromises()
  const dialog = document.body.querySelector<HTMLElement>('.ui-confirm-dialog')
  if (!dialog) throw new Error('confirm_dialog_not_mounted')
  return new DOMWrapper(dialog)
}

function hasMountedConfirmDialog() {
  return Boolean(document.body.querySelector('.ui-confirm-dialog'))
}

describe('CreateInvitationView.vue', () => {
  beforeEach(() => {
    createInvitationMocks.apiFetchMock.mockReset()
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([]))
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })
    vi.useFakeTimers()
    installClipboard(vi.fn().mockResolvedValue(undefined))
    localStorage.clear()
  })

  afterEach(() => {
    while (mountedWrappers.length) mountedWrappers.pop()?.unmount()
    document.body.replaceChildren()
    delete document.body.dataset.uiOverlayLockCount
    document.body.classList.remove('ui-overlay-open')
    document.documentElement.classList.remove('ui-overlay-open')
    vi.useRealTimers()
  })

  it('keeps invitation presentation local to the existing admin section card', () => {
    expect(createInvitationViewSource).toMatch(
      /<div class="invitation-manager">\s*<form class="invitation-form" @submit\.prevent="createInvite" autocomplete="off">/,
    )
    expect(createInvitationViewSource).toMatch(
      /\.invitation-manager\s*\{[\s\S]*?min-width:\s*0;[\s\S]*?font-family:\s*Vazirmatn,\s*Tahoma,\s*Arial,\s*sans-serif;[\s\S]*?font-synthesis:\s*none;/,
    )
    expect(createInvitationViewSource).toContain('<AppConfirmDialog')
    expect(createInvitationViewSource).toMatch(/<AppEmptyState[\s\S]*?role="status"/)
    expect(createInvitationViewSource).toContain('class="copy-btn web"')
    expect(createInvitationViewSource).not.toMatch(/(?:background|box-shadow):\s*linear-gradient\(/)
    expect(createInvitationViewSource).toMatch(
      /@media \(max-width: 540px\) \{[\s\S]*?\.form-actions \.ui-button:first-child \{ flex: 0 0 auto; \}/,
    )
    expect(createInvitationViewSource).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\.invitation-manager :deep\(\.ui-button__spinner\),[\s\S]*?\.invitation-manager :deep\(\.ui-loading-state__spinner\)[\s\S]*?animation-duration: 0\.01ms !important;[\s\S]*?animation-iteration-count: 1 !important;/,
    )
    expect(createInvitationViewSource).toMatch(/\.pending-title\s*\{\s*overflow-wrap:\s*anywhere;\s*\}/)
  })

  it('blocks invitation creation before any request when authentication is missing', async () => {
    const wrapper = await mountView({ jwtToken: null })

    await wrapper.get('form').trigger('submit.prevent')

    expect(createInvitationMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('❌ خطا: شما احراز هویت نشده‌اید.')
  })

  it('normalizes Persian digits and rejects an invalid mobile number before calling the API', async () => {
    const wrapper = await mountView()
    await fillInviteForm(wrapper, '۰۹۱۲۳')

    await wrapper.get('form').trigger('submit.prevent')

    expect(createInvitationMocks.apiFetchMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('❌ شماره موبایل نامعتبر است.')
  })

  it('renders a new invitation truthfully with expiry and copy-only controls, never raw URLs', async () => {
    const botLink = 'https://t.me/mbmtrading1_bot?start=INV-create'
    const webLink = 'https://coin.gold-trade.ir/i/Ab12Cd34'
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([]))
      if (url === '/api/invitations/') {
        return Promise.resolve(makeJsonResponse({
          created: true,
          state: 'pending',
          bot_link: botLink,
          web_short_link: webLink,
          bot_available: true,
          web_available: true,
          expires_at: '2026-07-14T10:00:00Z',
          sms_status: 'accepted',
        }))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView()
    await fillInviteForm(wrapper, '۰۹۱۲۳۴۵۶۷۸۹')
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(createInvitationMocks.apiFetchMock).toHaveBeenCalledWith('/api/invitations/', expect.objectContaining({
      method: 'POST',
      cache: 'no-store',
      body: JSON.stringify({
        account_name: 'alireza',
        mobile_number: '09123456789',
        role: 'مدیر میانی',
      }),
      retryNetwork: false,
      trackConnectionState: false,
    }))
    expect(wrapper.get('.success-box').text()).toContain('دعوت‌نامهٔ تازه ساخته شد.')
    expect(wrapper.get('.invitation-expiry').text()).toContain('مهلت دعوت:')
    expect(wrapper.text()).toContain('پیامک دعوت ارسال شد.')
    expect(wrapper.findAll('.success-box .copy-btn')).toHaveLength(2)
    expect(wrapper.get('.success-box .copy-btn').classes()).toContain('ui-button--secondary')
    expect(wrapper.get('.success-box .copy-btn.web').classes()).toContain('ui-button--primary')
    expect(wrapper.findAll('.success-box input')).toHaveLength(0)
    expect(wrapper.html()).not.toContain(botLink)
    expect(wrapper.html()).not.toContain(webLink)
  })

  it('distinguishes a recovered active invitation and only offers its available channel', async () => {
    const webLink = 'https://coin.gold-trade.ir/i/V2CODE01'
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([]))
      if (url === '/api/invitations/') {
        return Promise.resolve(makeJsonResponse({
          created: false,
          state: 'pending',
          bot_link: null,
          web_short_link: webLink,
          bot_available: false,
          web_available: true,
          expires_at: '2026-07-14T10:00:00Z',
          sms_status: 'disabled',
        }))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.get('.success-box').text()).toContain('دعوت‌نامهٔ فعال قبلی بازیابی شد.')
    expect(wrapper.text()).toContain('پیامک دعوت ارسال نشد؛ لینک را دستی ارسال کنید.')
    expect(wrapper.findAll('.success-box .copy-btn')).toHaveLength(1)
    expect(wrapper.get('.success-box .copy-btn').text()).toBe('کپی لینک وب')
    expect(wrapper.get('.success-box .copy-btn').classes()).toContain('ui-button--primary')
    expect(wrapper.html()).not.toContain(webLink)
  })

  it('fails closed when a successful create response contains no usable registration link', async () => {
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([]))
      if (url === '/api/invitations/') {
        return Promise.resolve(makeJsonResponse({
          state: 'pending',
          bot_available: false,
          web_available: false,
        }))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.find('.success-box').exists()).toBe(false)
    expect(wrapper.text()).toContain('لینک قابل استفاده‌ای برای این دعوت‌نامه آماده نشد.')
  })

  it('keeps the invitation draft and hides backend detail when creation is rejected', async () => {
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([]))
      return Promise.resolve(makeJsonResponse({ detail: 'حساس **جزئیات**' }, false, 409))
    })

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()

    expect(wrapper.get('.result-box.error').text()).toContain('ساخت دعوت‌نامه انجام نشد.')
    expect(wrapper.text()).not.toContain('جزئیات')
    expect((wrapper.get('#account_name').element as HTMLInputElement).value).toBe('alireza')
    expect((wrapper.get('#mobile_number').element as HTMLInputElement).value).toBe('09123456789')
  })

  it('uses Clipboard API for links and leaves the raw bearer outside the rendered DOM', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    installClipboard(writeText)
    const botLink = 'https://t.me/mbmtrading1_bot?start=INV-copy'
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([]))
      return Promise.resolve(makeJsonResponse({
        created: true,
        state: 'pending',
        bot_link: botLink,
        bot_available: true,
        web_available: false,
        expires_at: '2026-07-14T10:00:00Z',
      }))
    })

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()
    await wrapper.get('.copy-btn').trigger('click')
    await flushPromises()

    expect(writeText).toHaveBeenCalledWith(botLink)
    expect(wrapper.get('.copy-btn').text()).toBe('کپی شد!')
    expect(wrapper.html()).not.toContain(botLink)
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.get('.copy-btn').text()).toBe('کپی لینک تلگرام')
  })

  it('reports clipboard unavailability without putting a fallback textarea or link in the DOM', async () => {
    installClipboard(undefined)
    const webLink = 'https://coin.gold-trade.ir/i/COPY0001'
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([]))
      return Promise.resolve(makeJsonResponse({
        created: true,
        state: 'pending',
        web_short_link: webLink,
        bot_available: false,
        web_available: true,
        expires_at: '2026-07-14T10:00:00Z',
      }))
    })

    const wrapper = await mountView()
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()
    await wrapper.get('.copy-btn.web').trigger('click')
    await flushPromises()

    expect(wrapper.get('.copy-btn.web').text()).toContain('کپی نشد')
    expect(wrapper.html()).not.toContain(webLink)
    expect(document.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('limits invite role choices for cached middle managers without replacing server authority', async () => {
    localStorage.setItem('current_user_summary', JSON.stringify({ role: 'مدیر میانی' }))

    const wrapper = await mountView()
    const roleOptions = wrapper.findAll('#role option').map((option) => (option.element as HTMLOptionElement).value)

    expect(roleOptions).toEqual(['تماشا', 'عادی'])
    expect((wrapper.get('#role').element as HTMLSelectElement).value).toBe('عادی')
  })

  it('loads pending invitations with no-store, no total, and copy-only channel controls', async () => {
    const botLink = 'https://t.me/bot?start=INV-queue'
    const webLink = 'https://coin.gold-trade.ir/i/QUEUE001'
    const writeText = vi.fn().mockResolvedValue(undefined)
    installClipboard(writeText)
    createInvitationMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([
      pendingInvitation({ bot_link: botLink, web_short_link: webLink, bot_available: true }),
    ]))

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const pendingRequest = createInvitationMocks.apiFetchMock.mock.calls.find(([url]) => url === '/api/invitations/pending')

    expect(pendingRequest?.[1]).toEqual(expect.objectContaining({
      cache: 'no-store',
      retryNetwork: false,
      trackConnectionState: false,
      signal: expect.any(AbortSignal),
    }))
    expect(wrapper.get('.pending-header p').text()).toBe('فهرست دعوت‌های در انتظار')
    expect(wrapper.text()).not.toContain('دعوت‌نامه فعال')
    expect(wrapper.findAll('.pending-copy-btn')).toHaveLength(2)
    expect(wrapper.findAll('.pending-copy-btn')[0]!.classes()).toContain('ui-button--secondary')
    expect(wrapper.findAll('.pending-copy-btn')[1]!.classes()).toContain('ui-button--primary')
    expect(wrapper.findAll('.pending-link-row input')).toHaveLength(0)
    expect(wrapper.html()).not.toContain(botLink)
    expect(wrapper.html()).not.toContain(webLink)

    const copyButtons = wrapper.findAll('.pending-copy-btn')
    await copyButtons[0]!.trigger('click')
    await copyButtons[1]!.trigger('click')
    await flushPromises()
    expect(writeText).toHaveBeenNthCalledWith(1, botLink)
    expect(writeText).toHaveBeenNthCalledWith(2, webLink)
  })

  it('preserves loaded pending invitations when a background refresh fails', async () => {
    let pendingCalls = 0
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url !== '/api/invitations/pending') return Promise.reject(new Error(`Unexpected API call: ${url}`))
      pendingCalls += 1
      return Promise.resolve(pendingCalls === 1
        ? makeJsonResponse([pendingInvitation()])
        : makeJsonResponse({ detail: 'private failure' }, false, 500))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    await wrapper.get('.pending-refresh-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('pending-user')
    expect(wrapper.get('.pending-refresh-error').text()).toContain('دریافت دعوت‌نامه‌ها ممکن نشد.')
    expect(wrapper.text()).not.toContain('private failure')
  })

  it('clears loaded pending invitation data when a refresh reports 403', async () => {
    let pendingCalls = 0
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url !== '/api/invitations/pending') return Promise.reject(new Error(`Unexpected API call: ${url}`))
      pendingCalls += 1
      return Promise.resolve(pendingCalls === 1
        ? makeJsonResponse([pendingInvitation()])
        : makeJsonResponse({ detail: 'private access policy' }, false, 403))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    await wrapper.get('.pending-refresh-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('pending-user')
    expect(wrapper.get('.pending-error').text()).toContain('دسترسی شما به فهرست دعوت‌نامه‌ها تأیید نشد.')
    expect(wrapper.text()).not.toContain('private access policy')
  })

  it('ignores an aborted stale pending response after a newer refresh wins', async () => {
    const first = deferred<ReturnType<typeof makeJsonResponse>>()
    const second = deferred<ReturnType<typeof makeJsonResponse>>()
    let pendingCalls = 0
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') {
        pendingCalls += 1
        return pendingCalls === 1 ? first.promise : second.promise
      }
      if (url === '/api/invitations/') {
        return Promise.resolve(makeJsonResponse({
          created: true,
          state: 'pending',
          web_short_link: 'https://coin.gold-trade.ir/i/STALE001',
          bot_available: false,
          web_available: true,
          expires_at: '2026-07-14T10:00:00Z',
        }))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()
    expect(pendingCalls).toBe(2)
    second.resolve(makeJsonResponse([pendingInvitation({ account_name: 'fresh-user', id: 22 })]))
    await flushPromises()
    expect(wrapper.text()).toContain('fresh-user')

    first.resolve(makeJsonResponse([pendingInvitation({ account_name: 'stale-user', id: 23 })]))
    await flushPromises()
    expect(wrapper.text()).toContain('fresh-user')
    expect(wrapper.text()).not.toContain('stale-user')
  })

  it('aborts an in-flight pending request when the component unmounts', async () => {
    const request = deferred<ReturnType<typeof makeJsonResponse>>()
    createInvitationMocks.apiFetchMock.mockReturnValue(request.promise)

    const wrapper = await mountView({}, { clearInitialFetch: false })
    wrapper.unmount()
    const wrapperIndex = mountedWrappers.indexOf(wrapper)
    if (wrapperIndex >= 0) mountedWrappers.splice(wrapperIndex, 1)
    request.resolve(makeJsonResponse([pendingInvitation()]))
    await flushPromises()

    expect(wrapper.exists()).toBe(false)
  })

  it('uses the shared modal dialog and removes a pending invitation only after a 204 receipt', async () => {
    createInvitationMocks.apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([pendingInvitation()]))
      if (url === '/api/invitations/pending/12' && init?.method === 'DELETE') return Promise.resolve(makeJsonResponse({}, true, 204))
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const dialog = await openDeleteDialog(wrapper)
    expect(dialog.attributes('role')).toBe('dialog')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(dialog.text()).toContain('حذف دعوت‌نامه pending-user؟')
    expect(createInvitationMocks.apiFetchMock).not.toHaveBeenCalledWith('/api/invitations/pending/12', expect.anything())

    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(createInvitationMocks.apiFetchMock).toHaveBeenCalledWith('/api/invitations/pending/12', expect.objectContaining({
      method: 'DELETE',
      cache: 'no-store',
      retryNetwork: false,
      trackConnectionState: false,
    }))
    expect(wrapper.text()).not.toContain('pending-user')
    expect(wrapper.get('.pending-notice').text()).toContain('دعوت‌نامه از فهرست حذف شد.')
    expect(hasMountedConfirmDialog()).toBe(false)
  })

  it('returns focus to the pending-delete trigger after Escape or cancel without issuing a delete', async () => {
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([pendingInvitation()]))
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false, attachToBody: true })
    const deleteButton = wrapper.get<HTMLButtonElement>('.delete-pending-btn')

    deleteButton.element.focus()
    await deleteButton.trigger('click')
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    await flushPromises()

    expect(document.activeElement).toBe(deleteButton.element)
    expect(wrapper.text()).toContain('pending-user')
    expect(createInvitationMocks.apiFetchMock.mock.calls.filter(([url]) => url === '/api/invitations/pending/12')).toHaveLength(0)

    await deleteButton.trigger('click')
    await flushPromises()
    const dialog = document.body.querySelector<HTMLElement>('.ui-confirm-dialog')
    if (!dialog) throw new Error('confirm_dialog_not_mounted')
    ;(dialog.querySelector('.ui-button--secondary') as HTMLButtonElement).click()
    await nextTick()
    await flushPromises()

    expect(document.activeElement).toBe(deleteButton.element)
    expect(wrapper.text()).toContain('pending-user')
    expect(createInvitationMocks.apiFetchMock.mock.calls.filter(([url]) => url === '/api/invitations/pending/12')).toHaveLength(0)
  })

  it('does not treat a non-204 success response as a successful revoke', async () => {
    createInvitationMocks.apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([pendingInvitation()]))
      if (url === '/api/invitations/pending/12' && init?.method === 'DELETE') return Promise.resolve(makeJsonResponse({}, true, 200))
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const dialog = await openDeleteDialog(wrapper)
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('pending-user')
    expect(hasMountedConfirmDialog()).toBe(true)
    expect(document.body.textContent).toContain('وضعیت آن از سرور تأیید نشد.')
  })

  it.each([400, 404])('reconciles a terminal %s revoke response with one fresh pending list', async (status) => {
    let pendingCalls = 0
    createInvitationMocks.apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/invitations/pending') {
        pendingCalls += 1
        return Promise.resolve(makeJsonResponse(pendingCalls === 1 ? [pendingInvitation()] : []))
      }
      if (url === '/api/invitations/pending/12' && init?.method === 'DELETE') {
        return Promise.resolve(makeJsonResponse({ detail: 'private lifecycle detail' }, false, status))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const dialog = await openDeleteDialog(wrapper)
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(pendingCalls).toBe(2)
    expect(wrapper.text()).not.toContain('pending-user')
    expect(wrapper.get('.pending-notice').text()).toContain('دعوت‌نامه دیگر در انتظار نیست؛ فهرست به‌روز شد.')
    expect(hasMountedConfirmDialog()).toBe(false)
    expect(wrapper.text()).not.toContain('private lifecycle detail')
  })

  it('clears sensitive pending data on a permission denial without disclosing server policy detail', async () => {
    let pendingCalls = 0
    createInvitationMocks.apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/invitations/pending') {
        pendingCalls += 1
        return Promise.resolve(makeJsonResponse([pendingInvitation()]))
      }
      if (url === '/api/invitations/pending/12' && init?.method === 'DELETE') {
        return Promise.resolve(makeJsonResponse({ detail: 'foreign invitation policy' }, false, 403))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const dialog = await openDeleteDialog(wrapper)
    await dialog.get('.ui-button--danger').trigger('click')
    await flushPromises()

    expect(pendingCalls).toBe(1)
    expect(wrapper.text()).not.toContain('pending-user')
    expect(hasMountedConfirmDialog()).toBe(false)
    expect(wrapper.get('.pending-error').text()).toContain('دسترسی شما به فهرست دعوت‌نامه‌ها تأیید نشد.')
    expect(wrapper.text()).not.toContain('foreign invitation policy')
  })

  it('clears an in-memory create link when a later pending refresh is forbidden', async () => {
    let pendingCalls = 0
    const botLink = 'https://t.me/mbmtrading1_bot?start=INV-forbidden-clear'
    createInvitationMocks.apiFetchMock.mockImplementation((url: string) => {
      if (url === '/api/invitations/pending') {
        pendingCalls += 1
        return Promise.resolve(pendingCalls === 3
          ? makeJsonResponse({ detail: 'foreign invitation policy' }, false, 403)
          : makeJsonResponse([pendingInvitation()]))
      }
      if (url === '/api/invitations/') {
        return Promise.resolve(makeJsonResponse({
          created: true,
          state: 'pending',
          bot_link: botLink,
          bot_available: true,
          web_available: false,
          expires_at: '2026-07-14T10:00:00Z',
        }))
      }
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    await fillInviteForm(wrapper)
    await wrapper.get('form').trigger('submit.prevent')
    await flushPromises()
    expect(wrapper.find('.success-box').exists()).toBe(true)

    await wrapper.get('.pending-refresh-btn').trigger('click')
    await flushPromises()

    expect(wrapper.find('.success-box').exists()).toBe(false)
    expect(wrapper.findAll('.success-box .copy-btn')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('pending-user')
    expect(wrapper.html()).not.toContain(botLink)
    expect(wrapper.get('.pending-error').text()).toContain('دسترسی شما به فهرست دعوت‌نامه‌ها تأیید نشد.')
  })

  it('keeps one controlled confirmation after a generic delete failure and ignores duplicate confirms', async () => {
    const deleteResponse = deferred<ReturnType<typeof makeJsonResponse>>()
    createInvitationMocks.apiFetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/invitations/pending') return Promise.resolve(makeJsonResponse([pendingInvitation()]))
      if (url === '/api/invitations/pending/12' && init?.method === 'DELETE') return deleteResponse.promise
      return Promise.reject(new Error(`Unexpected API call: ${url}`))
    })

    const wrapper = await mountView({}, { clearInitialFetch: false })
    const dialog = await openDeleteDialog(wrapper)
    const confirmButton = dialog.get('.ui-button--danger')
    await confirmButton.trigger('click')
    await confirmButton.trigger('click')
    expect(createInvitationMocks.apiFetchMock.mock.calls.filter(([url]) => url === '/api/invitations/pending/12')).toHaveLength(1)

    deleteResponse.resolve(makeJsonResponse({ detail: 'server detail' }, false, 500))
    await flushPromises()

    expect(wrapper.text()).toContain('pending-user')
    expect(hasMountedConfirmDialog()).toBe(true)
    expect(document.body.textContent).toContain('وضعیت آن از سرور تأیید نشد.')
    expect(document.body.textContent).not.toContain('server detail')
  })
})
