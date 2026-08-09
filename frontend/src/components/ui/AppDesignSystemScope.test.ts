import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import AppDesignSystemScope from './AppDesignSystemScope.vue'

describe('AppDesignSystemScope', () => {
  it('creates an explicit V2 scope without changing DOM outside the provider', () => {
    const outside = document.createElement('div')
    outside.className = 'legacy-surface'
    document.body.appendChild(outside)

    const wrapper = mount(AppDesignSystemScope, {
      attrs: {
        'aria-label': 'V2 catalog',
        class: 'catalog-scope',
      },
      slots: {
        default: '<p data-test="content">محتوا</p>',
      },
    })

    expect(wrapper.element.tagName).toBe('DIV')
    expect(wrapper.attributes('data-ui-system')).toBe('v2')
    expect(wrapper.attributes('aria-label')).toBe('V2 catalog')
    expect(wrapper.classes()).toEqual(expect.arrayContaining(['ui-v2-scope', 'catalog-scope']))
    expect(wrapper.get('[data-test="content"]').text()).toBe('محتوا')
    expect(outside.hasAttribute('data-ui-system')).toBe(false)
    expect(outside.getAttribute('style')).toBeNull()

    wrapper.unmount()
    outside.remove()
  })

  it('supports semantic roots and an explicit portal scope', () => {
    const root = mount(AppDesignSystemScope, {
      props: { as: 'section' },
      attrs: { 'data-ui-system': 'legacy' },
    })
    expect(root.element.tagName).toBe('SECTION')
    expect(root.attributes('data-ui-system')).toBe('v2')

    const portal = mount(AppDesignSystemScope, {
      props: { as: 'aside', kind: 'portal' },
    })
    expect(portal.element.tagName).toBe('ASIDE')
    expect(portal.attributes('data-ui-system')).toBe('v2-portal')
  })
})
