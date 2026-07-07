<script setup lang="ts">
withDefaults(defineProps<{
  count?: number
}>(), {
  count: 5,
})
</script>

<template>
  <div class="offers-list" data-test="offers-loading-skeleton">
    <div v-for="i in count" :key="i" class="skeleton-card"></div>
  </div>
</template>

<style scoped>
.offers-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.skeleton-card {
  height: 78px;
  background: rgba(255, 255, 255, 0.5);
  border-radius: var(--ds-radius-md);
  border: 1px solid var(--ds-border-accent);
  animation: pulse-skeleton 1.5s ease-in-out infinite;
}

@keyframes pulse-skeleton {
  0%,
  100% {
    opacity: 0.6;
  }

  50% {
    opacity: 1;
  }
}
</style>
