import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ReplyPreview from './ReplyPreview.vue'

describe('ReplyPreview', () => {
  it('emits the replied message id when clicked', async () => {
    const wrapper = mount(ReplyPreview, {
      props: {
        reply: {
          id: 44,
          sender_id: 7,
          content: 'سلام',
          message_type: 'text',
        },
        currentUserId: 3,
        selectedUserName: 'محمد',
        isSent: false,
      },
    })

    await wrapper.get('.reply-context').trigger('click')

    expect(wrapper.emitted('scroll-to')).toEqual([[44]])
    expect(wrapper.text()).toContain('محمد')
    expect(wrapper.text()).toContain('سلام')
  })

  it('uses stable Persian previews for media replies', () => {
    const wrapper = mount(ReplyPreview, {
      props: {
        reply: {
          id: 45,
          sender_id: 3,
          content: '',
          message_type: 'voice',
        },
        currentUserId: 3,
        selectedUserName: 'رضا',
        isSent: true,
      },
    })

    expect(wrapper.text()).toContain('شما')
    expect(wrapper.text()).toContain('پیام صوتی')
    expect(wrapper.find('.reply-context').classes()).toContain('is-sent')
  })
})
