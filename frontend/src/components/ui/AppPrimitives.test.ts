import { mount } from '@vue/test-utils'
import { defineComponent, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import {
  AppActionCard,
  AppBottomSheet,
  AppButton,
  AppDangerZone,
  AppCheckbox,
  AppChip,
  AppDisclosure,
  AppEmptyState,
  AppErrorState,
  AppFilterChips,
  AppFormField,
  AppInput,
  AppListItem,
  AppLoadingState,
  AppMasterDetail,
  AppMetricCard,
  AppNumberStepper,
  AppOfferCard,
  AppOfferCustomerContext,
  AppOfferEmptyState,
  AppOfferHistoryStamp,
  AppOfferLoadingSkeletonList,
  AppOfferPrice,
  AppOfferQuantityBadge,
  AppOfferSideBadge,
  AppOfferTradeErrorToast,
  AppPage,
  AppPageHeader,
  AppResponsiveDialog,
  AppSearchField,
  AppSectionCard,
  AppSelect,
  AppStatusBadge,
  AppTabs,
  AppTextarea,
  AppConfirmDialog,
  AppToast,
  AppToolbar,
  AppTradeActionButton,
  AppWorkspace,
} from './index'

describe('ui primitives', () => {
  it('renders buttons, action cards, metrics, badges, chips, and sections with stable contracts', async () => {
    const button = mount(AppButton, {
      props: { variant: 'secondary' },
      slots: {
        icon: '<span class="button-icon">i</span>',
        default: 'ذخیره',
      },
    })
    expect(button.classes()).toContain('ui-button--secondary')
    expect(button.find('.button-icon').exists()).toBe(true)

    const action = mount(AppActionCard, {
      props: {
        title: 'مدیریت مشتریان',
        description: 'مشاهده و ویرایش روابط',
        badge: 'جدید',
        tone: 'primary',
      },
      slots: {
        icon: '<span>●</span>',
      },
    })
    await action.trigger('click')
    expect(action.emitted('select')).toHaveLength(1)
    expect(action.classes()).toContain('ui-action-card--primary')
    expect(action.text()).toContain('جدید')

    const metric = mount(AppMetricCard, {
      props: { label: 'تعداد', value: '۱۲', hint: 'فعال', tone: 'success' },
    })
    expect(metric.classes()).toContain('ui-metric-card--success')
    expect(metric.text()).toContain('۱۲')

    const badge = mount(AppStatusBadge, {
      props: { tone: 'warning' },
      slots: { default: 'در انتظار' },
    })
    expect(badge.classes()).toContain('ui-status-badge--warning')

    const chip = mount(AppChip, {
      props: { tone: 'primary' },
      slots: { default: 'امامی' },
    })
    expect(chip.classes()).toContain('ui-chip--primary')
    expect(chip.text()).toBe('امامی')

    const section = mount(AppSectionCard, {
      props: { title: 'تنظیمات', description: 'بخش‌های قابل ویرایش' },
      slots: { actions: '<button>عملیات</button>', default: '<p>محتوا</p>' },
    })
    expect(section.find('.ui-section-card__actions').exists()).toBe(true)
    expect(section.text()).toContain('محتوا')

    const offerCard = mount(AppOfferCard, {
      props: {
        hasTimer: true,
        timerCritical: true,
        traded: true,
        timerStyle: { '--t-pct': '42' },
      },
      slots: { default: '<div class="offer-card-inner">لفظ بازار</div>' },
    })
    expect(offerCard.attributes('data-test')).toBe('offer-card')
    expect(offerCard.classes()).toEqual(expect.arrayContaining(['offer-card-wrap', 'has-timer', 'timer-critical', 'is-traded']))
    expect(offerCard.attributes('style')).toContain('--t-pct: 42')
    expect(offerCard.text()).toContain('لفظ بازار')

    const tradeButton = mount(AppTradeActionButton, {
      props: { side: 'buy', pending: true, busy: true },
      slots: { default: 'تایید 5 عدد؟' },
    })
    expect(tradeButton.attributes('data-test')).toBe('trade-action-button')
    expect(tradeButton.attributes('data-state')).toBe('pending')
    expect(tradeButton.classes()).toEqual(expect.arrayContaining(['trade-btn', 'pending', 'busy']))
    expect(tradeButton.attributes('disabled')).toBeDefined()

    const sideBadge = mount(AppOfferSideBadge, { props: { side: 'sell' } })
    expect(sideBadge.classes()).toEqual(expect.arrayContaining(['role-badge', 'sell']))
    expect(sideBadge.text()).toBe('فروش')

    const quantityBadge = mount(AppOfferQuantityBadge, { slots: { default: '12 عدد' } })
    expect(quantityBadge.classes()).toContain('quantity-badge')
    expect(quantityBadge.attributes('data-test')).toBe('offer-quantity')
    expect(quantityBadge.text()).toBe('12 عدد')

    const historyStamp = mount(AppOfferHistoryStamp, {
      props: { label: 'معامله‌شده 20 عدد', traded: true },
    })
    expect(historyStamp.attributes('data-test')).toBe('history-stamp')
    expect(historyStamp.classes()).toEqual(expect.arrayContaining(['history-ribbon', 'traded-ribbon']))
    expect(historyStamp.text()).toBe('معامله‌شده 20 عدد')

    const offerPrice = mount(AppOfferPrice, { props: { value: 50000 } })
    expect(offerPrice.attributes('data-test')).toBe('offer-price')
    expect(offerPrice.classes()).toContain('price')
    expect(offerPrice.text()).toBe('50,000')

    const emptyPrice = mount(AppOfferPrice, { props: { value: 0 } })
    expect(emptyPrice.text()).toBe('---')

    const customerContext = mount(AppOfferCustomerContext, {
      props: { managementName: 'مشتری تست', tierLabel: 'سطح 1' },
    })
    expect(customerContext.attributes('data-test')).toBe('customer-context-row')
    expect(customerContext.text()).toContain('مشتری تست')
    expect(customerContext.text()).toContain('سطح 1')

    const loadingSkeleton = mount(AppOfferLoadingSkeletonList, { props: { count: 3 } })
    expect(loadingSkeleton.attributes('data-test')).toBe('offers-loading-skeleton')
    expect(loadingSkeleton.classes()).toContain('offers-list')
    expect(loadingSkeleton.findAll('.skeleton-card')).toHaveLength(3)

    const offerEmpty = mount(AppOfferEmptyState)
    expect(offerEmpty.attributes('data-test')).toBe('offers-empty-state')
    expect(offerEmpty.classes()).toContain('empty-state')
    expect(offerEmpty.text()).toContain('هیچ لفظ فعالی یافت نشد.')

    const tradeErrorToast = mount(AppOfferTradeErrorToast, { props: { message: 'خطا در انجام معامله' } })
    expect(tradeErrorToast.attributes('data-test')).toBe('offer-trade-error-toast')
    expect(tradeErrorToast.text()).toBe('خطا در انجام معامله')
  })

  it('supports keyboard-friendly tabs and emits model updates', async () => {
    const wrapper = mount(AppTabs, {
      props: {
        modelValue: 'all',
        label: 'فیلترها',
        options: [
          { key: 'all', label: 'همه' },
          { key: 'active', label: 'فعال' },
          { key: 'pending', label: 'دعوت‌ها' },
        ],
      },
    })

    expect(wrapper.attributes('role')).toBe('tablist')
    expect(wrapper.findAll('[role="tab"]')).toHaveLength(3)
    await wrapper.findAll('[role="tab"]')[1]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['active'])

    await wrapper.findAll('[role="tab"]')[0]!.trigger('keydown', { key: 'ArrowLeft' })
    expect(wrapper.emitted('update:modelValue')?.[1]).toEqual(['active'])
  })

  it('connects form fields to inputs and exposes validation state', async () => {
    const field = mount(AppFormField, {
      props: {
        label: 'نام مشتری',
        hint: 'نام فارسی مشتری را وارد کنید.',
      },
      slots: {
        default: `<template #default="{ id, describedby, invalid }">
          <input class="field-input" :id="id" :aria-describedby="describedby" :aria-invalid="invalid" />
        </template>`,
      },
    })
    const input = field.get('.field-input')
    expect(input.attributes('id')).toBeTruthy()
    expect(input.attributes('aria-describedby')).toBeTruthy()
    expect(field.text()).toContain('نام فارسی مشتری')

    const appInput = mount(AppInput, { props: { modelValue: 'علی' } })
    await appInput.get('input').setValue('محمد')
    expect(appInput.emitted('update:modelValue')?.[0]).toEqual(['محمد'])

    const numericInput = mount(defineComponent({
      components: { AppInput },
      setup() {
        const amount = ref<number | string>(0)
        return { amount }
      },
      template: '<AppInput v-model.number="amount" type="number" />',
    }))
    await numericInput.get('input').setValue('42')
    expect((numericInput.vm as unknown as { amount: number | string }).amount).toBe(42)
    await numericInput.get('input').setValue('')
    expect((numericInput.vm as unknown as { amount: number | string }).amount).toBe('')

    const booleanCheckbox = mount(defineComponent({
      components: { AppCheckbox },
      setup() {
        const enabled = ref(false)
        return { enabled }
      },
      template: '<AppCheckbox v-model="enabled" />',
    }))
    expect(booleanCheckbox.get('input').classes()).toContain('ui-checkbox')
    await booleanCheckbox.get('input').setValue(true)
    expect((booleanCheckbox.vm as unknown as { enabled: boolean }).enabled).toBe(true)

    const arrayCheckbox = mount(defineComponent({
      components: { AppCheckbox },
      setup() {
        const selected = ref<string[]>(['users'])
        return { selected }
      },
      template: '<AppCheckbox v-model="selected" value="customers" />',
    }))
    await arrayCheckbox.get('input').setValue(true)
    expect((arrayCheckbox.vm as unknown as { selected: string[] }).selected).toEqual(['users', 'customers'])
    await arrayCheckbox.get('input').setValue(false)
    expect((arrayCheckbox.vm as unknown as { selected: string[] }).selected).toEqual(['users'])

    const controlledCheckbox = mount(AppCheckbox, { props: { checked: true } })
    expect((controlledCheckbox.get('input').element as HTMLInputElement).checked).toBe(true)

    const select = mount(AppSelect, {
      props: {
        modelValue: 'tier1',
        options: [
          { value: 'tier1', label: 'سطح ۱' },
          { value: 'tier2', label: 'سطح ۲' },
        ],
      },
    })
    await select.get('select').setValue('tier2')
    expect(select.emitted('update:modelValue')?.[0]).toEqual(['tier2'])

    const textarea = mount(AppTextarea, { props: { modelValue: 'توضیح' } })
    await textarea.get('textarea').setValue('شرح وظیفه')
    expect(textarea.emitted('update:modelValue')?.[0]).toEqual(['شرح وظیفه'])
    const focusSpy = vi.spyOn(HTMLTextAreaElement.prototype, 'focus').mockImplementation(() => undefined)
    const originalScrollIntoView = HTMLTextAreaElement.prototype.scrollIntoView
    const scrollSpy = vi.fn()
    Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollSpy,
    })
    ;(textarea.vm as unknown as { focus: () => void; scrollIntoView: () => void }).focus()
    ;(textarea.vm as unknown as { focus: () => void; scrollIntoView: () => void }).scrollIntoView()
    expect(focusSpy).toHaveBeenCalled()
    expect(scrollSpy).toHaveBeenCalled()
    focusSpy.mockRestore()
    if (originalScrollIntoView) {
      Object.defineProperty(HTMLTextAreaElement.prototype, 'scrollIntoView', {
        configurable: true,
        value: originalScrollIntoView,
      })
    } else {
      delete (HTMLTextAreaElement.prototype as Partial<HTMLTextAreaElement>).scrollIntoView
    }
  })

  it('renders shared empty, loading, error, and danger states', () => {
    const empty = mount(AppEmptyState, {
      props: { title: 'موردی وجود ندارد', message: 'بعد از ایجاد، اینجا نمایش داده می‌شود.' },
      slots: { actions: '<button>افزودن</button>' },
    })
    expect(empty.text()).toContain('افزودن')

    const loading = mount(AppLoadingState, { props: { label: 'در حال دریافت مشتریان' } })
    expect(loading.attributes('role')).toBe('status')

    const error = mount(AppErrorState, {
      props: { title: 'خطا', message: 'دوباره تلاش کنید.' },
    })
    expect(error.attributes('role')).toBe('alert')

    const danger = mount(AppDangerZone, {
      props: { title: 'اقدامات حساس', description: 'این عملیات نیاز به تأیید دارد.' },
      slots: { default: '<button>قطع رابطه</button>' },
    })
    expect(danger.text()).toContain('قطع رابطه')
  })

  it('renders list items and confirm dialogs with action events', async () => {
    const item = mount(AppListItem, {
      props: {
        title: 'حسن رضایی',
        description: 'مشتری سطح ۲',
        meta: 'فعال',
        interactive: true,
      },
      slots: {
        leading: '<span>ح</span>',
      },
    })
    expect(item.element.tagName).toBe('BUTTON')
    expect(item.text()).toContain('فعال')
    await item.trigger('click')
    expect(item.emitted('select')).toHaveLength(1)

    const trigger = document.createElement('button')
    trigger.textContent = 'open'
    document.body.appendChild(trigger)
    trigger.focus()

    const dialog = mount(AppConfirmDialog, {
      props: {
        open: true,
        title: 'قطع رابطه',
        message: 'این عملیات نیاز به تأیید دارد.',
        confirmLabel: 'قطع رابطه',
        tone: 'danger',
      },
      attachTo: document.body,
    })
    await nextTick()
    expect(dialog.find('[role="dialog"]').exists()).toBe(true)
    expect(document.activeElement).toBe(dialog.findAll('button')[0]!.element)
    expect(document.body.classList.contains('ui-overlay-open')).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(dialog.emitted('cancel')).toHaveLength(1)
    await dialog.findAll('button')[0]!.trigger('click')
    await dialog.findAll('button')[1]!.trigger('click')
    expect(dialog.emitted('cancel')).toHaveLength(2)
    expect(dialog.emitted('confirm')).toHaveLength(1)
    dialog.unmount()
    await nextTick()
    expect(document.body.classList.contains('ui-overlay-open')).toBe(false)
    trigger.remove()
  })

  it('renders page, workspace, master-detail, toolbar, and page header primitives', () => {
    const page = mount(AppPage, {
      props: { narrow: true },
      slots: { default: '<section>محتوای صفحه</section>' },
    })
    expect(page.classes()).toContain('ui-page--narrow')
    expect(page.text()).toContain('محتوای صفحه')

    const workspace = mount(AppWorkspace, {
      slots: { default: '<section>فضای کاری</section>' },
    })
    expect(workspace.classes()).toContain('ui-workspace')

    const header = mount(AppPageHeader, {
      props: {
        eyebrow: 'عملیات',
        title: 'مشتریان',
        description: 'مدیریت روابط مشتریان',
      },
      slots: { actions: '<button>افزودن</button>' },
    })
    expect(header.text()).toContain('مدیریت روابط مشتریان')
    expect(header.find('.ui-page-header__actions').exists()).toBe(true)

    const masterDetail = mount(AppMasterDetail, {
      slots: {
        master: '<p>لیست</p>',
        detail: '<p>جزئیات</p>',
      },
    })
    expect(masterDetail.text()).toContain('جزئیات')

    const toolbar = mount(AppToolbar, {
      slots: {
        leading: '<span>فیلتر</span>',
        default: '<input aria-label="جستجو" />',
        actions: '<button>اعمال</button>',
      },
    })
    expect(toolbar.find('.ui-toolbar__actions').exists()).toBe(true)
  })

  it('renders disclosures with accessible toggle and controlled panels', async () => {
    const disclosure = mount(AppDisclosure, {
      props: {
        title: 'لیست همکاران',
        description: 'اعضای قابل مشاهده پروژه',
        open: false,
        titleId: 'project-users-title',
        panelId: 'project-users-panel',
        toggleClass: 'custom-toggle',
        panelClass: 'custom-panel',
      },
      slots: {
        leading: '<span>icon</span>',
        meta: '<span>meta</span>',
        default: '<p>محتوا</p>',
      },
    })

    const toggle = disclosure.get('.ui-disclosure__toggle')
    expect(toggle.classes()).toContain('custom-toggle')
    expect(disclosure.attributes('aria-labelledby')).toBe('project-users-title')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(toggle.attributes('aria-controls')).toBe('project-users-panel')
    expect(disclosure.find('#project-users-panel').exists()).toBe(false)

    await toggle.trigger('click')
    expect(disclosure.emitted('toggle')).toHaveLength(1)

    await disclosure.setProps({ open: true })
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(disclosure.get('#project-users-panel').classes()).toContain('custom-panel')
    expect(disclosure.get('#project-users-panel').text()).toContain('محتوا')
  })

  it('supports search fields, filter chips, and number steppers', async () => {
    const search = mount(AppSearchField, {
      props: { modelValue: '', placeholder: 'جستجوی مشتری' },
    })
    await search.get('input').setValue('علی')
    expect(search.emitted('update:modelValue')?.[0]).toEqual(['علی'])

    const chips = mount(AppFilterChips, {
      props: {
        modelValue: 'all',
        label: 'فیلتر مشتریان',
        options: [
          { key: 'all', label: 'همه' },
          { key: 'active', label: 'فعال' },
        ],
      },
    })
    await chips.findAll('[role="tab"]')[1]!.trigger('click')
    expect(chips.emitted('update:modelValue')?.[0]).toEqual(['active'])

    const stepper = mount(AppNumberStepper, {
      props: { modelValue: 0.5, min: 0, max: 2, step: 0.1, label: 'درصد کمیسیون' },
    })
    await stepper.findAll('button')[1]!.trigger('click')
    expect(stepper.emitted('update:modelValue')?.[0]).toEqual([0.6])
    await stepper.get('input').setValue('1.23')
    expect(stepper.emitted('update:modelValue')?.[1]).toEqual([1.23])
  })

  it('renders toast, bottom sheet, and responsive dialog primitives', async () => {
    const toast = mount(AppToast, {
      props: { title: 'ذخیره شد', message: 'تغییرات با موفقیت ذخیره شد.', tone: 'success' },
    })
    expect(toast.attributes('role')).toBe('status')
    expect(toast.attributes('aria-live')).toBe('polite')
    expect(toast.classes()).toContain('ui-toast--success')

    const alertToast = mount(AppToast, {
      props: { title: 'خطا', role: 'alert', tone: 'danger' },
      slots: {
        icon: '<span class="toast-icon">!</span>',
        default: '<p>پیام با slot</p>',
      },
    })
    expect(alertToast.attributes('role')).toBe('alert')
    expect(alertToast.attributes('aria-live')).toBe('assertive')
    expect(alertToast.find('.ui-toast__icon .toast-icon').exists()).toBe(true)
    expect(alertToast.text()).toContain('پیام با slot')

    const sheetTrigger = document.createElement('button')
    sheetTrigger.textContent = 'open-sheet'
    document.body.appendChild(sheetTrigger)
    sheetTrigger.focus()

    const sheet = mount(AppBottomSheet, {
      props: { open: true, title: 'فیلترها' },
      slots: { default: '<p>گزینه‌ها</p>', actions: '<button>اعمال</button>' },
      attachTo: document.body,
    })
    await nextTick()
    const sheetDialog = document.body.querySelector('.ui-bottom-sheet') as HTMLElement | null
    expect(sheetDialog).toBeTruthy()
    expect(sheetDialog?.getAttribute('aria-labelledby')).toBeTruthy()
    expect(document.activeElement).toBe(sheet.findComponent(AppButton).element)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(sheet.emitted('close')).toHaveLength(1)
    await sheet.findComponent(AppButton).trigger('click')
    expect(sheet.emitted('close')).toHaveLength(2)
    sheet.unmount()
    await nextTick()
    expect(document.activeElement).toBe(sheetTrigger)
    sheetTrigger.remove()

    const dialogTrigger = document.createElement('button')
    dialogTrigger.textContent = 'open-dialog'
    document.body.appendChild(dialogTrigger)
    dialogTrigger.focus()

    const dialog = mount(AppResponsiveDialog, {
      props: { open: true, title: 'جزئیات' },
      slots: { default: '<input aria-label="نام" /><button>ذخیره</button>' },
      attachTo: document.body,
    })
    await nextTick()
    const dialogElement = document.body.querySelector('.ui-responsive-dialog') as HTMLElement | null
    const dialogInput = document.body.querySelector('.ui-responsive-dialog input') as HTMLInputElement | null
    const dialogButtons = Array.from(document.body.querySelectorAll('.ui-responsive-dialog button')) as HTMLButtonElement[]
    expect(dialogElement).toBeTruthy()
    expect(dialogInput).toBeTruthy()
    expect(dialogButtons).toHaveLength(2)
    expect(document.activeElement).toBe(dialog.findComponent(AppButton).element)
    await dialog.findComponent(AppButton).trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(dialogButtons[1]!)
    dialogButtons[1]!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    await nextTick()
    expect(document.activeElement).toBe(dialog.findComponent(AppButton).element)
    dialog.unmount()
    await nextTick()
    expect(document.activeElement).toBe(dialogTrigger)
    dialogTrigger.remove()
  })
})
