<script setup lang="ts">
withDefaults(defineProps<{
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <span class="ui-chip" :class="`ui-chip--${tone}`">
    <slot />
  </span>
</template>
