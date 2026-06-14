<script setup lang="ts">
withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  block?: boolean
  loading?: boolean
  disabled?: boolean
  type?: 'button' | 'submit' | 'reset'
}>(), {
  variant: 'primary',
  size: 'md',
  block: false,
  loading: false,
  disabled: false,
  type: 'button',
})
</script>

<template>
  <button
    :type="type"
    class="ui-button"
    :class="[`ui-button--${variant}`, `ui-button--${size}`, { 'ui-button--block': block, 'is-loading': loading }]"
    :disabled="disabled || loading"
  >
    <span v-if="$slots.icon && !loading" class="ui-button__icon" aria-hidden="true">
      <slot name="icon" />
    </span>
    <span v-if="loading" class="ui-button__spinner" aria-hidden="true"></span>
    <span class="ui-button__label">
      <slot />
    </span>
  </button>
</template>
