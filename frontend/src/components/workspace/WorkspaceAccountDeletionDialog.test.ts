import { DOMWrapper, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'
import { UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE } from '../ui/uiDesignSystemScope'
import WorkspaceAccountDeletionDialog from './WorkspaceAccountDeletionDialog.vue'

function bodyDialog() {
  const element = document.body.querySelector<HTMLElement>('[role="dialog"]')
  if (!element) throw new Error('The account deletion dialog is not mounted in document.body.')
  return new DOMWrapper(element)
}

describe('WorkspaceAccountDeletionDialog', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    delete document.body.dataset.uiOverlayLockCount
    document.body.classList.remove('ui-overlay-open')
    document.documentElement.classList.remove('ui-overlay-open')
  })

  it('teleports the V2 portal to the body and restores focus and scroll state after a safe cancel', async () => {
    const trigger = document.createElement('button')
    document.body.append(trigger)
    trigger.focus()
    const host = document.createElement('div')
    document.body.append(host)

    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      attachTo: host,
      props: {
        open: true,
        subjectName: 'محمد همتی',
      },
    })
    await nextTick()

    const dialog = bodyDialog()
    expect(host.contains(dialog.element)).toBe(false)
    expect(
      document.body.querySelector(`[data-ui-system="${UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE}"]`),
    ).not.toBeNull()
    expect(document.body.classList.contains('ui-overlay-open')).toBe(true)
    expect(document.documentElement.classList.contains('ui-overlay-open')).toBe(true)
    expect(document.activeElement).toBe(dialog.get('input.ui-input').element)

    const focusable = dialog.findAll('input:not([disabled]), button:not([disabled])')
    const first = focusable[0]
    const last = focusable.at(-1)
    expect(first).toBeDefined()
    expect(last).toBeDefined()
    last?.element.focus()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(first?.element)

    document.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    )
    expect(wrapper.emitted('cancel')).toHaveLength(1)

    await wrapper.setProps({ open: false })
    await nextTick()
    expect(document.body.classList.contains('ui-overlay-open')).toBe(false)
    expect(document.documentElement.classList.contains('ui-overlay-open')).toBe(false)
    expect(document.activeElement).toBe(trigger)

    wrapper.unmount()
    host.remove()
  })

  it('requires the exact account name and explicit acknowledgement before confirming', async () => {
    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      props: {
        open: true,
        subjectName: 'محمد همتی',
      },
    })

    const dialog = bodyDialog()
    expect(dialog.text()).toContain('آفرهای فعال')
    expect(dialog.text()).toContain('سوابق معاملات حذف نمی‌شوند')
    const descriptionIds = dialog.attributes('aria-describedby')?.split(' ') ?? []
    expect(descriptionIds).toHaveLength(2)
    expect(document.getElementById(descriptionIds[1] ?? '')?.textContent).toContain('دسترسی وب‌اپ')
    const confirmButton = dialog.get('button.ui-button--danger')
    expect(confirmButton.attributes('disabled')).toBeDefined()

    await dialog.get('input.ui-input').setValue(' محمد همتی ')
    await dialog.get('input.ui-checkbox').setValue(true)
    expect(confirmButton.attributes('disabled')).toBeDefined()
    expect(dialog.text()).toContain('نام واردشده دقیقاً با نام نمایش‌داده‌شده یکسان نیست')

    await dialog.get('input.ui-input').setValue('محمد همتی')
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

    let dialog = bodyDialog()
    await dialog.get('input.ui-checkbox').setValue(true)
    expect(dialog.get('button.ui-button--danger').attributes('disabled')).toBeDefined()

    await wrapper.setProps({ subjectName: 'حسابدار فروش' })
    dialog = bodyDialog()
    await dialog.get('input.ui-input').setValue('حسابدار فروش')
    await dialog.get('input.ui-checkbox').setValue(true)
    expect(dialog.get('button.ui-button--danger').attributes('disabled')).toBeUndefined()

    await wrapper.setProps({ subjectName: 'حسابدار جدید' })
    dialog = bodyDialog()
    expect((dialog.get('input.ui-input').element as HTMLInputElement).value).toBe('')
    expect((dialog.get('input.ui-checkbox').element as HTMLInputElement).checked).toBe(false)
    expect(dialog.get('button.ui-button--danger').attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })

  it('locks every dialog control and announces progress while a confirmed deletion is busy', async () => {
    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      props: {
        open: true,
        subjectName: 'حسابدار فروش',
      },
    })

    const dialog = bodyDialog()
    await dialog.get('input.ui-input').setValue('حسابدار فروش')
    await dialog.get('input.ui-checkbox').setValue(true)
    await wrapper.setProps({ busy: true })

    expect(dialog.attributes('aria-busy')).toBe('true')
    expect(dialog.get('input.ui-input').attributes('disabled')).toBeDefined()
    expect(dialog.get('input.ui-checkbox').attributes('disabled')).toBeDefined()
    const cancelButton = dialog.get('button.ui-button--secondary')
    expect(cancelButton.attributes('disabled')).toBeDefined()
    expect(dialog.get('[role="status"]').text()).toContain('در حال انجام')

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

    const dialog = bodyDialog()
    await dialog.get('input.ui-input').setValue('حسابدار فروش')
    await dialog.get('input.ui-checkbox').setValue(true)
    const confirmButton = dialog.get('button.ui-button--danger')

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

  it('never renders a raw parent error and keeps retry available after the failed attempt', async () => {
    const rawServerDetail = 'server detail: account=محمد همتی; trace=forbidden-42'
    const wrapper = mount(WorkspaceAccountDeletionDialog, {
      props: {
        open: true,
        subjectName: 'محمد همتی',
      },
    })

    const dialog = bodyDialog()
    await dialog.get('input.ui-input').setValue('محمد همتی')
    await dialog.get('input.ui-checkbox').setValue(true)
    const confirmButton = dialog.get('button.ui-button--danger')
    await confirmButton.trigger('click')
    expect(confirmButton.attributes('disabled')).toBeDefined()

    await wrapper.setProps({ busy: true })
    await wrapper.setProps({ busy: false, error: rawServerDetail })
    const alert = dialog.get('[role="alert"]')
    expect(alert.text()).toBe('حذف حساب انجام نشد. لطفاً دوباره تلاش کنید.')
    expect(dialog.text()).not.toContain(rawServerDetail)
    expect(confirmButton.attributes('disabled')).toBeUndefined()

    await confirmButton.trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(2)

    wrapper.unmount()
  })
})
