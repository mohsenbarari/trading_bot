<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import AppButton from './AppButton.vue'
import { useOverlayA11y } from './useOverlayA11y'

type ClassValue = string | string[] | Record<string, boolean>

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  description?: string
  closeLabel?: string
  showClose?: boolean
  closeOnBackdrop?: boolean
  closeOnEscape?: boolean
  backdropClass?: ClassValue
  panelClass?: ClassValue
  bodyClass?: ClassValue
  actionsClass?: ClassValue
}>(), {
  description: '',
  closeLabel: 'بستن',
  showClose: true,
  closeOnBackdrop: true,
  closeOnEscape: true,
  backdropClass: '',
  panelClass: '',
  bodyClass: '',
  actionsClass: '',
})

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
})

function handleBackdropClick() {
  if (props.closeOnBackdrop) {
    emit('close')
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="ui-responsive-dialog-backdrop"
      :class="backdropClass"
      @click.self="handleBackdropClick"
    >
      <section
        ref="containerRef"
        class="ui-responsive-dialog"
        :class="panelClass"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="ariaDescriptionId"
        tabindex="-1"
      >
        <header class="ui-responsive-dialog__header">
          <div>
            <h2 :id="titleId">{{ title }}</h2>
            <p v-if="description" :id="descriptionId">{{ description }}</p>
          </div>
          <AppButton v-if="showClose" variant="ghost" size="sm" @click="$emit('close')">{{ closeLabel }}</AppButton>
        </header>
        <div class="ui-responsive-dialog__body" :class="bodyClass">
          <slot />
        </div>
        <footer v-if="$slots.actions" class="ui-responsive-dialog__actions" :class="actionsClass">
          <slot name="actions" />
        </footer>
      </section>
    </div>
  </Teleport>
</template>
