import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UserManager from './UserManager.vue'

const userManagerMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: userManagerMocks.apiFetchMock,
}))

function makeJsonResponse(payload: unknown, ok = true) {
  return {
    ok,
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

async function mountView() {
  const wrapper = mount(UserManager, {
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
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

  it('loads users on mount and emits the selected user profile navigation payload', async () => {
    const user = makeUser()
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

    expect(userManagerMocks.apiFetchMock).toHaveBeenCalledWith('/api/users/')
    expect(wrapper.text()).toContain('alireza')
    expect(wrapper.text()).toContain('مشتری بازار')
    expect(wrapper.text()).toContain('مشتری')
    expect(wrapper.text()).toContain('حسابدار')
    expect(wrapper.text()).toContain('سرگروه: owner_a')
    expect(wrapper.text()).toContain('سرگروه: owner_b')
    expect(wrapper.text()).toContain('09123456789')

    await wrapper.get('.user-item').trigger('click')

    expect(wrapper.emitted('navigate')).toEqual([['user_profile', user]])
  })

  it('opens search mode and fetches users with the trimmed encoded search query on enter', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse([]))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 2, account_name: 'ali-search' })]))

    const wrapper = await mountView()

    await wrapper.get('.search-toggle-btn').trigger('click')
    expect(wrapper.get('.search-toggle-btn').classes()).toContain('ui-button')
    expect(wrapper.get('.user-search-input').classes()).toContain('ui-input')
    expect(wrapper.get('.search-submit-btn').classes()).toContain('ui-button')

    await wrapper.get('input').setValue(' ali search ')
    await wrapper.get('input').trigger('keyup.enter')
    await flushPromises()

    expect(userManagerMocks.apiFetchMock).toHaveBeenNthCalledWith(2, '/api/users/?search=ali%20search')
    expect(wrapper.text()).toContain('ali-search')
  })

  it('closing search clears the query and refetches the unfiltered list', async () => {
    userManagerMocks.apiFetchMock
      .mockResolvedValueOnce(makeJsonResponse([makeUser()]))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 3, account_name: 'searched-user' })]))
      .mockResolvedValueOnce(makeJsonResponse([makeUser({ id: 4, account_name: 'base-user' })]))

    const wrapper = await mountView()

    await wrapper.get('.search-toggle-btn').trigger('click')
    const searchInput = wrapper.get('input')
    await searchInput.setValue('searched-user')
    await wrapper.get('.search-submit-btn').trigger('click')
    await flushPromises()

    await wrapper.get('.search-toggle-btn').trigger('click')
    await flushPromises()

    expect(userManagerMocks.apiFetchMock).toHaveBeenNthCalledWith(3, '/api/users/')
    await wrapper.get('.search-toggle-btn').trigger('click')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('base-user')
  })

  it('shows a friendly empty state when no users are returned', async () => {
    userManagerMocks.apiFetchMock.mockResolvedValue(makeJsonResponse([]))

    const wrapper = await mountView()

    expect(wrapper.find('.no-results').exists()).toBe(true)
    expect(wrapper.get('.no-results').classes()).toContain('ui-empty-state')
    expect(wrapper.text()).toContain('کاربری یافت نشد.')
  })

  it('surfaces API failures as a danger message', async () => {
    userManagerMocks.apiFetchMock.mockResolvedValue(makeJsonResponse({ detail: 'boom' }, false))

    const wrapper = await mountView()

    const errorState = wrapper.get('.ds-message.danger')
    expect(errorState.classes()).toContain('ui-empty-state')
    expect(errorState.attributes('role')).toBe('alert')
    expect(errorState.text()).toContain('خطا در دریافت لیست کاربران')
  })
})
