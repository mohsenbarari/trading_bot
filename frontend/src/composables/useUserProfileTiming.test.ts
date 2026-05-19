import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick, toRef, type PropType } from 'vue'
import { mount } from '@vue/test-utils'
import { useUserProfileTiming } from './useUserProfileTiming'

type TimingHarnessUser = {
  trading_restricted_until?: string | null
  limitations_expire_at?: string | null
}

type TimingHarnessVm = {
  countdownRestriction: string
  countdownLimitation: string
  parseJalaliToIranISO: (value: string) => string
  toEnglishDigits: (value: string) => string
}

const TimingHarness = defineComponent({
  props: {
    user: {
      type: Object as PropType<TimingHarnessUser | null>,
      default: null,
    },
  },
  setup(props) {
    return useUserProfileTiming(toRef(props, 'user'))
  },
  template: '<div />',
})

describe('useUserProfileTiming', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('computes live countdown strings for restrictions and limitations', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-08T08:00:00.000Z'))

    const wrapper = mount(TimingHarness, {
      props: {
        user: {
          trading_restricted_until: '2026-05-08T09:01:01.000Z',
          limitations_expire_at: '2026-05-08T08:01:01.000Z',
        },
      },
    })

    await nextTick()
    const vm = wrapper.vm as unknown as TimingHarnessVm
    expect(vm.countdownRestriction).toBe('1 ساعت 1 دقیقه 1 ثانیه')
    expect(vm.countdownLimitation).toBe('1 دقیقه 1 ثانیه')

    vi.advanceTimersByTime(2000)
    await nextTick()
    expect(vm.countdownLimitation).toBe('59 ثانیه')

    wrapper.unmount()
  })

  it('handles permanent restrictions and Jalali/number helper conversions', async () => {
    const wrapper = mount(TimingHarness, {
      props: {
        user: {
          trading_restricted_until: '2201-01-01T00:00:00.000Z',
          limitations_expire_at: null,
        },
      },
    })

    await nextTick()
    const vm = wrapper.vm as unknown as TimingHarnessVm
    expect(vm.countdownRestriction).toBe('دائمی')
    expect(vm.toEnglishDigits('۱۲۳۴۵۶۷۸۹۰')).toBe('1234567890')
    expect(vm.parseJalaliToIranISO('۱۴۰۳/۰۲/۱۰ 08:30')).toBe('2024-04-29T05:00:00.000Z')

    wrapper.unmount()
  })

  it('formats day-long restrictions and clears invalid or empty timing state safely', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-08T08:00:00.000Z'))

    const wrapper = mount(TimingHarness, {
      props: {
        user: {
          trading_restricted_until: '2026-05-10T10:02:00.000Z',
        },
      },
    })

    await nextTick()
    const vm = wrapper.vm as unknown as TimingHarnessVm
    expect(vm.countdownRestriction).toBe('2 روز 2 ساعت 2 دقیقه')
    expect(vm.countdownLimitation).toBe('')

    await wrapper.setProps({
      user: {
        trading_restricted_until: 'invalid-date',
        limitations_expire_at: 'invalid-date',
      },
    })
    await nextTick()
    expect(vm.countdownRestriction).toBe('')
    expect(vm.countdownLimitation).toBe('')

    await wrapper.setProps({ user: null })
    await nextTick()
    expect(vm.countdownRestriction).toBe('')
    expect(vm.countdownLimitation).toBe('')
    expect(vm.toEnglishDigits('')).toBe('')

    wrapper.unmount()
  })
})