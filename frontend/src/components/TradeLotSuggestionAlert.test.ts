import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

async function mountAlert(overrides: Record<string, unknown> = {}) {
  const TradeLotSuggestionAlert = (await import('./TradeLotSuggestionAlert.vue')).default
  return mount(TradeLotSuggestionAlert, {
    props: {
      show: true,
      title: 'پیشنهاد خرید',
      introText: 'این مقدارها هنوز در دسترس هستند.',
      offerType: 'buy',
      offerTypeLabel: 'خرید',
      commodityName: 'سکه',
      price: 52000,
      remainingQuantity: 25,
      lotSummary: '15 + 10',
      availableLots: [15, 10],
      autoCloseSeconds: 3,
      busy: false,
      busyAmount: null,
      ...overrides,
    },
    global: {
      stubs: {
        teleport: true,
        transition: false,
      },
    },
  })
}

describe('TradeLotSuggestionAlert.vue', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('starts the countdown, updates it every second, and auto-closes when idle', async () => {
    const wrapper = await mountAlert({ autoCloseSeconds: 3 })

    expect(wrapper.text()).toContain('بستن خودکار تا 3 ثانیه')

    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).toContain('بستن خودکار تا 2 ثانیه')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
  })

  it('uses the two-tap lot confirmation flow and clears the pending lot when it disappears', async () => {
    const wrapper = await mountAlert()

    const lotButtons = wrapper.findAll('.trade-suggestion-lot-btn')
    expect(lotButtons).toHaveLength(2)

    await lotButtons[0]!.trigger('click')
    expect(wrapper.text()).toContain('تایید 15 عدد؟')
    expect(wrapper.emitted('select-lot')).toBeUndefined()

    await wrapper.setProps({ availableLots: [10] })
    expect(wrapper.text()).not.toContain('تایید 15 عدد؟')

    const updatedButtons = wrapper.findAll('.trade-suggestion-lot-btn')
    await updatedButtons[0]!.trigger('click')
    expect(wrapper.text()).toContain('تایید 10 عدد؟')

    await updatedButtons[0]!.trigger('click')
    expect(wrapper.emitted('select-lot')).toEqual([[10]])

    wrapper.unmount()
  })

  it('pauses auto-close while busy, ignores lot clicks, and resumes once the busy state clears', async () => {
    const wrapper = await mountAlert({ busy: true, busyAmount: 10, autoCloseSeconds: 2 })

    expect(wrapper.text()).toContain('در حال ارسال...')

    const lotButtons = wrapper.findAll('.trade-suggestion-lot-btn')
    await lotButtons[0]!.trigger('click')
    expect(wrapper.emitted('select-lot')).toBeUndefined()

    await vi.advanceTimersByTimeAsync(4000)
    expect(wrapper.emitted('close')).toBeUndefined()

    await wrapper.setProps({ busy: false, busyAmount: null })
    expect(wrapper.text()).toContain('بستن خودکار تا 2 ثانیه')

    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
  })

  it('clears the two-tap pending selection after timeout and hides pending confirmation', async () => {
    const wrapper = await mountAlert({ autoCloseSeconds: 8 })

    const lotButton = wrapper.findAll('.trade-suggestion-lot-btn')[0]
    await lotButton!.trigger('click')
    expect(wrapper.text()).toContain('تایید 15 عدد؟')

    await vi.advanceTimersByTimeAsync(3000)
    expect(wrapper.text()).not.toContain('تایید 15 عدد؟')

    wrapper.unmount()
  })

  it('clears countdown timers when the alert becomes busy and resets pending on hide', async () => {
    const wrapper = await mountAlert({ autoCloseSeconds: 5 })

    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).toContain('بستن خودکار تا 4 ثانیه')

    await wrapper.findAll('.trade-suggestion-lot-btn')[0]!.trigger('click')
    expect(wrapper.text()).toContain('تایید 15 عدد؟')

    await wrapper.setProps({ busy: true, busyAmount: 15 })
    await vi.advanceTimersByTimeAsync(5000)
    expect(wrapper.emitted('close')).toBeUndefined()

    await wrapper.setProps({ show: false })
    expect(wrapper.text()).not.toContain('تایید 15 عدد؟')

    wrapper.unmount()
  })
})