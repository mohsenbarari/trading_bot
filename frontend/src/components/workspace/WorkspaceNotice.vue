<script setup lang="ts">
import AppToast from '../ui/AppToast.vue'

const props = withDefaults(defineProps<{
  title?: string
  message?: string
  tone?: 'info' | 'success' | 'warning' | 'danger'
  role?: 'status' | 'alert' | 'note'
}>(), {
  tone: 'info',
  role: 'status',
})
</script>

<template>
  <AppToast
    class="ds-workspace-notice"
    :class="`ds-workspace-notice--${tone}`"
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
