import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import {
  AppActionCard,
  AppButton,
  AppDangerZone,
  AppEmptyState,
  AppErrorState,
  AppFormField,
  AppInput,
  AppListItem,
  AppLoadingState,
  AppMetricCard,
  AppSectionCard,
  AppSelect,
  AppStatusBadge,
  AppTabs,
  AppTextarea,
  AppConfirmDialog,
} from './index'

describe('ui primitives', () => {
  it('renders buttons, action cards, metrics, badges, and sections with stable contracts', async () => {
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

    const section = mount(AppSectionCard, {
      props: { title: 'تنظیمات', description: 'بخش‌های قابل ویرایش' },
      slots: { actions: '<button>عملیات</button>', default: '<p>محتوا</p>' },
    })
    expect(section.find('.ui-section-card__actions').exists()).toBe(true)
    expect(section.text()).toContain('محتوا')
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

    const dialog = mount(AppConfirmDialog, {
      props: {
        open: true,
        title: 'قطع رابطه',
        message: 'این عملیات نیاز به تأیید دارد.',
        confirmLabel: 'قطع رابطه',
        tone: 'danger',
      },
    })
    expect(dialog.find('[role="dialog"]').exists()).toBe(true)
    await dialog.findAll('button')[0]!.trigger('click')
    await dialog.findAll('button')[1]!.trigger('click')
    expect(dialog.emitted('cancel')).toHaveLength(1)
    expect(dialog.emitted('confirm')).toHaveLength(1)
  })
})
