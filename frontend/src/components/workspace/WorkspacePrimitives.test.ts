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
    expect(wrapper.element.tagName).toBe('SECTION')
    expect(wrapper.classes()).toContain('ds-workspace--split')
    expect(wrapper.attributes('data-ui-system')).toBeUndefined()
    expect(wrapper.classes()).not.toContain('ui-v2-workspace-adapter')
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

  it('opts the shell into the V2 workspace root without changing the legacy default', async () => {
    const wrapper = mount(WorkspaceShell, {
      props: {
        title: 'عملیات',
        layout: 'stack',
        showBack: true,
        v2Scope: true,
      },
      slots: {
        default: '<p class="test-main">محتوا</p>',
      },
    })

    expect(wrapper.element.tagName).toBe('MAIN')
    expect(wrapper.attributes('data-ui-system')).toBe('v2')
    expect(wrapper.classes()).toContain('ui-v2-scope')
    expect(wrapper.classes()).toContain('ui-workspace')
    expect(wrapper.classes()).toContain('ui-workspace--narrow')
    expect(wrapper.classes()).toContain('ui-v2-workspace-adapter')
    expect(wrapper.find('.ui-v2-workspace-adapter__header').exists()).toBe(true)
    expect(wrapper.find('main main').exists()).toBe(false)

    await wrapper.get('.ui-v2-workspace-adapter__back').trigger('click')
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
    expect(section.attributes('data-ui-system')).toBeUndefined()

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
    expect(notice.attributes('data-ui-system')).toBeUndefined()

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
    expect(stat.attributes('data-ui-system')).toBeUndefined()

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
    expect(danger.attributes('data-ui-system')).toBeUndefined()
  })

  it('supports standalone V2 opt-in on every leaf workspace adapter', () => {
    const section = mount(WorkspaceSection, {
      props: { title: 'بخش', v2Scope: true },
    })
    const notice = mount(WorkspaceNotice, {
      props: { title: 'پیام', v2Scope: true },
    })
    const stat = mount(WorkspaceStatTile, {
      props: { label: 'برچسب', value: '۱', v2Scope: true },
    })
    const danger = mount(WorkspaceDangerZone, {
      props: { title: 'خطر', v2Scope: true },
    })
    const action = mount(WorkspaceActionTile, {
      props: { title: 'اقدام', v2Scope: true },
    })

    for (const wrapper of [section, notice, stat, danger, action]) {
      expect(wrapper.attributes('data-ui-system')).toBe('v2')
    }
    expect(section.classes()).toContain('ui-v2-workspace-section-adapter')
    expect(notice.classes()).toContain('ui-v2-workspace-notice-adapter')
    expect(stat.classes()).toContain('ui-v2-workspace-stat-adapter')
    expect(danger.classes()).toContain('ui-v2-workspace-danger-adapter')
    expect(action.classes()).toContain('ui-v2-workspace-action-adapter')
    for (const wrapper of [section, notice, stat, danger, action]) {
      expect(wrapper.classes()).toContain('ui-v2-scope')
    }
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
    expect(wrapper.classes()).toContain('ui-list-item')
    expect(wrapper.classes()).toContain('ui-list-item--interactive')
    expect(wrapper.classes()).toContain('is-active')
    expect(wrapper.attributes('data-ui-system')).toBeUndefined()
    expect(wrapper.text()).toContain('مدیریت مشتریان')
    expect(wrapper.text()).toContain('فعال')

    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toHaveLength(1)

    await wrapper.setProps({ disabled: true })
    await wrapper.trigger('click')
    expect(wrapper.emitted('select')).toHaveLength(1)
  })
})
