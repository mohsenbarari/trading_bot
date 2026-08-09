<script setup lang="ts">
import { computed } from 'vue'
import AppDangerZone from '../ui/AppDangerZone.vue'
import { getUiDesignSystemScopeAttributes } from '../ui/uiDesignSystemScope'

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    v2Scope?: boolean
  }>(),
  {
    v2Scope: false,
  },
)

const scopeAttributes = computed(() => (props.v2Scope ? getUiDesignSystemScopeAttributes() : {}))
</script>

<template>
  <AppDangerZone
    v-bind="scopeAttributes"
    class="ds-danger-zone"
    :class="{
      'ui-v2-scope': v2Scope,
      'ui-v2-workspace-danger-adapter': v2Scope,
    }"
    :title="title"
    :description="description"
  >
    <slot />
  </AppDangerZone>
</template>
