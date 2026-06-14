<script setup lang="ts">
withDefaults(defineProps<{
  label: string
  value: string | number
  hint?: string
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <article class="ui-metric-card" :class="`ui-metric-card--${tone}`">
    <span class="ui-metric-card__label">{{ label }}</span>
    <strong class="ui-metric-card__value">{{ value }}</strong>
    <span v-if="hint" class="ui-metric-card__hint">{{ hint }}</span>
  </article>
</template>
