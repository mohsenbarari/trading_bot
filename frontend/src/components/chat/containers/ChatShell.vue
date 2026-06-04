<script setup lang="ts">
defineProps<{
  className?: string
}>()
</script>

<template>
  <div :class="className || 'chat-view'">
    <slot />
  </div>
</template>

