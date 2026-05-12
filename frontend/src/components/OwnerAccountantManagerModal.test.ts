import { beforeEach, describe, expect, it, vi } from 'vitest'
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
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
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
      relation_display_name: 'حسابدار ارشد',
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
    await wrapper.get('.edit-display-name').setValue('حسابدار ارشد')
    await wrapper.get('.edit-duty-description').setValue('مدیریت ثبت‌ها')
    await wrapper.get('.save-edit').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/1', {
      method: 'PATCH',
      body: JSON.stringify({
        relation_display_name: 'حسابدار ارشد',
        duty_description: 'مدیریت ثبت‌ها',
      }),
    })
    expect(wrapper.text()).toContain('حسابدار ارشد')

    await wrapper.get('.cancel-pending').trigger('click')
    await flushPromises()

    expect(apiFetchMock).toHaveBeenCalledWith('/api/accountants/owner-relations/1', {
      method: 'DELETE',
    })
    expect(wrapper.text()).not.toContain('حسابدار ارشد')
  })
})