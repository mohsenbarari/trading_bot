import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AuthFlowShell from './AuthFlowShell.vue'

describe('AuthFlowShell', () => {
  it('renders the canonical product identity without authenticated navigation', () => {
    const wrapper = mount(AuthFlowShell, {
      props: {
        title: 'ورود به سامانه',
        description: 'شماره موبایل ثبت‌شده را وارد کنید.',
      },
      slots: {
        default: '<button type="button">ادامه</button>',
      },
    })

    expect(wrapper.element.tagName).toBe('MAIN')
    expect(wrapper.findAll('main')).toHaveLength(1)
    expect(wrapper.get('.ui-v2-public-header').text()).toContain('سامانه معاملات')
    expect(wrapper.get('h1').text()).toBe('ورود به سامانه')
    expect(wrapper.text()).toContain('شماره موبایل ثبت‌شده را وارد کنید.')
    expect(wrapper.get('img').attributes('src')).toMatch(
      /(?:uiux-v2-brand-mark\.svg|data:image\/svg\+xml)/,
    )
    expect(wrapper.find('nav').exists()).toBe(false)
    expect(wrapper.find('.ui-v2-auth-progress').exists()).toBe(false)
    expect(wrapper.classes('ui-v2-auth-flow--viewport-fill')).toBe(false)
  })

  it('renders an exact bounded progress model for real multi-step flows', () => {
    const wrapper = mount(AuthFlowShell, {
      props: {
        title: 'ثبت‌نام',
        currentStep: 2,
        totalSteps: 3,
      },
    })

    expect(wrapper.get('.ui-v2-auth-progress').text()).toContain('مرحله ۲ از ۳')
    const segments = wrapper.findAll('.ui-v2-auth-progress__bars > span')
    expect(segments).toHaveLength(3)
    expect(segments.filter((segment) => segment.classes('is-current'))).toHaveLength(2)
  })

  it('fails closed to no progress for invalid step metadata', () => {
    const wrapper = mount(AuthFlowShell, {
      props: {
        title: 'ثبت‌نام',
        currentStep: 4,
        totalSteps: 3,
      },
    })

    expect(wrapper.find('.ui-v2-auth-progress').exists()).toBe(false)
  })

  it('enables viewport fill only when a caller explicitly opts in', () => {
    const wrapper = mount(AuthFlowShell, {
      props: {
        title: 'ورود به سامانه',
        fillViewport: true,
      },
    })

    expect(wrapper.classes('ui-v2-auth-flow--viewport-fill')).toBe(true)
  })
})
