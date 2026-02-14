<template>
  <div class="skeleton-loader">
    <div 
      v-for="i in count" 
      :key="i" 
      class="skeleton-item"
      :style="{ height: height + 'px', marginBottom: gap + 'px' }"
    ></div>
  </div>
</template>

<script setup lang="ts">
defineProps({
  count: {
    type: Number,
    default: 3
  },
  height: {
    type: Number,
    default: 80
  },
  gap: {
    type: Number,
    default: 12
  }
});
</script>

<style scoped>
.skeleton-loader {
  width: 100%;
}

.skeleton-item {
  width: 100%;
  background: linear-gradient(90deg, #fefce8 25%, #fef3c7 50%, #fefce8 75%);
  background-size: 200% 100%;
  border-radius: 1rem;
  border: 1px solid rgba(245, 158, 11, 0.06);
  animation: loading 1.5s infinite;
}

@keyframes loading {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
</style>
