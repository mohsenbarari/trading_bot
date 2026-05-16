import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
  offer_expire_rate_per_minute: 2,
  offer_expire_daily_limit_after_threshold: 10,
  anti_abuse_daily_base: 2,
  anti_abuse_weekly_base: 5,
  anti_abuse_monthly_base: 7,
}

const loadedSettings = {
  invitation_expiry_days: 3,
  offer_expiry_minutes: 15,
  offer_min_quantity: 10,
  offer_max_quantity: 120,
  max_active_offers: 8,
  offer_expire_rate_per_minute: 4,
  offer_expire_daily_limit_after_threshold: 21,
  anti_abuse_daily_base: 6,
  anti_abuse_weekly_base: 9,
  anti_abuse_monthly_base: 12,
}

async function mountTradingSettings() {
  const TradingSettings = (await import('./TradingSettings.vue')).default
  return mount(TradingSettings, {
    props: {
      apiBaseUrl: '',
      jwtToken: 'jwt-token',
    },
  })
}

describe('TradingSettings.vue', () => {
  beforeEach(() => {
    tradingSettingsMocks.apiFetchMock.mockReset()
    vi.stubGlobal('confirm', vi.fn(() => true))
    tradingSettingsMocks.apiFetchMock.mockImplementation(async (path: string, options?: RequestInit) => {
      const method = options?.method || 'GET'
      if (path === '/api/trading-settings/' && method === 'GET') {
        return responseOf(loadedSettings)
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
    expect(wrapper.text()).toContain('امنیت و نشست‌ها')

    const headers = wrapper.findAll('.ds-accordion-header')
    await headers[0]!.trigger('click')
    await headers[3]!.trigger('click')
    await flushPromises()

    const accordions = wrapper.findAll('.ds-accordion')
    expect(accordions[0]!.classes()).toContain('open')
    expect(accordions[3]!.classes()).toContain('open')

    const inputs = wrapper.findAll('.ds-input')
    expect(inputs[0]!.attributes('placeholder')).toBe('3')
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

    const inputs = wrapper.findAll('.ds-input')
    await inputs[1]!.setValue('30')
    await inputs[8]!.setValue('14')
    await wrapper.find('.ds-btn.primary.action-btn').trigger('click')
    await flushPromises()

    expect(tradingSettingsMocks.apiFetchMock).toHaveBeenCalledWith(
      '/api/trading-settings/',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
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
        }),
      }),
    )
    expect(wrapper.text()).toContain('تنظیمات با موفقیت ذخیره شد')
    expect((inputs[1]!.element as HTMLInputElement).value).toBe('')
    expect(inputs[1]!.attributes('placeholder')).toBe('30')
    expect((inputs[8]!.element as HTMLInputElement).value).toBe('')
    expect(inputs[8]!.attributes('placeholder')).toBe('14')

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

    await wrapper.find('.ds-btn.primary.action-btn').trigger('click')
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

    await wrapper.find('.ds-btn.danger.action-btn').trigger('click')
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

    await wrapper.find('.ds-btn.danger.action-btn').trigger('click')
    await flushPromises()

    expect(tradingSettingsMocks.apiFetchMock).toHaveBeenCalledWith('/api/trading-settings/reset', { method: 'POST' })
    expect(wrapper.text()).toContain('تنظیمات به مقادیر پیش‌فرض بازنشانی شد')

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

    await wrapper.find('.ds-btn.danger.action-btn').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('خطا در بازنشانی تنظیمات')
    expect(wrapper.find('.ds-message.danger').exists()).toBe(true)

    wrapper.unmount()
  })
})