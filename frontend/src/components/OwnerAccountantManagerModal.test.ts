import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const apiFetchMock = vi.fn()

vi.mock('../utils/auth', () => ({
  apiFetch: apiFetchMock,
}))

function makeResponse(payload: unknown, ok = true) {
  return {
    ok,
    json: async () => payload,
  }
}

function makeRelation(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    owner_user_id: 7,
    accountant_user_id: null,
    accountant_account_name: null,
    global_account_name: 'acc1',
    relation_display_name: 'حسابدار اول',
    duty_description: 'پیگیری',
    mobile_number: '09120000000',
    status: 'pending',
    invitation_token: 'ACCT-token',
    registration_link: 'https://app.example/register?token=ACCT-token',
    expires_at: '2026-01-03T10:00:00',
    activated_at: null,
    deleted_at: null,
    created_at: '2026-01-01T10:00:00',
    ...overrides,
  }
}

describe('OwnerAccountantManagerModal.vue', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    vi.spyOn(window, 'confirm').mockImplementation(() => true)
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-01-02T10:00:00Z'))
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('loads relations and creates a new accountant relation', async () => {
    apiFetchMock.mockResolvedValueOnce(makeResponse([makeRelation()]))
    apiFetchMock.mockResolvedValueOnce(makeResponse(makeRelation({
      id: 2,
      global_account_name: 'acc2',
      relation_display_name: 'حسابدار دوم',
      mobile_number: '09123333333',
      duty_description: 'گزارش‌گیری',
    })))

    const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
    const wrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()
    expect(wrapper.text()).toContain('حسابدار اول')
  expect(wrapper.text()).toContain('مهلت ثبت نام: 1 روز')

    await wrapper.get('.create-account-name').setValue('acc2')
    await wrapper.get('.create-display-name').setValue('حسابدار دوم')
    await wrapper.get('.create-mobile-number').setValue('09123333333')
    await wrapper.get('.create-duty-description').setValue('گزارش‌گیری')
    await wrapper.get('.submit-create').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations', {
      method: 'POST',
      body: JSON.stringify({
        account_name: 'acc2',
        relation_display_name: 'حسابدار دوم',
        mobile_number: '09123333333',
        duty_description: 'گزارش‌گیری',
      }),
    })
    expect(wrapper.text()).toContain('حسابدار دوم')
  })

  it('edits and cancels a pending accountant relation', async () => {
    const initialRelation = makeRelation()
    const updatedRelation = makeRelation({
      relation_display_name: 'حسابدار اول',
      duty_description: 'مدیریت ثبت‌ها',
    })

    apiFetchMock.mockResolvedValueOnce(makeResponse([initialRelation]))
    apiFetchMock.mockResolvedValueOnce(makeResponse(updatedRelation))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ ...updatedRelation, status: 'revoked' }))

    const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
    const wrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()

    await wrapper.get('.start-edit').trigger('click')
    await wrapper.get('.edit-duty-description').setValue('مدیریت ثبت‌ها')
    await wrapper.get('.save-edit').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/1', {
      method: 'PATCH',
      body: JSON.stringify({
        duty_description: 'مدیریت ثبت‌ها',
      }),
    })
    expect(wrapper.text()).toContain('حسابدار اول')

    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.text()).toContain('مهلت ثبت نام: 23:59:59')

    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/1', {
      method: 'DELETE',
    })
    expect(wrapper.text()).not.toContain('حسابدار اول')
  })

  it('unlinks active accountant relations through the same delete endpoint', async () => {
    const activeRelation = makeRelation({
      id: 8,
      status: 'active',
      accountant_account_name: 'acc-active',
      relation_display_name: 'حسابدار فعال',
      registration_link: null,
      activated_at: '2026-01-02T08:00:00',
    })

    apiFetchMock.mockResolvedValueOnce(makeResponse([activeRelation]))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ ...activeRelation, status: 'deleted' }))

    const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
    const wrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.find('.unlink-active').exists()).toBe(true)
    await wrapper.get('.unlink-active').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/8', {
      method: 'DELETE',
    })
    expect(wrapper.text()).toContain('ارتباط حسابدار قطع شد')
    expect(wrapper.text()).not.toContain('حسابدار فعال')
  })

  it('supports silent refresh, form reset, copy-link feedback, and close emit', async () => {
    apiFetchMock.mockResolvedValueOnce(makeResponse([
      makeRelation(),
      makeRelation({
        id: 2,
        status: 'active',
        accountant_account_name: 'acc2',
        relation_display_name: 'حسابدار فعال',
        registration_link: null,
      }),
    ]))
    apiFetchMock.mockResolvedValueOnce(makeResponse([]))

    const clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: { writeText: clipboardWrite },
    })

    const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
    const wrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()

    await wrapper.get('.create-account-name').setValue('acc-temp')
    await wrapper.get('.create-display-name').setValue('حسابدار موقت')
    await wrapper.get('.create-mobile-number').setValue('09125555555')
    await wrapper.get('.create-duty-description').setValue('توضیح موقت')
    await wrapper.get('.secondary-btn').trigger('click')

    expect((wrapper.get('.create-account-name').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-display-name').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-mobile-number').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-duty-description').element as HTMLTextAreaElement).value).toBe('')

    await wrapper.get('.copy-link').trigger('click')
    await flushPromises()

    expect(clipboardWrite).toHaveBeenCalledWith('https://app.example/register?token=ACCT-token')
    expect(wrapper.text()).toContain('کپی شد')
  expect(wrapper.text()).toContain('این حسابدار با @acc2 فعال است.')

    await vi.advanceTimersByTimeAsync(1800)
    expect(wrapper.text()).toContain('کپی لینک ثبت‌نام')

    await wrapper.get('.ghost-btn').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenLastCalledWith('/api/accountants/owner-relations')
    expect(wrapper.text()).toContain('هنوز هیچ حسابداری برای این مالک ثبت نشده است.')

    await wrapper.get('.accountant-manager-close').trigger('click')
    expect(wrapper.emitted('close')).toEqual([[]])
  })

  it('renders lifecycle copy for active and terminal relation states', async () => {
    apiFetchMock.mockResolvedValueOnce(makeResponse([
      makeRelation({
        id: 2,
        status: 'active',
        accountant_account_name: null,
        relation_display_name: 'حسابدار فعال بدون نام کاربری',
        registration_link: null,
        activated_at: '2026-01-02T08:00:00',
      }),
      makeRelation({
        id: 3,
        status: 'expired',
        relation_display_name: 'حسابدار منقضی',
        registration_link: null,
      }),
      makeRelation({
        id: 4,
        status: 'revoked',
        relation_display_name: 'حسابدار لغوشده',
        registration_link: null,
      }),
      makeRelation({
        id: 5,
        status: 'deleted',
        relation_display_name: 'حسابدار حذف‌شده',
        registration_link: null,
      }),
    ]))

    const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
    const wrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()

    expect(wrapper.text()).toContain('این رابطه فعال شده است.')
    expect(wrapper.text()).toContain('مهلت این دعوت به پایان رسیده است.')
    expect(wrapper.text()).toContain('این دعوت توسط مالک لغو شده است.')
    expect(wrapper.text()).toContain('این رابطه حذف شده است.')

    wrapper.unmount()
  })

  it('shows fallback and detail errors for load, create, edit, cancel, and copy failures', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('خطای شبکه'))

    const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
    const wrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()
    expect(wrapper.text()).toContain('خطای شبکه')

    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValueOnce(makeResponse([makeRelation()]))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'ایجاد نشد' }, false))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'ویرایش نشد' }, false))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'لغو نشد' }, false))
    const clipboardWrite = vi.fn().mockRejectedValue(new Error('copy failed'))
    Object.assign(navigator, {
      clipboard: { writeText: clipboardWrite },
    })

    const secondWrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()

    await secondWrapper.get('.create-account-name').setValue('acc3')
    await secondWrapper.get('.create-display-name').setValue('حسابدار سوم')
    await secondWrapper.get('.create-mobile-number').setValue('09126666666')
    await secondWrapper.get('.submit-create').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('ایجاد نشد')

    await secondWrapper.get('.start-edit').trigger('click')
    await secondWrapper.get('.edit-duty-description').setValue('تغییر')
    await secondWrapper.get('.save-edit').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('ویرایش نشد')

    const cancelEditButton = secondWrapper.findAll('button').find((button) => button.text().includes('انصراف'))
    expect(cancelEditButton).toBeTruthy()
    await cancelEditButton!.trigger('click')
    await flushPromises()

    await secondWrapper.get('.cancel-pending').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('لغو نشد')

    await secondWrapper.get('.copy-link').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('کپی لینک ثبت‌نام ممکن نشد.')
  })

  it('cancels edit mode and respects a rejected pending-cancel confirmation', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    apiFetchMock.mockResolvedValueOnce(makeResponse([makeRelation()]))

    const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
    const wrapper = mount(OwnerAccountantManagerModal, {
      global: {
        stubs: {
          teleport: true,
        },
      },
    })

    await flushPromises()

    await wrapper.get('.start-edit').trigger('click')
    await wrapper.get('.edit-duty-description').setValue('توضیح جدید')
    const cancelEditButton = wrapper.findAll('button').find((button) => button.text().includes('انصراف'))
    expect(cancelEditButton).toBeTruthy()
    await cancelEditButton!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.edit-duty-description').exists()).toBe(false)

    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledTimes(1)
  })
})