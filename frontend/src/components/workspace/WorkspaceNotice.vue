<script setup lang="ts">
import { computed } from 'vue'
import AppToast from '../ui/AppToast.vue'
import { getUiDesignSystemScopeAttributes } from '../ui/uiDesignSystemScope'

const props = withDefaults(defineProps<{
  title?: string
  message?: string
  tone?: 'info' | 'success' | 'warning' | 'danger'
  role?: 'status' | 'alert' | 'note'
  v2Scope?: boolean
}>(), {
  tone: 'info',
  role: 'status',
  v2Scope: false,
})

const scopeAttributes = computed(() => (
  props.v2Scope ? getUiDesignSystemScopeAttributes() : {}
))
</script>

<template>
  <AppToast
    v-bind="scopeAttributes"
    class="ds-workspace-notice"
    :class="[
      `ds-workspace-notice--${tone}`,
      {
        'ui-v2-scope': v2Scope,
        'ui-v2-workspace-notice-adapter': v2Scope,
      },
    ]"
    :title="title"
    :message="message"
    :tone="tone"
    :role="props.role"
  >
    <template v-if="$slots.icon" #icon>
      <span class="ds-workspace-notice-icon">
        <slot name="icon" />
      </span>
    </template>
    <div class="ds-workspace-notice-copy">
      <slot />
    </div>
  </AppToast>
</template>
