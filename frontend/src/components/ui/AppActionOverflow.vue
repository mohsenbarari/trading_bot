<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
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

function close() {
  open.value = false
}

function toggle() {
  open.value = !open.value
}

function selectAction(action: AppActionOverflowItem) {
  if (action.disabled) return
  emit('select', action.id)
  close()
}

function onDocumentPointerDown(event: PointerEvent) {
  const target = event.target
  if (!(target instanceof Node)) return
  if (rootRef.value?.contains(target)) return
  close()
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}

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
        :label="moreLabel"
        :aria-expanded="open"
        aria-haspopup="menu"
        @click="toggle"
      >
        <MoreHorizontal :size="20" aria-hidden="true" />
      </AppIconButton>
      <div
        v-if="open"
        class="ui-action-overflow__panel"
        role="menu"
        :aria-label="moreLabel"
      >
        <button
          v-for="action in actions"
          :key="action.id"
          type="button"
          class="ui-action-overflow__item"
          :class="{ 'ui-action-overflow__item--danger': action.tone === 'danger' }"
          role="menuitem"
          :disabled="action.disabled"
          @click="selectAction(action)"
        >
          {{ action.label }}
        </button>
      </div>
    </div>
  </div>
</template>
