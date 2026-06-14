<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description?: string
  badge?: string
  disabled?: boolean
  active?: boolean
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
}>(), {
  disabled: false,
  active: false,
  tone: 'neutral',
})

defineEmits<{
  select: []
}>()
</script>

<template>
  <button
    type="button"
    class="ds-action-tile"
    :class="[`ds-action-tile--${tone}`, { 'is-active': active }]"
    :disabled="disabled"
    @click="$emit('select')"
  >
    <span v-if="$slots.icon" class="ds-action-tile-icon" aria-hidden="true">
      <slot name="icon" />
    </span>
    <span class="ds-action-tile-copy">
      <span class="ds-action-tile-title-row">
        <strong>{{ title }}</strong>
        <span v-if="badge" class="ds-action-tile-badge">{{ badge }}</span>
      </span>
      <span v-if="description" class="ds-action-tile-description">{{ description }}</span>
    </span>
    <span class="ds-action-tile-arrow" aria-hidden="true">‹</span>
  </button>
</template>
