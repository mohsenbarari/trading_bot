import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import JalaliDatePicker from './JalaliDatePicker.vue'

const tradingSettingsMocks = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('../utils/auth', () => ({
  apiFetch: tradingSettingsMocks.apiFetchMock,
}))

function responseOf(data: unknown, ok = true, status = ok ? 200 : 400) {
  return {
    ok,
    status,
    json: async () => data,
  }
}

const defaultSettings = {
  invitation_expiry_days: 2,
  offer_expiry_minutes: 2,
  offer_min_quantity: 5,
  offer_max_quantity: 50,
  max_active_offers: 4,
  competitive_price_validation_enabled: false,
  offer_price_warning_enabled: true,
  offer_expire_rate_per_minute: 2,
  offer_expire_daily_limit_after_threshold: 10,
  anti_abuse_daily_base: 2,
  anti_abuse_weekly_base: 5,
  anti_abuse_monthly_base: 7,
  market_schedule_enabled: false,
  market_timezone: 'Asia/Tehran',
  market_open_time_local: '09:00',
  market_close_time_local: '18:00',
  market_closed_weekdays: [],
}

const loadedSettings = {
  invitation_expiry_days: 3,
  offer_expiry_minutes: 15,
  offer_min_quantity: 10,
  offer_max_quantity: 120,
  max_active_offers: 8,
  competitive_price_validation_enabled: false,
  offer_price_warning_enabled: true,
  offer_expire_rate_per_minute: 4,
  offer_expire_daily_limit_after_threshold: 21,
  anti_abuse_daily_base: 6,
  anti_abuse_weekly_base: 9,
  anti_abuse_monthly_base: 12,
  market_schedule_enabled: true,
  market_timezone: 'Asia/Tehran',
  market_open_time_local: '10:00',
  market_close_time_local: '17:00',
  market_closed_weekdays: [4],
}

const marketState = {
  is_open: false,
  active_web_notice_visible: true,
  offers_since_last_open: 1,
  last_transition_at: null,
  next_transition_at: '2026-05-22T10:30:00Z',
}

let overrideRecords: Array<any> = []

async function mountTradingSettings() {
  const TradingSettings = (await import('./TradingSettings.vue')).default
  return mount(TradingSettings, {
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
    },
  })
}

async function setJalaliDate(wrapper: Awaited<ReturnType<typeof mountTradingSettings>>, value: string) {
  const picker = wrapper.findComponent(JalaliDatePicker)
  expect(picker.exists()).toBe(true)
  picker.vm.$emit('update:modelValue', value)
  picker.vm.$emit('change', value)
  await flushPromises()
}

describe('TradingSettings.vue', () => {
  beforeEach(() => {
    tradingSettingsMocks.apiFetchMock.mockReset()
    vi.stubGlobal('confirm', vi.fn(() => true))
    overrideRecords = [
      {
        id: 1,
        date: '2026-05-24',
        override_type: 'closed_all_day',
        open_time_local: null,
        close_time_local: null,
        note: 'تعطیلی رسمی',
      },
      {
        id: 2,
        date: '2026-05-25',
        override_type: 'custom_hours',
        open_time_local: '11:00',
        close_time_local: '14:00',
        note: 'شیفت کوتاه',
      },
    ]
    tradingSettingsMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/trading-settings/' && method === 'GET') {
        return responseOf(loadedSettings)
      }
      if (path === '/api/trading-settings/market-state' && method === 'GET') {
        return responseOf(marketState)
      }
      if (path === '/api/trading-settings/market-overrides' && method === 'GET') {
        return responseOf(overrideRecords)
      }
      if (path === '/api/trading-settings/market-overrides' && method === 'POST') {
        const body = JSON.parse(options?.body as string)
        const created = { id: overrideRecords.length + 10, ...body }
        overrideRecords = [...overrideRecords, created]
        return responseOf(created)
      }
      const overrideMatch = path.match(/^\/api\/trading-settings\/market-overrides\/(\d+)$/)
      if (overrideMatch && method === 'PUT') {
        const overrideId = Number(overrideMatch[1])
        const body = JSON.parse(options?.body as string)
        const updated = { id: overrideId, ...body }
        overrideRecords = overrideRecords.map((item) => (item.id === overrideId ? updated : item))
        return responseOf(updated)
      }
      if (overrideMatch && method === 'DELETE') {
        const overrideId = Number(overrideMatch[1])
        overrideRecords = overrideRecords.filter((item) => item.id !== overrideId)
        return responseOf({ success: true })
      }
      if (path === '/api/trading-settings/' && method === 'PUT') {
        return responseOf(JSON.parse(options?.body as string))
      }
      if (path === '/api/trading-settings/reset' && method === 'POST') {
        return responseOf(defaultSettings)
      }
      return responseOf({ detail: 'unhandled path' }, false, 500)
    })
  })

  it('loads settings, opens accordion sections, and marks inputs as default when drafts match defaults', async () => {
    const wrapper = await mountTradingSettings()
    await flushPromises()

    expect(tradingSettingsMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/', { method: 'GET' })
    expect(tradingSettingsMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/market-state', { method: 'GET' })
    expect(tradingSettingsMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/market-overrides', { method: 'GET' })
    expect(wrapper.text()).toContain('امنیت و نشست‌ها')
    expect(wrapper.text()).toContain('برنامه بازار')

    const headers = wrapper.findAll('.settings-section__header')
    expect(headers).toHaveLength(6)
    expect(headers[0]!.classes()).toContain('ui-button')
    expect(headers[5]!.classes()).toContain('ui-button')
    await headers[0]!.trigger('click')
    await headers[3]!.trigger('click')
    await headers[4]!.trigger('click')
    await headers[5]!.trigger('click')
    await flushPromises()

    const accordions = wrapper.findAll('.settings-section')
    expect(accordions[0]!.classes()).toContain('open')
    expect(accordions[3]!.classes()).toContain('open')
    expect(wrapper.get('[data-testid="market-state-card"]').text()).toContain('بسته')
    expect(wrapper.get('[data-testid="market-state-card"]').text()).toContain('یادآور فعال')
    expect(wrapper.findAll('[data-testid="market-override-row"]').length).toBe(2)

    const inputs = wrapper.findAll('.settings-input')
    expect(inputs[0]!.attributes('placeholder')).toBe('3')
    expect(inputs[0]!.classes()).toContain('ui-input')
    expect(inputs[4]!.classes()).toContain('ui-input')
    expect(inputs[9]!.classes()).toContain('ui-input')
    expect(inputs[0]!.classes()).not.toContain('is-default')

    await inputs[0]!.setValue('2')
    expect(inputs[0]!.classes()).toContain('is-default')

    await inputs[0]!.setValue('5')
    expect(inputs[0]!.classes()).not.toContain('is-default')

    wrapper.unmount()
  })

  it('shows a danger message when loading the settings fails', async () => {
    tradingSettingsMocks.apiFetchMock.mockImplementationOnce(async () => responseOf({ detail: 'boom' }, false, 500))

    const wrapper = await mountTradingSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در بارگذاری تنظیمات')
    expect(wrapper.find('.ds-loading-state').exists()).toBe(false)

    wrapper.unmount()
  })

  it('saves edited settings with merged fallback values and clears the draft form after success', async () => {
    const wrapper = await mountTradingSettings()
    await flushPromises()

    const inputs = wrapper.findAll('.settings-input')
    await inputs[1]!.setValue('30')
    await inputs[8]!.setValue('14')
    expect(wrapper.get('[data-testid="market-open-time"]').classes()).toContain('ui-input')
    expect(wrapper.get('[data-testid="market-close-time"]').classes()).toContain('ui-input')
    expect(wrapper.get('[data-testid="market-schedule-enabled"]').classes()).toContain('ui-checkbox')
    expect(wrapper.get('[data-testid="competitive-price-validation-enabled"]').classes()).toContain('ui-checkbox')
    expect(wrapper.get('[data-testid="offer-price-warning-enabled"]').classes()).toContain('ui-checkbox')
    expect((wrapper.get('[data-testid="competitive-price-validation-enabled"]').element as HTMLInputElement).checked).toBe(false)
    expect((wrapper.get('[data-testid="offer-price-warning-enabled"]').element as HTMLInputElement).checked).toBe(true)
    await wrapper.get('[data-testid="market-schedule-enabled"]').setValue(false)
    await wrapper.get('[data-testid="market-open-time"]').setValue('09:30')
    await wrapper.get('[data-testid="market-close-time"]').setValue('16:45')
    expect(wrapper.get('[data-testid="weekday-3"]').classes()).toContain('ui-checkbox')
    await wrapper.get('[data-testid="weekday-3"]').setValue(true)
    const saveFooterButton = wrapper.get('.settings-button.settings-button--primary.footer-control')
    expect(saveFooterButton.classes()).toContain('ui-button')
    await saveFooterButton.trigger('click')
    await flushPromises()

    const putCall = tradingSettingsMocks.apiFetchMock.mock.calls.find(
      ([url, options]) => url === '/api/trading-settings/' && options?.method === 'PUT',
    )
    expect(putCall).toBeDefined()
    expect(JSON.parse(String(putCall![1]?.body))).toEqual({
      invitation_expiry_days: 3,
      offer_expiry_minutes: 30,
      offer_min_quantity: 10,
      offer_max_quantity: 120,
      max_active_offers: 8,
      offer_expire_rate_per_minute: 4,
      offer_expire_daily_limit_after_threshold: 21,
      anti_abuse_daily_base: 6,
      anti_abuse_weekly_base: 14,
      anti_abuse_monthly_base: 12,
      market_schedule_enabled: false,
      competitive_price_validation_enabled: false,
      offer_price_warning_enabled: true,
      market_open_time_local: '09:30',
      market_close_time_local: '16:45',
      market_closed_weekdays: [3, 4],
    })
    expect(wrapper.text()).toContain('تنظیمات با موفقیت ذخیره شد')
    expect(wrapper.get('.settings-viewport-toast--success').text()).toBe('تنظیمات با موفقیت ذخیره شد')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('')
    expect(inputs[1]!.attributes('placeholder')).toBe('30')
    expect((inputs[8]!.element as HTMLInputElement).value).toBe('')
    expect(inputs[8]!.attributes('placeholder')).toBe('14')

    wrapper.unmount()
  })

  it('creates, edits, and deletes market calendar overrides', async () => {
    const wrapper = await mountTradingSettings()
    await flushPromises()

    const headers = wrapper.findAll('.settings-section__header')
    await headers[5]!.trigger('click')
    await flushPromises()

    await setJalaliDate(wrapper, '2026-05-30')
    expect(wrapper.get('[data-testid="override-type"]').classes()).toEqual(expect.arrayContaining(['ui-input', 'ui-select']))
    await wrapper.get('[data-testid="override-type"]').setValue('custom_hours')
    expect(wrapper.get('[data-testid="override-open-time"]').classes()).toContain('ui-input')
    expect(wrapper.get('[data-testid="override-close-time"]').classes()).toContain('ui-input')
    expect(wrapper.get('[data-testid="override-note"]').classes()).toContain('ui-input')
    await wrapper.get('[data-testid="override-open-time"]').setValue('12:00')
    await wrapper.get('[data-testid="override-close-time"]').setValue('15:30')
    await wrapper.get('[data-testid="override-note"]').setValue('جلسه خاص')
    const overrideSaveButton = wrapper.get('[data-testid="override-save"]')
    expect(overrideSaveButton.classes()).toContain('ui-button')
    await overrideSaveButton.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('استثنای تقویمی با موفقیت ثبت شد')
    expect(wrapper.findAll('[data-testid="market-override-row"]').length).toBe(3)
    expect(wrapper.text()).toContain('جلسه خاص')

    const overrideEditButton = wrapper.get('[data-testid="override-edit-1"]')
    expect(overrideEditButton.classes()).toContain('ui-button')
    await overrideEditButton.trigger('click')
    await wrapper.get('[data-testid="override-note"]').setValue('تعطیلی اصلاح‌شده')
    expect(wrapper.get('[data-testid="override-cancel"]').classes()).toContain('ui-button')
    await wrapper.get('[data-testid="override-save"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('استثنای تقویمی با موفقیت ویرایش شد')
    expect(wrapper.text()).toContain('تعطیلی اصلاح‌شده')

    const overrideDeleteButton = wrapper.get('[data-testid="override-delete-2"]')
    expect(overrideDeleteButton.classes()).toContain('ui-button')
    await overrideDeleteButton.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('استثنای تقویمی حذف شد')
    expect(wrapper.findAll('[data-testid="market-override-row"]').length).toBe(2)

    wrapper.unmount()
  })

  it('shows the server detail when saving settings fails', async () => {
    tradingSettingsMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/trading-settings/' && method === 'GET') {
        return responseOf(loadedSettings)
      }
      if (path === '/api/trading-settings/' && method === 'PUT') {
        return responseOf({ detail: 'ذخیره نشد' }, false, 400)
      }
      return responseOf(defaultSettings)
    })

    const wrapper = await mountTradingSettings()
    await flushPromises()

    await wrapper.find('.settings-button.settings-button--primary.footer-control').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ذخیره نشد')
    expect(wrapper.find('.ds-message.danger').exists()).toBe(true)

    wrapper.unmount()
  })

  it('skips reset when the confirmation dialog is rejected', async () => {
    const confirmMock = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmMock)

    const wrapper = await mountTradingSettings()
    await flushPromises()

    const resetFooterButton = wrapper.get('.settings-button.settings-button--danger.footer-control')
    expect(resetFooterButton.classes()).toContain('ui-button')
    await resetFooterButton.trigger('click')
    await flushPromises()

    expect(confirmMock).toHaveBeenCalledOnce()
    expect(tradingSettingsMocks.apiFetchMock).not.toHaveBeenCalledWith(
      '/api/trading-settings/reset',
      expect.anything(),
    )

    wrapper.unmount()
  })

  it('resets settings to defaults after confirmation and surfaces reset errors', async () => {
    const wrapper = await mountTradingSettings()
    await flushPromises()

    await wrapper.find('.settings-button.settings-button--danger.footer-control').trigger('click')
    await flushPromises()

    expect(tradingSettingsMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/reset', { method: 'POST' })
    expect(wrapper.text()).toContain('تنظیمات به مقادیر پیش‌فرض بازنشانی شد')
    expect(wrapper.get('.settings-viewport-toast--success').text()).toBe('تنظیمات به مقادیر پیش‌فرض بازنشانی شد')

    tradingSettingsMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/trading-settings/' && method === 'GET') {
        return responseOf(loadedSettings)
      }
      if (path === '/api/trading-settings/reset' && method === 'POST') {
        return responseOf({ detail: 'reset failed' }, false, 500)
      }
      return responseOf(defaultSettings)
    })

    await wrapper.find('.settings-button.settings-button--danger.footer-control').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در بازنشانی تنظیمات')
    expect(wrapper.find('.ds-message.danger').exists()).toBe(true)

    wrapper.unmount()
  })
})
