import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import {
  WorkspaceActionTile,
  WorkspaceDangerZone,
  WorkspaceNotice,
  WorkspaceSection,
  WorkspaceShell,
  WorkspaceStatTile,
} from './index'

describe('workspace primitives', () => {
  it('renders an accessible workspace shell with toolbar, aside, actions, and back event', async () => {
    const wrapper = mount(WorkspaceShell, {
      props: {
        title: 'مشتریان',
        eyebrow: 'عملیات',
        description: 'مدیریت روابط مشتریان',
        layout: 'split',
        showBack: true,
      },
      slots: {
        actions: '<button class="test-action">افزودن</button>',
        toolbar: '<div class="test-toolbar">فیلترها</div>',
        default: '<p class="test-main">لیست مشتریان</p>',
        aside: '<p class="test-aside">خلاصه</p>',
      },
    })

    const heading = wrapper.find('h1')
    expect(wrapper.classes()).toContain('ds-workspace--split')
    expect(wrapper.attributes('aria-labelledby')).toBe(heading.attributes('id'))
    expect(wrapper.text()).toContain('عملیات')
    expect(wrapper.text()).toContain('مدیریت روابط مشتریان')
    expect(wrapper.find('.test-toolbar').exists()).toBe(true)
    expect(wrapper.find('.test-main').exists()).toBe(true)
    expect(wrapper.find('.test-aside').exists()).toBe(true)
    expect(wrapper.find('.test-action').exists()).toBe(true)

    await wrapper.find('.ds-workspace-back').trigger('click')
    expect(wrapper.emitted('back')).toHaveLength(1)
  })

  it('renders section, notice, stat, and danger zone primitives with stable classes', () => {
    const section = mount(WorkspaceSection, {
      props: {
        title: 'محدودیت‌ها',
        description: 'تنظیم سقف معاملات',
        tone: 'warning',
      },
      slots: {
        actions: '<button>ذخیره</button>',
        default: '<div class="section-content">فرم</div>',
      },
    })
    expect(section.classes()).toContain('ds-workspace-section--warning')
    expect(section.classes()).toContain('ui-section-card')
    expect(section.classes()).toContain('ui-section-card--warning')
    expect(section.text()).toContain('محدودیت‌ها')
    expect(section.find('.section-content').exists()).toBe(true)
    expect(section.find('.ui-section-card__actions .ds-workspace-section-actions').exists()).toBe(true)

    const notice = mount(WorkspaceNotice, {
      props: {
        title: 'ذخیره شد',
        message: 'تغییرات با موفقیت ثبت شد.',
        tone: 'success',
      },
    })
    expect(notice.attributes('role')).toBe('status')
    expect(notice.classes()).toContain('ds-workspace-notice--success')
    expect(notice.classes()).toContain('ui-toast')
    expect(notice.classes()).toContain('ui-toast--success')

    const stat = mount(WorkspaceStatTile, {
      props: {
        label: 'تعداد معاملات',
        value: '۱۲',
        hint: 'در ۷ روز گذشته',
        tone: 'primary',
      },
    })
    expect(stat.classes()).toContain('ds-stat-tile--primary')
    expect(stat.classes()).toContain('ui-metric-card')
    expect(stat.classes()).toContain('ui-metric-card--primary')
    expect(stat.text()).toContain('۱۲')

    const danger = mount(WorkspaceDangerZone, {
      props: {
        title: 'اقدامات حساس',
        description: 'این عملیات قابل بازگشت نیست.',
      },
      slots: {
        default: '<button class="danger-action">قطع رابطه</button>',
      },
    })
    expect(danger.classes()).toContain('ui-danger-zone')
    expect(danger.find('.danger-action').exists()).toBe(true)
    expect(danger.text()).toContain('اقدامات حساس')
  })

  it('emits action tile selection and respects disabled state', async () => {
    const wrapper = mount(WorkspaceActionTile, {
      props: {
        title: 'مدیریت مشتریان',
        description: 'مشاهده و ویرایش روابط',
        badge: 'فعال',
        active: true,
        tone: 'primary',
      },
      slots: {
        icon: '<span>●</span>',
      },
    })

    expect(wrapper.classes()).toContain('ds-action-tile--primary')
    expect(wrapper.classes()).toContain('ui-action-card')
    expect(wrapper.classes()).toContain('ui-action-card--primary')
    expect(wrapper.classes()).toContain('is-active')
    expect(wrapper.text()).toContain('مدیریت مشتریان')
    expect(wrapper.text()).toContain('فعال')

    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toHaveLength(1)

    await wrapper.setProps({ disabled: true })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toHaveLength(1)
  })
})
