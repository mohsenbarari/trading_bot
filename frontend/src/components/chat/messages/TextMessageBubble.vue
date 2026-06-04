<script setup lang="ts">
defineProps<{
  html: string
}>()

defineEmits<{
  (event: 'content-click', payload: MouseEvent): void
}>()
</script>

<template>
  <p class="text-message-bubble" v-html="html" @click="$emit('content-click', $event)" />
</template>

<style scoped>
.text-message-bubble {
  margin: 0;
}
</style>
