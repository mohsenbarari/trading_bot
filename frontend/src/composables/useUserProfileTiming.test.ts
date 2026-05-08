import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { useUserProfileTiming } from './useUserProfileTiming'

const TimingHarness = defineComponent({
  props: {
    user: {
      type: Object,
      default: null,
    },
  },
  setup(props) {
    return useUserProfileTiming({
      get value() {
        return props.user as any
      },
      set value(_value) {
        // read-only prop-backed ref for the composable under test
      },
    })
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
    expect((wrapper.vm as any).countdownRestriction).toBe('1 ساعت 1 دقیقه 1 ثانیه')
    expect((wrapper.vm as any).countdownLimitation).toBe('1 دقیقه 1 ثانیه')

    vi.advanceTimersByTime(2000)
    await nextTick()
    expect((wrapper.vm as any).countdownLimitation).toBe('59 ثانیه')

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
    expect((wrapper.vm as any).countdownRestriction).toBe('دائمی')
    expect((wrapper.vm as any).toEnglishDigits('۱۲۳۴۵۶۷۸۹۰')).toBe('1234567890')
    expect((wrapper.vm as any).parseJalaliToIranISO('۱۴۰۳/۰۲/۱۰ 08:30')).toBe('2024-04-29T05:00:00.000Z')

    wrapper.unmount()
  })
})