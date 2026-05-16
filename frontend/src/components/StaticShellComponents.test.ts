import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import HomePage from './HomePage.vue'
import PlaceholderView from './PlaceholderView.vue'
import UserSettings from './UserSettings.vue'

describe('static shell components', () => {
  it('renders the empty home page shell', () => {
    const wrapper = mount(HomePage, {
      props: {
        userRole: 'standard',
      },
    })

    expect(wrapper.find('.home-page').exists()).toBe(true)
    expect(wrapper.text()).toBe('')
  })

  it('renders the generic placeholder title and description', () => {
    const wrapper = mount(PlaceholderView, {
      props: {
        title: 'گزارش‌ها',
      },
    })

    expect(wrapper.text()).toContain('گزارش‌ها')
    expect(wrapper.text()).toContain('این بخش در حال توسعه است')
  })

  it('renders the user-settings coming-soon state', () => {
    const wrapper = mount(UserSettings)

    expect(wrapper.text()).toContain('در حال ساخت')
    expect(wrapper.text()).toContain('تنظیمات پنل کاربری به زودی اضافه خواهد شد.')
  })
})