<script setup lang="ts">
withDefaults(defineProps<{
  side: 'buy' | 'sell'
  pending?: boolean
  busy?: boolean
  disabled?: boolean
  type?: 'button' | 'submit' | 'reset'
}>(), {
  pending: false,
  busy: false,
  disabled: false,
  type: 'button',
})
</script>

<template>
  <button
    :type="type"
    class="trade-btn"
    data-test="trade-action-button"
    :data-state="pending ? 'pending' : 'idle'"
    :class="[pending ? 'pending' : side, busy ? 'busy' : '']"
    :disabled="disabled || busy"
  >
    <slot />
  </button>
</template>
