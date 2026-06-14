<script setup lang="ts">
withDefaults(defineProps<{
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'
  padded?: boolean
}>(), {
  tone: 'neutral',
  padded: true,
})
</script>

<template>
  <article class="ui-card" :class="[`ui-card--${tone}`, { 'ui-card--padded': padded }]">
    <slot />
  </article>
</template>
