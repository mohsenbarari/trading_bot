import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UserManager from './UserManager.vue'

const userManagerSource = readFileSync(
  resolve(process.cwd(), 'src/components/UserManager.vue'),
  'utf8',
)

const userManagerMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: userManagerMocks.apiFetchMock,
}))

function makeJsonResponse(payload: unknown, ok = true, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    statusText: '',
    headers: {
      get: () => 'application/json',
    },
    json: async () => payload,
  }
}

function makeUser(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    full_name: 'Ali Reza',
    telegram_id: 100,
    account_name: 'alireza',
    role: 'عادی',
    mobile_number: '09123456789',
    ...overrides,
  }
}

async function mountView(props: Record<string, unknown> = {}) {
  const wrapper = mount(UserManager, {
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
      ...props,
    },
    global: {
      stubs: {
        LoadingSkeleton: {
          template: '<div class="loading-skeleton-stub">loading</div>',
          props: ['count', 'height'],
        },
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('UserManager.vue', () => {
  beforeEach(() => {
    userManagerMocks.apiFetchMock.mockReset()
  })

  it('loads users into shared list-item rows and emits the selected profile navigation payload', async () => {
    const user = makeUser({ account_status: 'inactive' })
    const customer = makeUser({
      id: 2,
      account_name: 'customer_raw',
      customer_management_name: 'مشتری بازار',
      is_customer: true,
      customer_owner_account_name: 'owner_a',
    })
    const accountant = makeUser({
      id: 3,
      account_name: 'accountant_raw',
      is_accountant: true,
      accountant_owner_account_name: 'owner_b',
    })
    userManagerMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([user, customer, accountant]))

    const wrapper = await mountView()

    expect(userManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/', expect.objectContaining({
      retryNetwork: false,
      trackConnectionState: false,
    }))
    expect(wrapper.text()).toContain('alireza')
    expect(wrapper.text()).toContain('مشتری بازار')
    expect(wrapper.text()).toContain('مشتری')
    expect(wrapper.text()).toContain('حسابدار')
    expect(wrapper.text()).toContain('سرگروه: owner_a')
    expect(wrapper.text()).toContain('سرگروه: owner_b')
    expect(wrapper.text()).toContain('09123456789')
    expect(wrapper.get('.user-account-status').text()).toBe('حساب غیرفعال')

    const row = wrapper.get('.users-list > li > button.user-item')
    expect(row.element.tagName).toBe('BUTTON')
    expect(row.classes()).toContain('ui-list-item')
    expect(row.classes()).toContain('ui-list-item--interactive')
    expect(row.attributes('type')).toBe('button')
    expect(row.attributes('aria-label')).toContain('alireza')

    await row.trigger('click')

    expect(wrapper.emitted('navigate')).toEqual([['user_profile', user]])
    expect(wrapper.emitted('loaded')).toEqual([[]])
    expect(wrapper.emitted('settled')).toEqual([[]])
  })

  it('keeps search persistent and commits its trimmed query only on form submit', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse([]))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 2, account_name: 'ali-search' })]))

    const wrapper = await mountView()

    expect(wrapper.find('.search-toggle-btn').exists()).toBe(false)
    expect(wrapper.get('.user-search-form').exists()).toBe(true)
    expect(wrapper.get('.user-search-input').classes()).toContain('ui-input')
    expect(wrapper.get('.search-submit-btn').classes()).toContain('ui-button')
    expect(wrapper.get('label[for="user-directory-search"]').text()).toBe('جستجوی کاربر')
    expect(wrapper.get('input#user-directory-search').exists()).toBe(true)

    await wrapper.get('input').setValue(' ali search ')
    expect(userManagerMocks.apiFetchMock).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('query-change')).toBeUndefined()

    await wrapper.get('.user-search-form').trigger('submit')
    await flushPromises()

    expect(userManagerMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/?search=ali%20search', expect.any(Object))
    expect(wrapper.emitted('query-change')).toEqual([['ali search']])
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('ali search')
    expect(wrapper.text()).toContain('ali-search')
  })

  it('reflows constrained directory rows by their local list width, with a narrow-viewport fallback', async () => {
    userManagerMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([
      makeUser({
        account_name: 'directory-user-with-a-deliberately-long-name',
        account_status: 'inactive',
        role: 'مدیر ارشد',
        customer_owner_account_name: 'owner-with-a-deliberately-long-account-name',
      }),
    ]))

    const wrapper = await mountView()
    const row = wrapper.get('.users-list > li > button.user-item')

    expect(row.get('.ui-list-item__leading').exists()).toBe(true)
    expect(row.get('.ui-list-item__copy').exists()).toBe(true)
    expect(row.get('.ui-list-item__trailing').exists()).toBe(true)
    expect(row.get('.user-name').text()).toContain('directory-user-with-a-deliberately-long-name')
    expect(row.get('.user-meta').text()).toContain('حساب غیرفعال')
    expect(row.get('.role-badge').text()).toContain('مدیر ارشد')
    expect(userManagerSource).toMatch(
      /\.users-list\s*\{[\s\S]*?container:\s*user-directory\s*\/\s*inline-size;/,
    )
    expect(userManagerSource).toMatch(
      /@container user-directory \(max-width:\s*34rem\)\s*\{[\s\S]*?grid-template-columns:\s*var\(--ds-native-row-min-height, 48px\) minmax\(0, 1fr\);[\s\S]*?grid-template-areas:\s*'leading copy'\s*'leading trailing';/,
    )
    expect(userManagerSource).toMatch(
      /@supports not \(container-type:\s*inline-size\)\s*\{[\s\S]*?@media \(max-width:\s*480px\)\s*\{[\s\S]*?grid-template-areas:\s*'leading copy'\s*'leading trailing';/,
    )
    expect(userManagerSource).toMatch(
      /\.user-item :deep\(\.ui-list-item__copy > span\),\s*\.user-name\s*\{[\s\S]*?text-overflow:\s*ellipsis;[\s\S]*?white-space:\s*nowrap;/,
    )
  })

  it('keeps the directory typography locally aligned with the Figma Persian card scale', () => {
    expect(userManagerSource).toMatch(
      /\.user-manager\s*\{[\s\S]*?font-family:\s*Vazirmatn,\s*Tahoma,\s*Arial,\s*sans-serif;[\s\S]*?font-synthesis:\s*none;/,
    )
    expect(userManagerSource).toMatch(
      /\.user-item :deep\(\.ui-list-item__copy > span\)\s*\{[\s\S]*?font-family:\s*var\(--ds-font-mono\);/,
    )
    expect(userManagerSource).toMatch(
      /\.user-name\s*\{[\s\S]*?font-size:\s*14px;[\s\S]*?font-weight:\s*600;[\s\S]*?line-height:\s*24px;/,
    )
  })

  it('clears a committed search only from the explicit clear action and reloads the base list', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ account_name: 'base-user' })]))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 2, account_name: 'searched-user' })]))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 3, account_name: 'base-user-again' })]))

    const wrapper = await mountView()
    await wrapper.get('input').setValue(' searched-user ')
    await wrapper.get('.user-search-form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('.user-search-clear').exists()).toBe(true)
    await wrapper.get('.user-search-clear').trigger('click')
    await flushPromises()

    expect(userManagerMocks.apiFetchMock).toHaveBeenNthCalledWith(3, '/api/users/', expect.any(Object))
    expect(wrapper.emitted('query-change')).toEqual([['searched-user'], ['']])
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('base-user-again')
  })

  it('normalizes an incoming query prop, reloads on later prop changes, and does not emit a route change itself', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ account_name: 'initial-user' })]))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 2, account_name: 'next-user' })]))

    const wrapper = await mountView({ query: ' initial ' })

    expect(userManagerMocks.apiFetchMock).toHaveBeenNthCalledWith(1, '/api/users/?search=initial', expect.any(Object))
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('initial')
    expect(wrapper.emitted('query-change')).toBeUndefined()

    await wrapper.setProps({ query: ' next ' })
    await flushPromises()

    expect(userManagerMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/?search=next', expect.any(Object))
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('next')
    expect(wrapper.text()).toContain('next-user')
    expect(wrapper.emitted('query-change')).toBeUndefined()
  })

  it('shows a friendly empty state and emits loaded for an accepted empty payload', async () => {
    userManagerMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([]))

    const wrapper = await mountView()

    expect(wrapper.find('.no-results').exists()).toBe(true)
    expect(wrapper.get('.no-results').classes()).toContain('ui-empty-state')
    expect(wrapper.text()).toContain('کاربری یافت نشد.')
    expect(wrapper.emitted('loaded')).toEqual([[]])
    expect(wrapper.emitted('settled')).toEqual([[]])
  })

  it('surfaces an initial failure with a same-screen retry that can recover', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse({ detail: 'boom' }, false))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ account_name: 'retry-user' })]))

    const wrapper = await mountView()

    const errorState = wrapper.get('.ds-message.danger')
    expect(errorState.classes()).toContain('ui-empty-state')
    expect(errorState.attributes('role')).toBe('alert')
    expect(errorState.text()).toContain('دریافت کاربران ممکن نشد. دوباره تلاش کنید.')
    expect(wrapper.emitted('settled')).toEqual([[]])

    await wrapper.get('.user-load-retry').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('retry-user')
    expect(wrapper.find('.user-initial-error').exists()).toBe(false)
    expect(wrapper.emitted('loaded')).toEqual([[]])
    expect(wrapper.emitted('settled')).toEqual([[], []])
  })

  it('retains rows and the committed query when a search refresh fails', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ account_name: 'existing-user' })]))
      .mockResolvedValueOnce(makeJsonResponse({ detail: 'search down' }, false))

    const wrapper = await mountView()
    await wrapper.get('input').setValue(' existing ')
    await wrapper.get('.user-search-form').trigger('submit')
    await flushPromises()

    expect(wrapper.text()).toContain('existing-user')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('existing')
    expect(wrapper.get('.user-query-stale-notice').text()).toContain('نتایج فعلی مربوط به جست‌وجوی قبلی هستند.')
    expect(wrapper.get('.user-refresh-error').text()).toContain('دریافت کاربران ممکن نشد. دوباره تلاش کنید.')
    expect(wrapper.find('.no-results').exists()).toBe(false)
    expect(wrapper.emitted('query-change')).toEqual([['existing']])
  })

  it('clears retained rows and presents an explicit permission state when list access is denied', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ account_name: 'previous-user' })]))
      .mockResolvedValueOnce(makeJsonResponse({ detail: 'forbidden' }, false, 403))

    const wrapper = await mountView()
    await wrapper.setProps({ query: 'blocked' })
    await flushPromises()

    expect(wrapper.get('.user-initial-error').text()).toContain('دسترسی به فهرست کاربران مجاز نیست')
    expect(wrapper.text()).toContain('مجوز مشاهده فهرست کاربران را ندارید.')
    expect(wrapper.text()).not.toContain('previous-user')
    expect(wrapper.find('.users-list').exists()).toBe(false)
  })

  it('aborts and stale-gates an outdated prop-driven request while retaining the latest result', async () => {
    let resolveStale: ((value: ReturnType<typeof makeJsonResponse>) => void) | undefined
    let staleSignal: AbortSignal | undefined
    const staleResponse = new Promise<ReturnType<typeof makeJsonResponse>>((resolve) => {
      resolveStale = resolve
    })

    userManagerMocks.apiFetchMock
      .mockImplementationOnce((_url: string, options: RequestInit) => {
        staleSignal = options.signal as AbortSignal
        return staleResponse
      })
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 3, account_name: 'latest-user' })]))

    const wrapper = await mountView({ query: 'stale' })
    await wrapper.setProps({ query: 'latest' })
    await flushPromises()

    expect(staleSignal?.aborted).toBe(true)
    expect(userManagerMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/?search=latest', expect.any(Object))
    expect(wrapper.text()).toContain('latest-user')

    resolveStale!(makeJsonResponse([makeUser({ id: 2, account_name: 'stale-user' })]))
    await flushPromises()

    expect(wrapper.text()).toContain('latest-user')
    expect(wrapper.text()).not.toContain('stale-user')
    expect(wrapper.emitted('loaded')).toEqual([[]])
    expect(wrapper.emitted('settled')).toEqual([[]])
  })
})
