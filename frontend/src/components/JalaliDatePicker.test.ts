import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import moment from 'moment-jalaali'
import { parse } from 'postcss'
import JalaliDatePicker from './JalaliDatePicker.vue'

const pickerSource = readFileSync(resolve(process.cwd(), 'src/components/JalaliDatePicker.vue'), 'utf8')
const pickerStyles = parse(pickerSource.match(/<style scoped>([\s\S]*?)<\/style>/)![1]!)

function calendarDeclaration(selector: string, property: string, narrow = false) {
  let value = ''
  pickerStyles.walkRules(selector, (rule) => {
    if ((rule.parent?.type === 'atrule') !== narrow) return
    rule.walkDecls(property, (declaration) => { value = declaration.value })
  })
  return value
}

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
  it('budgets seven 40px popover targets independently of a narrow trigger', () => {
    const popover = '.jalali-calendar-panel.is-popover'
    expect(calendarDeclaration(popover, 'width')).toBe('min(360px, calc(100vw - 16px))')
    expect(calendarDeclaration(popover, 'box-sizing')).toBe('border-box')
    expect(calendarDeclaration(popover, 'left')).toBe('50%')
    expect(calendarDeclaration(popover, 'right')).toBe('auto')
    expect(calendarDeclaration(popover, 'transform')).toBe('translateX(calc(-50% + var(--jalali-popover-shift, 0px)))')
    expect(calendarDeclaration(`${popover} .jalali-calendar-day`, 'min-width')).toBe('40px')
    expect(calendarDeclaration(`${popover} .jalali-calendar-day`, 'width')).toBe('100%')

    // This is a production-CSS budget contract, not a jsdom layout assertion.
    // The E2E picker helper checks actual browser rectangles and hit targets.
    for (const viewport of [320, 360, 375, 414, 1280]) {
      const panelWidth = Math.min(360, viewport - 16)
      const padding = Number.parseFloat(calendarDeclaration(
        viewport <= 360 ? popover : '.jalali-calendar-panel', 'padding', viewport <= 360,
      ))
      const gap = Number.parseFloat(calendarDeclaration(
        viewport <= 360
          ? `${popover} .jalali-calendar-weekdays,\n  ${popover} .jalali-calendar-grid`
          : '.jalali-calendar-weekdays,\n.jalali-calendar-grid', 'gap', viewport <= 360,
      ))
      expect((panelWidth - 2 * padding - 2 - 6 * gap) / 7).toBeGreaterThanOrEqual(40)
    }
  })

  it.each([
    { viewport: 320, left: 8, width: 140, panelWidth: 304 },
    { viewport: 414, left: 8, width: 170, panelWidth: 360 },
    { viewport: 414, left: 236, width: 170, panelWidth: 360 },
    { viewport: 1280, left: 430, width: 170, panelWidth: 360 },
  ])('clamps a $viewport px popover using its actual anchor, including after resize', async (geometry) => {
    vi.stubGlobal('innerWidth', geometry.viewport)
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function () {
      return this.classList.contains('jalali-date-picker')
        ? { left: geometry.left, width: geometry.width } as DOMRect
        : { width: geometry.panelWidth } as DOMRect
    })
    const wrapper = mount(JalaliDatePicker, { props: { modelValue: '1405/08/02', valueType: 'jalali' } })
    try {
      await wrapper.get('.jalali-date-trigger').trigger('click')
      await nextTick()
      const panel = wrapper.get('.jalali-calendar-panel').element as HTMLElement
      const centeredLeft = geometry.left + (geometry.width - geometry.panelWidth) / 2
      const actualLeft = () => centeredLeft + Number.parseFloat(panel.style.getPropertyValue('--jalali-popover-shift'))
      expect(actualLeft()).toBeGreaterThanOrEqual(8)
      expect(actualLeft() + geometry.panelWidth).toBeLessThanOrEqual(geometry.viewport - 8)

      vi.stubGlobal('innerWidth', geometry.viewport + 100)
      window.dispatchEvent(new Event('resize'))
      await nextTick()
      expect(actualLeft()).toBeGreaterThanOrEqual(8)
      expect(actualLeft() + geometry.panelWidth).toBeLessThanOrEqual(geometry.viewport + 100 - 8)
    } finally {
      wrapper.unmount()
      rect.mockRestore()
      vi.unstubAllGlobals()
    }
  })

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
