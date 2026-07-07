import { computed, nextTick, onBeforeUnmount, ref, useId, watch, type Ref } from 'vue'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

type OverlayA11yOptions = {
  open: Ref<boolean>
  description: Ref<string | undefined>
  containerRef: Ref<HTMLElement | null>
  close: () => void
  closeOnEscape?: Ref<boolean>
}

export function useOverlayA11y(options: OverlayA11yOptions) {
  const titleId = useId()
  const descriptionId = useId()
  const previousActiveElement = ref<HTMLElement | null>(null)
  const ownsScrollLock = ref(false)

  const ariaDescriptionId = computed(() => (options.description.value ? descriptionId : undefined))

  function getFocusableElements() {
    const container = options.containerRef.value
    if (!container) return []
    return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
      .filter((element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true')
  }

  function focusInitialTarget() {
    const focusableElements = getFocusableElements()
    if (focusableElements.length > 0) {
      focusableElements[0]!.focus()
      return
    }

    options.containerRef.value?.focus()
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

    const focusableElements = getFocusableElements()
    if (focusableElements.length === 0) {
      event.preventDefault()
      options.containerRef.value?.focus()
      return
    }

    const firstElement = focusableElements[0]!
    const lastElement = focusableElements[focusableElements.length - 1]!
    const activeElement = document.activeElement as HTMLElement | null

    if (event.shiftKey) {
      if (!activeElement || activeElement === firstElement || !options.containerRef.value?.contains(activeElement)) {
        event.preventDefault()
        lastElement.focus()
      }
      return
    }

    if (!activeElement || activeElement === lastElement || !options.containerRef.value?.contains(activeElement)) {
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

  watch(
    options.open,
    async (isOpen) => {
      if (isOpen) {
        previousActiveElement.value = document.activeElement instanceof HTMLElement ? document.activeElement : null
        lockScroll()
        document.addEventListener('keydown', handleKeydown)
        await nextTick()
        focusInitialTarget()
        return
      }

      document.removeEventListener('keydown', handleKeydown)
      unlockScroll()
      await nextTick()
      restoreFocus()
    },
    { flush: 'post', immediate: true },
  )

  onBeforeUnmount(() => {
    document.removeEventListener('keydown', handleKeydown)
    unlockScroll()
    restoreFocus()
  })

  return {
    titleId,
    descriptionId,
    ariaDescriptionId,
  }
}
