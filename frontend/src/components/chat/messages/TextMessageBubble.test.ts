import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import TextMessageBubble from './TextMessageBubble.vue'

describe('TextMessageBubble', () => {
  it('renders highlighted html and emits content clicks', async () => {
    const wrapper = mount(TextMessageBubble, {
      props: {
        html: 'سلام <mark>بازار</mark>',
      },
    })

    await wrapper.get('p').trigger('click')

    expect(wrapper.find('mark').text()).toBe('بازار')
    expect(wrapper.emitted('content-click')).toHaveLength(1)
  })
})
