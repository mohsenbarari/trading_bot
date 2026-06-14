<script setup lang="ts">
withDefaults(defineProps<{
  label: string
  value: string | number
  hint?: string
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <article class="ds-stat-tile" :class="`ds-stat-tile--${tone}`">
    <span class="ds-stat-label">{{ label }}</span>
    <strong class="ds-stat-value">{{ value }}</strong>
    <span v-if="hint" class="ds-stat-hint">{{ hint }}</span>
  </article>
</template>
