import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

describe('chat skeleton components', () => {
  beforeEach(() => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the requested number of conversation skeleton items with deterministic widths', async () => {
    const ConversationSkeleton = (await import('./ConversationSkeleton.vue')).default
    const wrapper = mount(ConversationSkeleton, {
      props: {
        count: 3,
      },
    })

    expect(wrapper.findAll('.skeleton-item')).toHaveLength(3)
    expect(wrapper.find('.skeleton-name').attributes('style')).toContain('width: 65%')
    expect(wrapper.find('.skeleton-preview').attributes('style')).toContain('width: 75%')
  })

  it('renders chat skeleton rows with sent and received layouts plus conditional short lines', async () => {
    const ChatSkeleton = (await import('./ChatSkeleton.vue')).default
    const wrapper = mount(ChatSkeleton, {
      props: {
        count: 4,
      },
    })

    const rows = wrapper.findAll('.skel-msg')
    expect(rows).toHaveLength(4)
    expect(rows[0]!.classes()).toContain('skel-received')
    expect(rows[2]!.classes()).toContain('skel-sent')
    expect(wrapper.findAll('.skel-text.short')).toHaveLength(3)
    expect(wrapper.find('.skel-bubble').attributes('style')).toContain('width: 52.5%')
  })
})