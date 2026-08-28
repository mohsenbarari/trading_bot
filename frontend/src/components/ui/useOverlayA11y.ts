import { computed, nextTick, onBeforeUnmount, ref, useId, watch, type Ref } from 'vue'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

type OverlayInitialFocus = 'first' | 'container'

type OverlayA11yOptions = {
  open: Ref<boolean>
  description: Ref<string | undefined>
  containerRef: Ref<HTMLElement | null>
  close: () => void
  closeOnEscape?: Ref<boolean>
  initialFocus?: Ref<OverlayInitialFocus>
  initialFocusRef?: Ref<HTMLElement | null>
  trapProgrammaticFocus?: Ref<boolean>
}

export function useOverlayA11y(options: OverlayA11yOptions) {
  const titleId = useId()
  const descriptionId = useId()
  const previousActiveElement = ref<HTMLElement | null>(null)
  const ownsScrollLock = ref(false)
  let disposed = false

  const ariaDescriptionId = computed(() => (options.description.value ? descriptionId : undefined))

  function getFocusableElements() {
    const container = options.containerRef.value
    if (!container) return []
    return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
      .filter((element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true')
  }

  function focusInitialTarget() {
    const preferred = options.initialFocusRef?.value
    if (preferred && preferred.isConnected && !preferred.hasAttribute('disabled')) {
      preferred.focus({ preventScroll: true })
      return
    }

    const container = options.containerRef.value
    if (options.initialFocus?.value === 'container') {
      container?.focus({ preventScroll: true })
      return
    }

    const focusableElements = getFocusableElements()
    if (focusableElements.length > 0) {
      focusableElements[0]!.focus()
      return
    }

    container?.focus({ preventScroll: true })
  }

  function keepProgrammaticFocusInside(event: FocusEvent) {
    if (!options.trapProgrammaticFocus?.value || !options.open.value || !options.containerRef.value) {
      return
    }
    if (event.target instanceof Node && !options.containerRef.value.contains(event.target)) {
      options.containerRef.value.focus({ preventScroll: true })
    }
  }

  function handleKeydown(event: KeyboardEvent) {
    if (!options.open.value) return

    if (event.key === 'Escape') {
      event.preventDefault()
      if (options.closeOnEscape?.value ?? true) {
        options.close()
      }
      return
    }

    if (event.key !== 'Tab') return

    const container = options.containerRef.value
    const focusableElements = getFocusableElements()
    if (focusableElements.length === 0) {
      event.preventDefault()
      container?.focus({ preventScroll: true })
      return
    }

    const firstElement = focusableElements[0]!
    const lastElement = focusableElements[focusableElements.length - 1]!
    const activeElement = document.activeElement as HTMLElement | null

    if (event.shiftKey) {
      if (
        !activeElement ||
        activeElement === firstElement ||
        activeElement === container ||
        !container?.contains(activeElement)
      ) {
        event.preventDefault()
        lastElement.focus()
      }
      return
    }

    if (!activeElement || activeElement === lastElement || !container?.contains(activeElement)) {
      event.preventDefault()
      firstElement.focus()
    }
  }

  function lockScroll() {
    const currentCount = Number.parseInt(document.body.dataset.uiOverlayLockCount || '0', 10) || 0
    document.body.dataset.uiOverlayLockCount = String(currentCount + 1)
    document.documentElement.classList.add('ui-overlay-open')
    document.body.classList.add('ui-overlay-open')
    ownsScrollLock.value = true
  }

  function unlockScroll() {
    if (!ownsScrollLock.value) return

    const currentCount = Number.parseInt(document.body.dataset.uiOverlayLockCount || '0', 10) || 0
    const nextCount = Math.max(0, currentCount - 1)

    if (nextCount === 0) {
      delete document.body.dataset.uiOverlayLockCount
      document.documentElement.classList.remove('ui-overlay-open')
      document.body.classList.remove('ui-overlay-open')
    } else {
      document.body.dataset.uiOverlayLockCount = String(nextCount)
    }

    ownsScrollLock.value = false
  }

  function restoreFocus() {
    const target = previousActiveElement.value
    if (target && target.isConnected) {
      target.focus()
    }
    previousActiveElement.value = null
  }

  function attachListeners() {
    document.addEventListener('keydown', handleKeydown)
    document.addEventListener('focusin', keepProgrammaticFocusInside, true)
  }

  function detachListeners() {
    document.removeEventListener('keydown', handleKeydown)
    document.removeEventListener('focusin', keepProgrammaticFocusInside, true)
  }

  watch(
    options.open,
    async (isOpen) => {
      if (isOpen) {
        previousActiveElement.value =
          document.activeElement instanceof HTMLElement ? document.activeElement : null
        lockScroll()
        attachListeners()
        await nextTick()
        if (!disposed && options.open.value) focusInitialTarget()
        return
      }

      detachListeners()
      unlockScroll()
      restoreFocus()
    },
    { flush: 'post', immediate: true },
  )

  onBeforeUnmount(() => {
    disposed = true
    detachListeners()
    unlockScroll()
    restoreFocus()
  })

  return {
    titleId,
    descriptionId,
    ariaDescriptionId,
  }
}
