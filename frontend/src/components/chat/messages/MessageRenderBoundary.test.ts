import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

import MessageRenderBoundary from './MessageRenderBoundary.vue'

const ThrowingMessage = defineComponent({
  setup() {
    return () => {
      throw new Error('broken message payload')
    }
  },
})

const StableMessage = defineComponent({
  setup() {
    return () => h('div', { class: 'stable-message' }, 'ok')
  },
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MessageRenderBoundary', () => {
  it('renders a local fallback and reports diagnostics once for a bad message', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)

    const wrapper = mount(MessageRenderBoundary, {
      props: {
        messageId: 10,
        renderKey: 'initial',
      },
      slots: {
        default: ThrowingMessage,
      },
    })

    await nextTick()

    expect(wrapper.text()).toContain('این پیام قابل نمایش نیست')
    expect(warnSpy).toHaveBeenCalledTimes(1)
    expect(warnSpy.mock.calls[0][0]).toBe('[messenger] message render failed')
    expect(warnSpy.mock.calls[0][1]).toMatchObject({ messageId: 10 })
  })

  it('resets the fallback when the message identity changes', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const shouldThrow = ref(true)

    const Harness = defineComponent({
      setup() {
        const messageId = ref(20)
        return { messageId, shouldThrow }
      },
      render() {
        return h(
          MessageRenderBoundary,
          { messageId: this.messageId, renderKey: 'same-search-state' },
          {
            default: () => (this.shouldThrow ? h(ThrowingMessage) : h(StableMessage)),
          },
        )
      },
    })

    const wrapper = mount(Harness)
    await nextTick()

    expect(wrapper.text()).toContain('این پیام قابل نمایش نیست')

    shouldThrow.value = false
    wrapper.vm.messageId = 21
    await nextTick()
    await nextTick()

    expect(wrapper.find('.stable-message').text()).toBe('ok')
    expect(warnSpy).toHaveBeenCalledTimes(1)
  })
})
