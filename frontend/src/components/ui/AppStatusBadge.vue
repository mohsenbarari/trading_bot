<script setup lang="ts">
withDefaults(defineProps<{
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <span class="ui-status-badge" :class="`ui-status-badge--${tone}`">
    <slot />
  </span>
</template>
