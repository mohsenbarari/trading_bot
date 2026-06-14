<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description?: string
  badge?: string
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'
  active?: boolean
  disabled?: boolean
}>(), {
  tone: 'neutral',
  active: false,
  disabled: false,
})

const emit = defineEmits<{
  select: []
}>()

function handleClick() {
  emit('select')
}
</script>

<template>
  <button
    type="button"
    class="ui-action-card"
    :class="[`ui-action-card--${tone}`, { 'is-active': active }]"
    :disabled="disabled"
    @click="handleClick"
  >
    <span v-if="$slots.icon" class="ui-action-card__icon" aria-hidden="true">
      <slot name="icon" />
    </span>
    <span class="ui-action-card__copy">
      <span class="ui-action-card__title-row">
        <strong>{{ title }}</strong>
        <span v-if="badge" class="ui-action-card__badge">{{ badge }}</span>
      </span>
      <span v-if="description" class="ui-action-card__description">{{ description }}</span>
    </span>
    <span class="ui-action-card__arrow" aria-hidden="true">‹</span>
  </button>
</template>
