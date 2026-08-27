import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import HelpPopover from './HelpPopover.vue'

const helpPopoverSource = readFileSync(
  resolve(process.cwd(), 'src/components/HelpPopover.vue'),
  'utf8',
)

function componentSource(name: string) {
  return readFileSync(resolve(process.cwd(), `src/components/${name}`), 'utf8')
}

function helpPopoverCalls(source: string) {
  return source.match(/<HelpPopover\b[\s\S]*?\/>/g) || []
}

afterEach(() => {
  vi.useRealTimers()
  document.body.replaceChildren()
})

describe('HelpPopover.vue', () => {
  it('keeps the 48px default target and the same comfortable-target rule', () => {
    const wrapper = mount(HelpPopover, {
      props: { text: 'راهنمای کوتاه', label: 'راهنمای آزمایشی' },
    })
    const trigger = wrapper.get('.help-popover-trigger')
    const defaultRule = helpPopoverSource.match(/\.help-popover-trigger\s*\{([\s\S]*?)\n\}/)?.[1]
    const comfortableRule = helpPopoverSource.match(
      /\.help-popover--comfortable-target\s+\.help-popover-trigger\s*\{([\s\S]*?)\n\}/,
    )?.[1]

    expect(wrapper.get('.help-popover').classes()).not.toContain('help-popover--comfortable-target')
    expect(trigger.attributes('aria-label')).toBe('راهنمای آزمایشی')
    expect(defaultRule).toContain('width: 3rem;')
    expect(defaultRule).toContain('height: 3rem;')
    expect(comfortableRule).toContain('box-sizing: border-box;')
    expect(comfortableRule).toContain('inline-size: 3rem;')
    expect(comfortableRule).toContain('block-size: 3rem;')
    expect(comfortableRule).toContain('min-inline-size: 3rem;')
    expect(comfortableRule).toContain('min-block-size: 3rem;')
  })

  it('adds the comfortable-target modifier only when requested', () => {
    const wrapper = mount(HelpPopover, {
      props: { text: 'راهنمای کوتاه', comfortableTarget: true },
    })

    expect(wrapper.get('.help-popover').classes()).toContain('help-popover--comfortable-target')
  })

  it('opens its note from the trigger without bubbling the click and retains the existing timeout', async () => {
    vi.useFakeTimers()
    const bodyClick = vi.fn()
    document.body.addEventListener('click', bodyClick)
    const wrapper = mount(HelpPopover, {
      attachTo: document.body,
      props: { text: 'راهنمای قابل مشاهده', label: 'باز کردن راهنما' },
    })

    try {
      await wrapper.get('.help-popover-trigger').trigger('click')

      expect(bodyClick).not.toHaveBeenCalled()
      expect(wrapper.get('.help-popover-note').text()).toBe('راهنمای قابل مشاهده')

      await vi.advanceTimersByTimeAsync(6000)
      expect(wrapper.find('.help-popover-note').exists()).toBe(false)
    } finally {
      document.body.removeEventListener('click', bodyClick)
      wrapper.unmount()
    }
  })

  it('keeps live profile and operations surfaces free of HelpPopover', () => {
    const publicProfile = componentSource('PublicProfile.vue')
    const userProfile = componentSource('UserProfile.vue')

    expect(helpPopoverCalls(publicProfile)).toHaveLength(0)
    expect(helpPopoverCalls(userProfile)).toHaveLength(0)
    expect(helpPopoverCalls(componentSource('CreateChannelView.vue'))).toHaveLength(0)
    expect(helpPopoverCalls(componentSource('OwnerCustomerManagerModal.vue'))).toHaveLength(0)
    expect(helpPopoverCalls(componentSource('OwnerAccountantManagerModal.vue'))).toHaveLength(0)

    expect(componentSource('AdminMessagesView.vue')).not.toContain('comfortable-target')
  })
})
