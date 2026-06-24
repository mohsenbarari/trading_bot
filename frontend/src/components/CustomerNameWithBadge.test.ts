import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CustomerNameWithBadge from './CustomerNameWithBadge.vue'

describe('CustomerNameWithBadge.vue', () => {
  it('renders the normalized customer name with the customer badge', () => {
    const wrapper = mount(CustomerNameWithBadge, {
      props: {
        name: '  مشتری تهران  ',
        compact: true,
      },
    })

    expect(wrapper.classes()).toContain('compact')
    expect(wrapper.get('.customer-name-with-badge__name').text()).toBe('مشتری تهران')
    expect(wrapper.get('.customer-name-with-badge__badge').text()).toBe('مشتری')
  })

  it('falls back to the customer label when the name is empty', () => {
    const wrapper = mount(CustomerNameWithBadge, {
      props: {
        name: '   ',
      },
    })

    expect(wrapper.get('.customer-name-with-badge__name').text()).toBe('مشتری')
    expect(wrapper.get('.customer-name-with-badge__badge').text()).toBe('مشتری')
  })
})
