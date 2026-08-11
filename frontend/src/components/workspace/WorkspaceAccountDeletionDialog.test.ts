import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import WorkspaceAccountDeletionDialog from './WorkspaceAccountDeletionDialog.vue'

describe('WorkspaceAccountDeletionDialog', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('requires the exact account name and explicit acknowledgement before confirming', async () => {
    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      attachTo: document.body,
      props: {
        open: true,
        subjectName: 'محمد همتی',
      },
    })

    expect(wrapper.text()).toContain('آفرهای فعال')
    expect(wrapper.text()).toContain('سوابق معاملات حذف نمی‌شوند')
    const dialog = wrapper.get('[role="dialog"]')
    const descriptionIds = dialog.attributes('aria-describedby')?.split(' ') ?? []
    expect(descriptionIds).toHaveLength(2)
    expect(document.getElementById(descriptionIds[1] ?? '')?.textContent).toContain('دسترسی وب‌اپ')
    const confirmButton = wrapper.get('button.ui-button--danger')
    expect(confirmButton.attributes('disabled')).toBeDefined()

    await wrapper.get('input.ui-input').setValue(' محمد همتی ')
    await wrapper.get('input.ui-checkbox').setValue(true)
    expect(confirmButton.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('نام واردشده دقیقاً با نام نمایش‌داده‌شده یکسان نیست')

    await wrapper.get('input.ui-input').setValue('محمد همتی')
    expect(confirmButton.attributes('disabled')).toBeUndefined()
    await confirmButton.trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(confirmButton.attributes('disabled')).toBeDefined()
    await confirmButton.trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)

    wrapper.unmount()
  })

  it('fails closed for an empty subject and resets when the displayed subject changes', async () => {
    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      props: {
        open: true,
        subjectName: '',
      },
    })

    await wrapper.get('input.ui-checkbox').setValue(true)
    expect(wrapper.get('button.ui-button--danger').attributes('disabled')).toBeDefined()

    await wrapper.setProps({ subjectName: 'حسابدار فروش' })
    await wrapper.get('input.ui-input').setValue('حسابدار فروش')
    await wrapper.get('input.ui-checkbox').setValue(true)
    expect(wrapper.get('button.ui-button--danger').attributes('disabled')).toBeUndefined()

    await wrapper.setProps({ subjectName: 'حسابدار جدید' })
    expect((wrapper.get('input.ui-input').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('input.ui-checkbox').element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.get('button.ui-button--danger').attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('locks every dialog control and announces progress while a confirmed deletion is busy', async () => {
    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      props: {
        open: true,
        subjectName: 'حسابدار فروش',
      },
    })

    await wrapper.get('input.ui-input').setValue('حسابدار فروش')
    await wrapper.get('input.ui-checkbox').setValue(true)
    await wrapper.setProps({ busy: true })

    expect(wrapper.get('[role="dialog"]').attributes('aria-busy')).toBe('true')
    expect(wrapper.get('input.ui-input').attributes('disabled')).toBeDefined()
    expect(wrapper.get('input.ui-checkbox').attributes('disabled')).toBeDefined()
    const cancelButton = wrapper.get('button.ui-button--secondary')
    expect(cancelButton.attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="status"]').text()).toContain('در حال انجام')

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('cancel')).toBeUndefined()

    wrapper.unmount()
  })

  it('releases the internal submission latch only after the parent reports a failed attempt', async () => {
    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      props: {
        open: true,
        subjectName: 'حسابدار فروش',
      },
    })

    await wrapper.get('input.ui-input').setValue('حسابدار فروش')
    await wrapper.get('input.ui-checkbox').setValue(true)
    const confirmButton = wrapper.get('button.ui-button--danger')

    confirmButton.element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    confirmButton.element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('confirm')).toHaveLength(1)
    expect(confirmButton.attributes('disabled')).toBeDefined()

    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false, error: 'حذف حساب ناموفق بود.' })
    expect(confirmButton.attributes('disabled')).toBeUndefined()

    await confirmButton.trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(2)

    wrapper.unmount()
  })
})
