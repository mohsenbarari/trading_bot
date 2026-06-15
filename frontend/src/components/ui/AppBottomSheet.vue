<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import AppButton from './AppButton.vue'
import { useOverlayA11y } from './useOverlayA11y'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  description?: string
  closeLabel?: string
}>(), {
  description: '',
  closeLabel: 'بستن',
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
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="ui-sheet-backdrop" @click.self="$emit('close')">
      <section
        ref="containerRef"
        class="ui-bottom-sheet"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="titleId"
        :aria-describedby="ariaDescriptionId"
        tabindex="-1"
      >
        <header class="ui-bottom-sheet__header">
          <div>
            <h2 :id="titleId">{{ title }}</h2>
            <p v-if="description" :id="descriptionId">{{ description }}</p>
          </div>
          <AppButton variant="ghost" size="sm" @click="$emit('close')">{{ closeLabel }}</AppButton>
        </header>
        <div class="ui-bottom-sheet__body">
          <slot />
        </div>
        <footer v-if="$slots.actions" class="ui-bottom-sheet__actions">
          <slot name="actions" />
        </footer>
      </section>
    </div>
  </Teleport>
</template>
