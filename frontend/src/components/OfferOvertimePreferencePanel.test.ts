import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import OfferOvertimePreferencePanel from './OfferOvertimePreferencePanel.vue'
import {
  M5_SAVE_SUCCESS_ZERO,
  M9_HELPER,
  M9_LABEL,
} from '../constants/offerOvertimeCopy'
import * as currentUser from '../utils/currentUser'

const apiMocks = vi.hoisted(() => ({
  saveOfferOvertimePreference: vi.fn(),
}))

vi.mock('../services/offerOvertimeApi', () => ({
  saveOfferOvertimePreference: apiMocks.saveOfferOvertimePreference,
}))

describe('OfferOvertimePreferencePanel', () => {
  beforeEach(() => {
    apiMocks.saveOfferOvertimePreference.mockReset()
    currentUser.clearCurrentUserSummary()
    currentUser.cacheCurrentUserSummary({
      id: 7,
      role: 'عادی',
      offer_overtime_minutes: 3,
      is_accountant: false,
      customer_tier: null,
    })
  })

  it('hides for accountants and tier-2 customers', () => {
    currentUser.cacheCurrentUserSummary({
      id: 8,
      role: 'عادی',
      is_accountant: true,
      offer_overtime_minutes: 0,
    })
    let wrapper = mount(OfferOvertimePreferencePanel)
    expect(wrapper.find('.overtime-pref').exists()).toBe(false)
    wrapper.unmount()

    currentUser.cacheCurrentUserSummary({
      id: 9,
      role: 'عادی',
      is_accountant: false,
      customer_tier: 'tier2',
      offer_overtime_minutes: 0,
    })
    wrapper = mount(OfferOvertimePreferencePanel)
    expect(wrapper.find('.overtime-pref').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders M9 copy and saves on explicit button click', async () => {
    apiMocks.saveOfferOvertimePreference.mockResolvedValue({
      offer_overtime_minutes: 0,
      detail: M5_SAVE_SUCCESS_ZERO,
      warning: null,
    })

    const wrapper = mount(OfferOvertimePreferencePanel)
    expect(wrapper.text()).toContain(M9_LABEL)
    expect(wrapper.text()).toContain(M9_HELPER)

    await wrapper.find('input[type="number"]').setValue('0')
    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('ذخیره'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()

    expect(apiMocks.saveOfferOvertimePreference).toHaveBeenCalledWith(0)
    expect(wrapper.text()).toContain(M5_SAVE_SUCCESS_ZERO)
    expect(currentUser.currentUserSummary.value?.offer_overtime_minutes).toBe(0)
    wrapper.unmount()
  })

  it('does not surface the legacy cross-channel reachability warning on nonzero save', async () => {
    const legacyWarning =
      'تأیید هر لفظ فقط در همان محل ثبت لفظ نمایش داده می‌شود: لفظ وب در وب‌اپ و لفظ بات در بات.'
    apiMocks.saveOfferOvertimePreference.mockResolvedValue({
      offer_overtime_minutes: 5,
      detail: '✅ وقت اضافه لفظ‌های جدید شما روی 5 دقیقه تنظیم شد.',
      warning: legacyWarning,
    })

    const wrapper = mount(OfferOvertimePreferencePanel)
    await wrapper.find('input[type="number"]').setValue('5')
    const saveButton = wrapper.findAll('button').find((button) => button.text().includes('ذخیره'))
    await saveButton!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain(legacyWarning)
    expect(wrapper.text()).toContain('✅ وقت اضافه لفظ‌های جدید شما روی 5 دقیقه تنظیم شد.')
    wrapper.unmount()
  })
})
