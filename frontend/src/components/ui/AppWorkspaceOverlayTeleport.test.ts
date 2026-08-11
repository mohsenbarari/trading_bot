import { mount } from '@vue/test-utils'
import { defineComponent, nextTick } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'
import AppBottomSheet from './AppBottomSheet.vue'
import AppResponsiveDialog from './AppResponsiveDialog.vue'

afterEach(() => {
  document.body.innerHTML = ''
})

describe('workspace overlay teleport target', () => {
  it.each([
    ['bottom sheet', AppBottomSheet, '.ui-sheet-backdrop'],
    ['responsive dialog', AppResponsiveDialog, '.ui-responsive-dialog-backdrop'],
  ])(
    'defers %s into an in-scope host rendered by the same tree',
    async (_label, component, selector) => {
      const Parent = defineComponent({
        components: { Overlay: component },
        template: `
        <section data-ui-system="v2">
          <Overlay
            :open="true"
            title="عنوان"
            teleport-to="#workspace-overlay-test-host"
            backdrop-class="ui-v2-workspace-test-backdrop"
            panel-class="ui-v2-workspace-test-panel"
          >
            <p>محتوا</p>
          </Overlay>
          <div id="workspace-overlay-test-host" />
        </section>
      `,
      })

      const wrapper = mount(Parent, { attachTo: document.body })
      await nextTick()
      await nextTick()

      const host = wrapper.get('#workspace-overlay-test-host')
      expect(host.find(selector).exists()).toBe(true)
      expect(host.find('.ui-v2-workspace-test-backdrop').exists()).toBe(true)
      expect(host.find('.ui-v2-workspace-test-panel').exists()).toBe(true)

      wrapper.unmount()
    },
  )

  it('keeps a guarded bottom sheet open when close affordances are disabled', async () => {
    const wrapper = mount(AppBottomSheet, {
      props: {
        open: true,
        title: 'ثبت در حال انجام',
        showClose: false,
        closeOnBackdrop: false,
        closeOnEscape: false,
      },
      slots: {
        default: '<button>ادامه</button>',
      },
      attachTo: document.body,
    })
    await nextTick()

    expect(document.body.querySelectorAll('.ui-bottom-sheet button')).toHaveLength(1)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeUndefined()

    const backdrop = document.body.querySelector('.ui-sheet-backdrop') as HTMLElement | null
    expect(backdrop).toBeTruthy()
    backdrop!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(wrapper.emitted('close')).toBeUndefined()

    wrapper.unmount()
  })
})
