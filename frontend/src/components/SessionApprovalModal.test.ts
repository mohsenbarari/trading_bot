import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import SessionApprovalModal from './SessionApprovalModal.vue'
import { isSecurityLayerActive, resetSecurityLayerStateForTests } from '../utils/securityLayerState'

const sessionModalMocks = vi.hoisted(() => ({
  showModal: null as { value: boolean } | null,
  approve: null as ReturnType<typeof vi.fn> | null,
  reject: null as ReturnType<typeof vi.fn> | null,
  approveRecovery: null as ReturnType<typeof vi.fn> | null,
  rejectRecovery: null as ReturnType<typeof vi.fn> | null,
  requestRecoveryIdentity: null as ReturnType<typeof vi.fn> | null,
  openRecoveryThread: null as ReturnType<typeof vi.fn> | null,
}))

vi.mock('../composables/useSessionApprovalRuntime', async () => {
  const { ref } = await import('vue')
  sessionModalMocks.showModal = ref(false)
  sessionModalMocks.approve = vi.fn()
  sessionModalMocks.reject = vi.fn()
  sessionModalMocks.approveRecovery = vi.fn()
  sessionModalMocks.rejectRecovery = vi.fn()
  sessionModalMocks.requestRecoveryIdentity = vi.fn()
  sessionModalMocks.openRecoveryThread = vi.fn()
  return {
    useSessionApprovalRuntime: () => ({
      approve: sessionModalMocks.approve,
      approveRecovery: sessionModalMocks.approveRecovery,
      countdown: ref(0),
      loading: ref(false),
      openRecoveryThread: sessionModalMocks.openRecoveryThread,
      pendingRecovery: ref(null),
      pendingRequest: ref(null),
      reject: sessionModalMocks.reject,
      rejectRecovery: sessionModalMocks.rejectRecovery,
      requestRecoveryIdentity: sessionModalMocks.requestRecoveryIdentity,
      showModal: sessionModalMocks.showModal,
    }),
  }
})

const flushDialogOpen = async () => {
  await nextTick()
  await nextTick()
}

const getDialog = () => document.body.querySelector<HTMLElement>('[role="dialog"]')

describe('SessionApprovalModal', () => {
  beforeEach(() => {
    document.body.replaceChildren()
    resetSecurityLayerStateForTests()
    if (sessionModalMocks.showModal) sessionModalMocks.showModal.value = false
    sessionModalMocks.approve?.mockClear()
    sessionModalMocks.reject?.mockClear()
    sessionModalMocks.approveRecovery?.mockClear()
    sessionModalMocks.rejectRecovery?.mockClear()
    sessionModalMocks.requestRecoveryIdentity?.mockClear()
    sessionModalMocks.openRecoveryThread?.mockClear()
  })

  it('registers a labelled forced-choice dialog and releases its security layer on unmount', async () => {
    const wrapper = mount(SessionApprovalModal, {
      attachTo: document.body,
      props: { v2Portal: true },
    })

    sessionModalMocks.showModal!.value = true
    await flushDialogOpen()

    const layer = document.body.querySelector<HTMLElement>('.ui-v2-session-layer')
    const dialog = getDialog()
    expect(isSecurityLayerActive.value).toBe(true)
    expect(layer?.getAttribute('data-ui-system')).toBe('v2-portal')
    expect(layer?.classList.contains('z-[10000]')).toBe(true)
    expect(dialog?.getAttribute('aria-modal')).toBe('true')
    expect(dialog?.getAttribute('aria-busy')).toBe('false')
    expect(dialog?.getAttribute('tabindex')).toBe('-1')

    const titleId = dialog?.getAttribute('aria-labelledby')
    const descriptionIds = dialog?.getAttribute('aria-describedby')?.split(' ') ?? []
    expect(titleId).toBeTruthy()
    expect(document.getElementById(titleId!)?.textContent).toContain('درخواست ورود جدید')
    expect(descriptionIds).toHaveLength(2)
    expect(document.getElementById(descriptionIds[0]!)?.textContent).toContain('اجازه ورود')
    expect(document.getElementById(descriptionIds[1]!)?.textContent).toContain(
      'بدون انتخاب بسته نمی‌شود',
    )
    expect(dialog?.querySelector('h2')?.classList.contains('text-white')).toBe(true)
    expect(dialog?.querySelector('svg')?.classList.contains('ui-v2-session-icon')).toBe(true)

    wrapper.unmount()
    expect(isSecurityLayerActive.value).toBe(false)
  })

  it('uses tokenized opacity motion in V2 without executing the legacy scale animation', async () => {
    const wrapper = mount(SessionApprovalModal, {
      attachTo: document.body,
      props: { v2Portal: true },
    })

    sessionModalMocks.showModal!.value = true
    await flushDialogOpen()

    const layer = document.body.querySelector<HTMLElement>('.ui-v2-session-layer')
    const card = document.body.querySelector<HTMLElement>('.ui-v2-session-card')
    expect(layer?.getAttribute('data-ui-system')).toBe('v2-portal')
    expect(layer?.getAttribute('data-ui-v2-motion')).toBe('essential')
    expect(layer?.getAttribute('style')).toContain('transition-duration: var(--ui-v2-motion-state)')
    expect(card?.classList.contains('animate-scale-in')).toBe(false)

    wrapper.unmount()
  })

  it('keeps the legacy scale animation only on the unscoped protected branch', async () => {
    const wrapper = mount(SessionApprovalModal, { attachTo: document.body })
    sessionModalMocks.showModal!.value = true
    await flushDialogOpen()

    const layer = document.body.querySelector<HTMLElement>('.backdrop-blur-sm')
    const card = document.body.querySelector<HTMLElement>('.animate-scale-in')
    expect(card?.classList.contains('animate-scale-in')).toBe(true)
    expect(layer?.className).not.toContain('ui-v2-session')
    expect(card?.className).not.toContain('ui-v2-session')
    expect(card?.hasAttribute('role')).toBe(false)
    expect(card?.hasAttribute('aria-modal')).toBe(false)
    expect(card?.hasAttribute('tabindex')).toBe(false)
    expect(layer?.classList.contains('z-[9999]')).toBe(true)
    expect(layer?.hasAttribute('data-ui-v2-motion')).toBe(false)
    expect(isSecurityLayerActive.value).toBe(false)
    expect(document.body.textContent).not.toContain('بدون انتخاب بسته نمی‌شود')
    expect(card?.querySelector('h3')?.classList.contains('text-white')).toBe(true)
    expect(card?.querySelector('svg')?.classList.contains('ui-v2-session-icon')).toBe(false)

    wrapper.unmount()
  })

  it('traps keyboard and programmatic focus, then restores the opening control', async () => {
    const openingControl = document.createElement('button')
    openingControl.textContent = 'باز کردن'
    document.body.append(openingControl)
    openingControl.focus()

    const wrapper = mount(SessionApprovalModal, {
      attachTo: document.body,
      props: { v2Portal: true },
    })
    sessionModalMocks.showModal!.value = true
    await flushDialogOpen()

    const dialog = getDialog()!
    const actions = Array.from(dialog.querySelectorAll<HTMLButtonElement>('button'))
    expect(document.activeElement).toBe(dialog)
    expect(actions).toHaveLength(2)

    actions[0]!.focus()
    actions[0]!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }),
    )
    expect(document.activeElement).toBe(actions[actions.length - 1])

    actions[actions.length - 1]!.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }),
    )
    expect(document.activeElement).toBe(actions[0])

    const outsideControl = document.createElement('button')
    document.body.append(outsideControl)
    outsideControl.focus()
    expect(document.activeElement).toBe(dialog)

    sessionModalMocks.showModal!.value = false
    await nextTick()
    expect(document.activeElement).toBe(openingControl)

    wrapper.unmount()
  })

  it('prevents Escape without dismissing or choosing on behalf of the user', async () => {
    const wrapper = mount(SessionApprovalModal, {
      attachTo: document.body,
      props: { v2Portal: true },
    })
    sessionModalMocks.showModal!.value = true
    await flushDialogOpen()

    const action = getDialog()!.querySelector<HTMLButtonElement>('button')!
    action.focus()
    const escapeEvent = new KeyboardEvent('keydown', {
      key: 'Escape',
      bubbles: true,
      cancelable: true,
    })
    action.dispatchEvent(escapeEvent)

    expect(escapeEvent.defaultPrevented).toBe(true)
    expect(sessionModalMocks.showModal!.value).toBe(true)
    expect(sessionModalMocks.approve).not.toHaveBeenCalled()
    expect(sessionModalMocks.reject).not.toHaveBeenCalled()
    expect(document.activeElement).toBe(action)

    wrapper.unmount()
  })
})
