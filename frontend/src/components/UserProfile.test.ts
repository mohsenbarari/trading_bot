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
})