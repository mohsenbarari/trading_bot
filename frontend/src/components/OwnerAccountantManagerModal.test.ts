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

async function mountModal(props: Record<string, unknown> = {}) {
  const OwnerAccountantManagerModal = (await import('./OwnerAccountantManagerModal.vue')).default
  const wrapper = mount(OwnerAccountantManagerModal, {
    props,
    global: {
      stubs: {
        teleport: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

async function openCreatePanel(wrapper: any) {
  await wrapper.findAll('.accountant-main-menu-header')[0].trigger('click')
  await flushPromises()
}

async function openRelationsPanel(wrapper: any) {
  await wrapper.findAll('.accountant-main-menu-header')[1].trigger('click')
  await flushPromises()
}

async function openFirstAccountantDetail(wrapper: any) {
  await wrapper.get('.accountant-settings-btn').trigger('click')
  await flushPromises()
}

async function openDetailSection(wrapper: any, index: number) {
  await wrapper.findAll('.detail-accordion > .ds-accordion-header')[index].trigger('click')
  await flushPromises()
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
    vi.restoreAllMocks()
  })

  it('loads relations and creates a new accountant relation in the direct accordion flow', async () => {
    apiFetchMock.mockResolvedValueOnce(makeResponse([makeRelation()]))
    apiFetchMock.mockResolvedValueOnce(makeResponse(makeRelation({
      id: 2,
      global_account_name: 'acc2',
      relation_display_name: 'حسابدار دوم',
      mobile_number: '09123333333',
      duty_description: 'گزارش‌گیری',
    })))

    const wrapper = await mountModal()

    expect(wrapper.text()).toContain('حسابداران')
    expect(wrapper.text()).not.toContain('دسته‌بندی مدیریت حسابداران')

    await openRelationsPanel(wrapper)
    expect(wrapper.text()).toContain('دعوت‌نامه‌های در انتظار')
    expect(wrapper.text()).toContain('حسابدار اول')
    expect(wrapper.text()).toContain('مهلت ثبت نام: 1 روز')

    await openCreatePanel(wrapper)
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
    expect(wrapper.text()).toContain('دعوت حسابدار ثبت شد.')
    expect(wrapper.text()).toContain('حسابدار دوم')
  })

  it('renders as an inline workspace surface and emits route navigation events', async () => {
    apiFetchMock.mockResolvedValueOnce(makeResponse([makeRelation({
      id: 8,
      status: 'active',
      accountant_user_id: 18,
      accountant_account_name: 'acc-active',
      relation_display_name: 'حسابدار فعال',
      registration_link: null,
      activated_at: '2026-01-02T08:00:00',
    })]))

    const wrapper = await mountModal({ presentation: 'workspace' })

    expect(wrapper.find('.accountant-manager-page').exists()).toBe(true)
    expect(wrapper.find('.accountant-manager-backdrop').exists()).toBe(false)
    expect(wrapper.find('.accountant-manager-header').exists()).toBe(false)
    expect(wrapper.find('.accountant-manager-shell--workspace').exists()).toBe(true)

    await openRelationsPanel(wrapper)
    await openFirstAccountantDetail(wrapper)
    expect(wrapper.emitted('open-relation')?.[0]).toEqual([8])

    await wrapper.get('.accountant-detail-topbar .ghost-btn').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('back-to-list')).toHaveLength(1)
  })

  it('opens the requested accountant relation when mounted from a detail route', async () => {
    apiFetchMock.mockResolvedValueOnce(makeResponse([makeRelation({
      id: 8,
      status: 'active',
      accountant_user_id: 18,
      accountant_account_name: 'acc-active',
      relation_display_name: 'حسابدار فعال',
      registration_link: null,
      activated_at: '2026-01-02T08:00:00',
    })]))

    const wrapper = await mountModal({
      presentation: 'workspace',
      initialRelationId: '8',
    })

    expect(wrapper.find('.accountant-detail-page').exists()).toBe(true)
    expect(wrapper.text()).toContain('حسابدار فعال')
    expect(wrapper.text()).toContain('مشخصات و شرح وظیفه')
  })

  it('edits an active accountant in the detail page and cancels a pending invitation', async () => {
    const pendingRelation = makeRelation()
    const activeRelation = makeRelation({
      id: 8,
      status: 'active',
      accountant_user_id: 18,
      accountant_account_name: 'acc-active',
      global_account_name: 'acc-active',
      relation_display_name: 'حسابدار فعال',
      registration_link: null,
      activated_at: '2026-01-02T08:00:00',
    })
    const updatedRelation = {
      ...activeRelation,
      duty_description: 'مدیریت ثبت‌ها',
    }

    apiFetchMock.mockResolvedValueOnce(makeResponse([pendingRelation, activeRelation]))
    apiFetchMock.mockResolvedValueOnce(makeResponse(updatedRelation))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ ...pendingRelation, status: 'revoked' }))

    const wrapper = await mountModal()
    await openRelationsPanel(wrapper)

    await openFirstAccountantDetail(wrapper)
    await wrapper.get('.edit-duty-description').setValue('مدیریت ثبت‌ها')
    await wrapper.get('.save-edit').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/8', {
      method: 'PATCH',
      body: JSON.stringify({
        duty_description: 'مدیریت ثبت‌ها',
      }),
    })
    expect(wrapper.text()).toContain('اطلاعات حسابدار به‌روزرسانی شد.')

    await wrapper.get('.accountant-detail-topbar .ghost-btn').trigger('click')
    await flushPromises()
    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/1', {
      method: 'DELETE',
    })
    expect(wrapper.text()).toContain('دعوت حسابدار لغو شد.')
    expect(wrapper.text()).not.toContain('حسابدار اول')
  })

  it('unlinks active accountant relations from the detail danger section', async () => {
    const activeRelation = makeRelation({
      id: 8,
      status: 'active',
      accountant_user_id: 18,
      accountant_account_name: 'acc-active',
      relation_display_name: 'حسابدار فعال',
      registration_link: null,
      activated_at: '2026-01-02T08:00:00',
    })

    apiFetchMock.mockResolvedValueOnce(makeResponse([activeRelation]))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ ...activeRelation, status: 'deleted' }))

    const wrapper = await mountModal()
    await openRelationsPanel(wrapper)
    await openFirstAccountantDetail(wrapper)
    await openDetailSection(wrapper, 2)

    expect(wrapper.find('.unlink-active').exists()).toBe(true)
    await wrapper.get('.unlink-active').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/8', {
      method: 'DELETE',
    })
    expect(wrapper.text()).toContain('ارتباط حسابدار قطع شد')
    expect(wrapper.text()).not.toContain('حسابدار فعال')
  })

  it('lets owners view and terminate active accountant sessions from the detail page', async () => {
    const activeRelation = makeRelation({
      id: 8,
      status: 'active',
      accountant_user_id: 18,
      accountant_account_name: 'acc-active',
      relation_display_name: 'حسابدار فعال',
      registration_link: null,
      activated_at: '2026-01-02T08:00:00',
    })
    const sessions = [
      {
        id: 'session-1',
        device_name: 'Chrome',
        device_ip: '10.0.0.1',
        platform: 'web',
        home_server: 'foreign',
        is_primary: true,
        is_active: true,
        created_at: '2026-01-02T08:00:00',
        last_active_at: '2026-01-02T09:00:00',
      },
    ]

    apiFetchMock.mockResolvedValueOnce(makeResponse([activeRelation]))
    apiFetchMock.mockResolvedValueOnce(makeResponse(sessions))
    apiFetchMock.mockResolvedValueOnce(makeResponse({
      detail: 'نشست حسابدار با موفقیت پایان یافت',
      terminated_session_id: 'session-1',
      promoted_primary_session_id: null,
    }))
    apiFetchMock.mockResolvedValueOnce(makeResponse([]))

    const wrapper = await mountModal()
    await openRelationsPanel(wrapper)
    await openFirstAccountantDetail(wrapper)
    await openDetailSection(wrapper, 1)

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/8/sessions', {
      method: 'GET',
    })
    expect(wrapper.text()).toContain('نشست حسابدار')
    expect(wrapper.text()).toContain('Chrome')

    await wrapper.get('.terminate-session').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('نشست «Chrome» پایان یابد؟')
    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/8/sessions/session-1', {
      method: 'DELETE',
    })
    expect(wrapper.text()).toContain('نشست حسابدار با موفقیت پایان یافت')
    expect(wrapper.text()).toContain('در حال حاضر نشست فعالی برای این حسابدار ثبت نشده است.')
  })

  it('supports form reset, copy-link feedback, and close emit', async () => {
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

    const clipboardWrite = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, {
      clipboard: { writeText: clipboardWrite },
    })

    const wrapper = await mountModal()
    await openCreatePanel(wrapper)

    await wrapper.get('.create-account-name').setValue('acc-temp')
    await wrapper.get('.create-display-name').setValue('حسابدار موقت')
    await wrapper.get('.create-mobile-number').setValue('09125555555')
    await wrapper.get('.create-duty-description').setValue('توضیح موقت')
    await wrapper.get('.secondary-btn').trigger('click')

    expect((wrapper.get('.create-account-name').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-display-name').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-mobile-number').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('.create-duty-description').element as HTMLTextAreaElement).value).toBe('')

    await openRelationsPanel(wrapper)
    await wrapper.get('.copy-link').trigger('click')
    await flushPromises()

    expect(clipboardWrite).toHaveBeenCalledWith('https://app.example/register?token=ACCT-token')
    expect(wrapper.text()).toContain('کپی شد')

    await vi.advanceTimersByTimeAsync(1800)
    expect(wrapper.text()).toContain('کپی لینک')

    await wrapper.get('.accountant-manager-back').trigger('click')
    expect(wrapper.emitted('close')).toEqual([[]])
  })

  it('renders lifecycle copy inside accountant detail pages', async () => {
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

    const wrapper = await mountModal()
    await openRelationsPanel(wrapper)

    await openFirstAccountantDetail(wrapper)
    expect(wrapper.text()).toContain('این رابطه فعال شده است.')

    await wrapper.get('.accountant-detail-topbar .ghost-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.accountant-settings-btn')[1].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('مهلت این دعوت به پایان رسیده است.')

    await wrapper.get('.accountant-detail-topbar .ghost-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.accountant-settings-btn')[2].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('این دعوت توسط مالک لغو شده است.')

    await wrapper.get('.accountant-detail-topbar .ghost-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.accountant-settings-btn')[3].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('این رابطه حذف شده است.')
  })

  it('shows fallback and detail errors for load, create, edit, cancel, and copy failures', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('خطای شبکه'))

    const wrapper = await mountModal()
    expect(wrapper.text()).toContain('خطای شبکه')

    apiFetchMock.mockReset()
    apiFetchMock.mockResolvedValueOnce(makeResponse([
      makeRelation(),
      makeRelation({
        id: 8,
        status: 'active',
        accountant_user_id: 18,
        accountant_account_name: 'acc-active',
        relation_display_name: 'حسابدار فعال',
        registration_link: null,
        activated_at: '2026-01-02T08:00:00',
      }),
    ]))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'ایجاد نشد' }, false))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'ویرایش نشد' }, false))
    apiFetchMock.mockResolvedValueOnce(makeResponse({ detail: 'لغو نشد' }, false))
    const clipboardWrite = vi.fn().mockRejectedValue(new Error('copy failed'))
    Object.assign(navigator, {
      clipboard: { writeText: clipboardWrite },
    })

    const secondWrapper = await mountModal()
    await openCreatePanel(secondWrapper)

    await secondWrapper.get('.create-account-name').setValue('acc3')
    await secondWrapper.get('.create-display-name').setValue('حسابدار سوم')
    await secondWrapper.get('.create-mobile-number').setValue('09126666666')
    await secondWrapper.get('.submit-create').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('ایجاد نشد')

    await openRelationsPanel(secondWrapper)
    await openFirstAccountantDetail(secondWrapper)
    await secondWrapper.get('.edit-duty-description').setValue('تغییر')
    await secondWrapper.get('.save-edit').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('ویرایش نشد')

    await secondWrapper.get('.accountant-detail-topbar .ghost-btn').trigger('click')
    await flushPromises()
    await secondWrapper.get('.cancel-pending').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('لغو نشد')

    await secondWrapper.get('.copy-link').trigger('click')
    await flushPromises()
    expect(secondWrapper.text()).toContain('کپی لینک ثبت‌نام ممکن نشد.')
  })

  it('respects a rejected pending-cancel confirmation', async () => {
    vi.mocked(window.confirm).mockReturnValue(false)
    apiFetchMock.mockResolvedValueOnce(makeResponse([makeRelation()]))

    const wrapper = await mountModal()
    await openRelationsPanel(wrapper)

    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledTimes(1)
  })
})
