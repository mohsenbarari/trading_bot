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

describe('UserProfile.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    vi.spyOn(window, 'alert').mockImplementation(() => {})
    vi.spyOn(window, 'confirm').mockImplementation(() => true)
  })

  it('allows admin to update max accountants for an owner', async () => {
    const user = {
      id: 12,
      account_name: 'owner12',
      mobile_number: '09120000000',
      role: 'عادی',
      has_bot_access: true,
      can_block_users: true,
      max_blocked_users: 10,
      trading_restricted_until: null,
      max_daily_trades: null,
      max_active_commodities: null,
      max_daily_requests: null,
      limitations_expire_at: null,
      trades_count: 0,
      commodities_traded_count: 0,
      channel_messages_count: 0,
      max_sessions: 2,
      max_accountants: 3,
    }

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
    const user = {
      id: 15,
      account_name: 'owner15',
      mobile_number: '09123334444',
      role: 'عادی',
      has_bot_access: true,
      can_block_users: true,
      max_blocked_users: 10,
      trading_restricted_until: null,
      max_daily_trades: null,
      max_active_commodities: null,
      max_daily_requests: null,
      limitations_expire_at: null,
      trades_count: 0,
      commodities_traded_count: 0,
      channel_messages_count: 0,
      max_sessions: 2,
      max_accountants: 3,
    }

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
})