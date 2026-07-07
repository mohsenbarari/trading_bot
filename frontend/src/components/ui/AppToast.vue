<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  title?: string
  message?: string
  tone?: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  role?: 'status' | 'alert' | 'note'
  ariaLive?: 'polite' | 'assertive' | 'off'
}>(), {
  title: '',
  message: '',
  tone: 'neutral',
  role: 'status',
  ariaLive: undefined,
})

const resolvedAriaLive = computed(() => {
  if (props.ariaLive) return props.ariaLive
  if (props.role === 'alert') return 'assertive'
  if (props.role === 'note') return 'off'
  return 'polite'
})
</script>

<template>
  <div
    class="ui-toast"
    :class="`ui-toast--${tone}`"
    :role="role"
    :aria-live="resolvedAriaLive"
  >
    <div v-if="$slots.icon" class="ui-toast__icon" aria-hidden="true">
      <slot name="icon" />
    </div>
    <div class="ui-toast__copy">
      <strong v-if="title">{{ title }}</strong>
      <span v-if="message">{{ message }}</span>
      <slot />
    </div>
  </div>
</template>
