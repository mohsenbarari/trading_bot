<script setup lang="ts">
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
  <div class="ds-workspace-notice" :class="`ds-workspace-notice--${tone}`" :role="props.role">
    <div v-if="$slots.icon" class="ds-workspace-notice-icon" aria-hidden="true">
      <slot name="icon" />
    </div>
    <div class="ds-workspace-notice-copy">
      <strong v-if="title">{{ title }}</strong>
      <p v-if="message">{{ message }}</p>
      <slot />
    </div>
  </div>
</template>
