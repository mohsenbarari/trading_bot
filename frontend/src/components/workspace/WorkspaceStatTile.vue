<script setup lang="ts">
import AppMetricCard from '../ui/AppMetricCard.vue'

type WorkspaceTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

withDefaults(defineProps<{
  label: string
  value: string | number
  hint?: string
  tone?: WorkspaceTone
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <AppMetricCard
    class="ds-stat-tile"
    :class="`ds-stat-tile--${tone}`"
    :label="label"
    :value="value"
    :hint="hint"
    :tone="tone"
  />
</template>
