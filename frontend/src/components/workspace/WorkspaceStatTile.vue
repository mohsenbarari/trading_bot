<script setup lang="ts">
import { computed } from 'vue'
import AppMetricCard from '../ui/AppMetricCard.vue'
import { getUiDesignSystemScopeAttributes } from '../ui/uiDesignSystemScope'

type WorkspaceTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const props = withDefaults(defineProps<{
  label: string
  value: string | number
  hint?: string
  tone?: WorkspaceTone
  v2Scope?: boolean
}>(), {
  tone: 'neutral',
  v2Scope: false,
})

const scopeAttributes = computed(() => (
  props.v2Scope ? getUiDesignSystemScopeAttributes() : {}
))
</script>

<template>
  <AppMetricCard
    v-bind="scopeAttributes"
    class="ds-stat-tile"
    :class="[
      `ds-stat-tile--${tone}`,
      {
        'ui-v2-scope': v2Scope,
        'ui-v2-workspace-stat-adapter': v2Scope,
      },
    ]"
    :label="label"
    :value="value"
    :hint="hint"
    :tone="tone"
  />
</template>
