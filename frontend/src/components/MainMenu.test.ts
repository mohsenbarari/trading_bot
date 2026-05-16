import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MainMenu from './MainMenu.vue'

describe('MainMenu.vue', () => {
  it('shows the admin navigation set for super admins and emits the matching destinations', async () => {
    const wrapper = mount(MainMenu, {
      props: {
        userRole: 'مدیر ارشد',
        isTradePageVisible: false,
      },
    })

    expect(wrapper.text()).toContain('پنل مدیریت')
    expect(wrapper.text()).not.toContain('تنظیمات')

    const buttons = wrapper.findAll('button')
    await buttons[0]!.trigger('click')
    await buttons[1]!.trigger('click')
    await buttons[2]!.trigger('click')
    await buttons[3]!.trigger('click')

    expect(wrapper.emitted('navigate')).toEqual([
      ['trade'],
      ['profile'],
      ['chat'],
      ['admin_panel'],
    ])
  })

  it('shows the standard user actions when the viewer is not a super admin', async () => {
    const wrapper = mount(MainMenu, {
      props: {
        userRole: 'عادی',
        isTradePageVisible: true,
      },
    })

    expect(wrapper.text()).toContain('تنظیمات')
    expect(wrapper.text()).not.toContain('پنل مدیریت')

    const buttons = wrapper.findAll('button')
    await buttons[1]!.trigger('click')
    await buttons[2]!.trigger('click')
    await buttons[3]!.trigger('click')

    expect(wrapper.emitted('navigate')).toEqual([
      ['profile'],
      ['chat'],
      ['user_settings'],
    ])
  })
})