<script setup lang="ts">
withDefaults(defineProps<{
  label: string
  variant?: 'neutral' | 'primary' | 'danger'
  size?: 'sm' | 'md'
  disabled?: boolean
  type?: 'button' | 'submit' | 'reset'
}>(), {
  variant: 'neutral',
  size: 'md',
  disabled: false,
  type: 'button',
})
</script>

<template>
  <button
    :type="type"
    class="ui-icon-button"
    :class="[`ui-icon-button--${variant}`, `ui-icon-button--${size}`]"
    :aria-label="label"
    :disabled="disabled"
  >
    <slot />
  </button>
</template>
