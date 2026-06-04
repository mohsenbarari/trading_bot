import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ForwardedHeader from './ForwardedHeader.vue'

describe('ForwardedHeader', () => {
  it('emits profile opens when the forwarded source is actionable', async () => {
    const wrapper = mount(ForwardedHeader, {
      props: {
        name: 'کانال بازار',
        canOpenProfile: true,
        isSent: false,
      },
    })

    await wrapper.get('.forward-link').trigger('click')

    expect(wrapper.text()).toContain('از کانال بازار')
    expect(wrapper.emitted('open-profile')).toEqual([[]])
  })

  it('renders non-actionable forwarded sources without a button', () => {
    const wrapper = mount(ForwardedHeader, {
      props: {
        name: 'فرستنده ناشناس',
        canOpenProfile: false,
        isSent: true,
      },
    })

    expect(wrapper.find('.forward-link').exists()).toBe(false)
    expect(wrapper.find('.forwarded-banner').classes()).toContain('is-sent')
  })
})
