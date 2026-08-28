import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'
import moment from 'moment-jalaali'
import JalaliDatePicker from './JalaliDatePicker.vue'

const protectedTradingSettingsSource = readFileSync(
  resolve(process.cwd(), 'src/components/TradingSettings.vue'),
  'utf8',
)
const stage7ConsumerSources = [
  readFileSync(resolve(process.cwd(), 'src/components/UserProfile.vue'), 'utf8'),
  readFileSync(resolve(process.cwd(), 'src/components/PublicProfile.vue'), 'utf8'),
]

function jalaliDatePickerTags(source: string) {
  return [...source.matchAll(/<JalaliDatePicker\b[^>]*>/g)].map((match) => match[0])
}

function findDay(wrapper: ReturnType<typeof mount>, label: string) {
  const day = wrapper
    .findAll('.jalali-calendar-day')
    .find((node) => node.text() === label && !node.attributes('disabled'))
  expect(day).toBeTruthy()
  return day!
}

describe('JalaliDatePicker.vue', () => {
  it('renders a Gregorian model as Jalali text and emits Gregorian values', async () => {
    const wrapper = mount(JalaliDatePicker, {
      props: {
        modelValue: '2026-05-30',
        valueType: 'gregorian',
        triggerTestId: 'date-trigger',
      },
    })

    expect(wrapper.get('[data-testid="date-trigger"]').text()).toContain('۹ خرداد ۱۴۰۵')

    await wrapper.get('[data-testid="date-trigger"]').trigger('click')
    await findDay(wrapper, '۱۰').trigger('click')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([
      moment('1405/03/10', 'jYYYY/jMM/jDD').format('YYYY-MM-DD'),
    ])
    expect(wrapper.emitted('change')?.at(-1)).toEqual([
      moment('1405/03/10', 'jYYYY/jMM/jDD').format('YYYY-MM-DD'),
    ])
  })

  it('supports inline Jalali model values without converting the output format', async () => {
    const wrapper = mount(JalaliDatePicker, {
      props: {
        modelValue: '1405/03/09',
        valueType: 'jalali',
        inline: true,
      },
    })

    expect(wrapper.get('.jalali-calendar-title').attributes('aria-label')).toBe('خرداد ۱۴۰۵')
    await findDay(wrapper, '۱۰').trigger('click')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['1405/03/10'])
  })

  it('lets users jump directly by selecting Jalali month and year', async () => {
    const wrapper = mount(JalaliDatePicker, {
      props: {
        modelValue: '2026-05-30',
        valueType: 'gregorian',
        inline: true,
      },
    })

    const selects = wrapper.findAll('.jalali-calendar-select')
    expect(selects).toHaveLength(2)

    await selects[0]!.setValue('6')
    await selects[1]!.setValue('1410')

    expect(wrapper.text()).toContain('مهر')
    expect(wrapper.text()).toContain('۱۴۱۰')

    await findDay(wrapper, '۱').trigger('click')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([
      moment('1410/07/01', 'jYYYY/jMM/jDD').format('YYYY-MM-DD'),
    ])
  })

  it('clears the current value from the footer action', async () => {
    const wrapper = mount(JalaliDatePicker, {
      props: {
        modelValue: '2026-05-30',
        valueType: 'gregorian',
      },
    })

    await wrapper.get('.jalali-date-trigger').trigger('click')
    const clearButton = wrapper
      .findAll('.jalali-calendar-link')
      .find((node) => node.text().includes('پاک کردن'))
    expect(clearButton).toBeTruthy()
    await clearButton!.trigger('click')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([''])
    expect(wrapper.emitted('change')?.at(-1)).toEqual([''])
  })

  it('leaves arrow keys untouched by default for protected legacy consumers', async () => {
    const wrapper = mount(JalaliDatePicker, {
      props: {
        modelValue: '1405/03/09',
        valueType: 'jalali',
        inline: true,
      },
      attachTo: document.body,
    })

    const selectedDay = findDay(wrapper, '۹')
    ;(selectedDay.element as HTMLElement).focus()
    const event = new KeyboardEvent('keydown', {
      key: 'ArrowLeft',
      bubbles: true,
      cancelable: true,
    })
    selectedDay.element.dispatchEvent(event)
    await nextTick()

    expect(event.defaultPrevented).toBe(false)
    expect(document.activeElement).toBe(selectedDay.element)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it('moves calendar-day focus with arrow keys only when explicitly enabled', async () => {
    const wrapper = mount(JalaliDatePicker, {
      props: {
        modelValue: '2026-05-30',
        valueType: 'gregorian',
        inline: true,
        arrowKeyNavigation: true,
      },
      attachTo: document.body,
    })

    const selectedDay = findDay(wrapper, '۹')
    ;(selectedDay.element as HTMLElement).focus()
    await selectedDay.trigger('keydown', { key: 'ArrowLeft' })

    expect(document.activeElement).toBe(findDay(wrapper, '۱۰').element)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it('moves focus across a Jalali month boundary when arrow navigation is enabled', async () => {
    const wrapper = mount(JalaliDatePicker, {
      props: {
        modelValue: '1405/03/01',
        valueType: 'jalali',
        inline: true,
        arrowKeyNavigation: true,
      },
      attachTo: document.body,
    })

    const selectedDay = findDay(wrapper, '۱')
    ;(selectedDay.element as HTMLElement).focus()
    await selectedDay.trigger('keydown', { key: 'ArrowRight' })
    await nextTick()
    await nextTick()

    expect(wrapper.get('.jalali-calendar-title').attributes('aria-label')).toBe('اردیبهشت ۱۴۰۵')
    expect((document.activeElement as HTMLElement | null)?.dataset.dayKey).toBe('1405/02/31')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps the protected TradingSettings dependency on the inert default and opts in Stage 7 consumers', () => {
    const protectedTags = jalaliDatePickerTags(protectedTradingSettingsSource)
    expect(protectedTags).toHaveLength(1)
    for (const tag of protectedTags) {
      expect(tag).not.toContain('arrow-key-navigation')
    }

    const stage7Tags = stage7ConsumerSources.flatMap(jalaliDatePickerTags)
    expect(stage7Tags).toHaveLength(4)
    for (const tag of stage7Tags) {
      expect(tag).toContain('arrow-key-navigation')
    }
  })
})
