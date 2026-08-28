<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { MoreHorizontal } from 'lucide-vue-next'
import AppIconButton from './AppIconButton.vue'

export type AppActionOverflowItem = {
  id: string
  label: string
  tone?: 'neutral' | 'danger'
  disabled?: boolean
}

const props = withDefaults(
  defineProps<{
    actions: AppActionOverflowItem[]
    moreLabel?: string
  }>(),
  {
    moreLabel: 'بیشتر',
  },
)

const emit = defineEmits<{
  select: [id: string]
}>()

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const triggerRef = ref<{ $el?: HTMLElement } | null>(null)
const activeIndex = ref(0)
const menuId = useId()

function triggerElement() {
  const el = triggerRef.value?.$el
  return el instanceof HTMLElement ? el : null
}

function enabledIndexes() {
  return props.actions
    .map((action, index) => (action.disabled ? -1 : index))
    .filter((index) => index >= 0)
}

function itemButtons() {
  return Array.from(panelRef.value?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])
}

function focusItem(index: number) {
  activeIndex.value = index
  void nextTick(() => {
    itemButtons()[index]?.focus()
  })
}

function close(options?: { restore?: boolean }) {
  if (!open.value) return
  open.value = false
  if (options?.restore !== false) {
    void nextTick(() => triggerElement()?.focus())
  }
}

function openMenu(focus: 'first' | 'last' = 'first') {
  if (!props.actions.length) return
  open.value = true
  const enabled = enabledIndexes()
  const target = focus === 'last'
    ? (enabled[enabled.length - 1] ?? 0)
    : (enabled[0] ?? 0)
  void nextTick(() => focusItem(target))
}

function toggle() {
  if (open.value) {
    close({ restore: true })
    return
  }
  openMenu('first')
}

function selectAction(action: AppActionOverflowItem) {
  if (action.disabled) return
  emit('select', action.id)
  close({ restore: true })
}

function moveActive(delta: number) {
  const enabled = enabledIndexes()
  if (!enabled.length) return
  const currentSlot = enabled.indexOf(activeIndex.value)
  const from = currentSlot === -1 ? (delta > 0 ? -1 : 0) : currentSlot
  const next = enabled[(from + delta + enabled.length) % enabled.length]
  if (next !== undefined) focusItem(next)
}

function onDocumentPointerDown(event: PointerEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  if (rootRef.value?.contains(target)) return
  close({ restore: false })
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (!open.value) {
    if (event.key === 'ArrowDown' && document.activeElement === triggerElement()) {
      event.preventDefault()
      openMenu('first')
    }
    if (event.key === 'ArrowUp' && document.activeElement === triggerElement()) {
      event.preventDefault()
      openMenu('last')
    }
    return
  }

  if (event.key === 'Escape') {
    event.preventDefault()
    close({ restore: true })
    return
  }

  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveActive(1)
    return
  }

  if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(-1)
    return
  }

  if (event.key === 'Home') {
    event.preventDefault()
    const first = enabledIndexes()[0]
    if (first !== undefined) focusItem(first)
    return
  }

  if (event.key === 'End') {
    event.preventDefault()
    const enabled = enabledIndexes()
    const last = enabled[enabled.length - 1]
    if (last !== undefined) focusItem(last)
    return
  }

  if (event.key === 'Tab') {
    event.preventDefault()
    close({ restore: true })
  }
}

watch(
  () => props.actions,
  () => {
    if (open.value && !props.actions.length) close({ restore: true })
  },
)

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown)
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <div ref="rootRef" class="ui-action-overflow">
    <div class="ui-action-overflow__primary">
      <slot />
    </div>
    <div v-if="actions.length" class="ui-action-overflow__menu">
      <AppIconButton
        ref="triggerRef"
        :label="moreLabel"
        :aria-expanded="open"
        aria-haspopup="menu"
        :aria-controls="menuId"
        @click="toggle"
      >
        <MoreHorizontal :size="20" aria-hidden="true" />
      </AppIconButton>
      <div
        v-if="open"
        :id="menuId"
        ref="panelRef"
        class="ui-action-overflow__panel"
        role="menu"
        :aria-label="moreLabel"
      >
        <button
          v-for="(action, index) in actions"
          :key="action.id"
          type="button"
          class="ui-action-overflow__item"
          :class="{ 'ui-action-overflow__item--danger': action.tone === 'danger' }"
          role="menuitem"
          :tabindex="index === activeIndex ? 0 : -1"
          :disabled="action.disabled"
          @click="selectAction(action)"
        >
          {{ action.label }}
        </button>
      </div>
    </div>
  </div>
</template>
