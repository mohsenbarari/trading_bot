import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import moment from 'moment-jalaali'
import JalaliDatePicker from './JalaliDatePicker.vue'

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
    const clearButton = wrapper.findAll('.jalali-calendar-link').find((node) => node.text().includes('پاک کردن'))
    expect(clearButton).toBeTruthy()
    await clearButton!.trigger('click')

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([''])
    expect(wrapper.emitted('change')?.at(-1)).toEqual([''])
  })
})