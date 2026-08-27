<script setup lang="ts">
import { computed, ref, toRef, type CSSProperties } from 'vue'
import AppButton from './AppButton.vue'
import { useOverlayA11y } from './useOverlayA11y'

type ClassValue = string | string[] | Record<string, boolean>
type OverlayInitialFocus = 'first' | 'container'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    closeLabel?: string
    showClose?: boolean
    closeOnBackdrop?: boolean
    closeOnEscape?: boolean
    busy?: boolean
    initialFocus?: OverlayInitialFocus
    trapProgrammaticFocus?: boolean
    describedByExtra?: string
    titleClass?: ClassValue
    headerClass?: ClassValue
    teleportTo?: string | HTMLElement
    backdropClass?: ClassValue
    backdropStyle?: CSSProperties
    backdropAttrs?: Record<string, string | undefined>
    panelClass?: ClassValue
    bodyClass?: ClassValue
    actionsClass?: ClassValue
  }>(),
  {
    description: '',
    closeLabel: 'بستن',
    showClose: true,
    closeOnBackdrop: true,
    closeOnEscape: true,
    busy: false,
    initialFocus: 'first',
    trapProgrammaticFocus: false,
    describedByExtra: '',
    titleClass: '',
    headerClass: '',
    teleportTo: 'body',
    backdropClass: '',
    backdropStyle: undefined,
    backdropAttrs: () => ({}),
    panelClass: '',
    bodyClass: '',
    actionsClass: '',
  },
)

const emit = defineEmits<{
  close: []
}>()

const containerRef = ref<HTMLElement | null>(null)

const { titleId, descriptionId, ariaDescriptionId } = useOverlayA11y({
  open: toRef(props, 'open'),
  description: computed(() => props.description || undefined),
  containerRef,
  close: () => emit('close'),
  closeOnEscape: toRef(props, 'closeOnEscape'),
  initialFocus: toRef(props, 'initialFocus'),
  trapProgrammaticFocus: toRef(props, 'trapProgrammaticFocus'),
})

const describedByValue = computed(() => {
  const ids = [ariaDescriptionId.value, props.describedByExtra].filter((id): id is string => Boolean(id))
  return ids.length ? ids.join(' ') : undefined
})

function handleBackdropClick() {
  if (props.closeOnBackdrop) {
    emit('close')
  }
}
</script>

<template>
  <Teleport :to="teleportTo" defer>
    <div
      v-if="open"
      class="ui-sheet-backdrop"
      :class="backdropClass"
      :style="backdropStyle"
      v-bind="backdropAttrs"
      @click.self="handleBackdropClick"
    >
      <section
        ref="containerRef"
        class="ui-bottom-sheet"
        :class="panelClass"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="describedByValue"
        :aria-busy="busy ? 'true' : 'false'"
        tabindex="-1"
      >
        <header class="ui-bottom-sheet__header" :class="headerClass">
          <div>
            <h2 :id="titleId" :class="titleClass">{{ title }}</h2>
            <p v-if="description" :id="descriptionId">{{ description }}</p>
          </div>
          <AppButton v-if="showClose" variant="ghost" size="sm" @click="$emit('close')">{{
            closeLabel
          }}</AppButton>
        </header>
        <div class="ui-bottom-sheet__body" :class="bodyClass">
          <slot />
        </div>
        <footer v-if="$slots.actions" class="ui-bottom-sheet__actions" :class="actionsClass">
          <slot name="actions" />
        </footer>
      </section>
    </div>
  </Teleport>
</template>
