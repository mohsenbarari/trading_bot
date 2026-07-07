<script setup lang="ts">
import { computed, useId } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  description?: string
  open?: boolean
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'
  titleId?: string
  panelId?: string
  toggleClass?: string
  panelClass?: string
}>(), {
  open: false,
  tone: 'neutral',
  titleId: '',
  panelId: '',
  toggleClass: '',
  panelClass: '',
})

defineEmits<{
  toggle: []
}>()

const generatedId = useId()
const resolvedTitleId = computed(() => props.titleId || `${generatedId}-title`)
const resolvedPanelId = computed(() => props.panelId || `${generatedId}-panel`)
</script>

<template>
  <section
    class="ui-disclosure"
    :class="[`ui-disclosure--${tone}`, { 'is-open': open }]"
    :aria-labelledby="resolvedTitleId"
  >
    <button
      type="button"
      class="ui-disclosure__toggle"
      :class="toggleClass"
      :aria-expanded="open"
      :aria-controls="resolvedPanelId"
      @click="$emit('toggle')"
    >
      <span v-if="$slots.leading" class="ui-disclosure__leading" aria-hidden="true">
        <slot name="leading" />
      </span>
      <span class="ui-disclosure__copy">
        <strong :id="resolvedTitleId">{{ title }}</strong>
        <span v-if="description">{{ description }}</span>
      </span>
      <span v-if="$slots.meta" class="ui-disclosure__meta">
        <slot name="meta" />
      </span>
    </button>

    <div
      v-if="open"
      :id="resolvedPanelId"
      class="ui-disclosure__panel"
      :class="panelClass"
    >
      <slot />
    </div>
  </section>
</template>
